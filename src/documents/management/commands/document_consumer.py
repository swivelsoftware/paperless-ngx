import logging
import os
from fnmatch import filter
from pathlib import Path
from pathlib import PurePath
from threading import Event
from time import monotonic
from time import sleep
from typing import Final

from django import db
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from watchfiles import Change
from watchfiles import DefaultFilter
from watchfiles import watch

from documents.data_models import ConsumableDocument
from documents.data_models import DocumentMetadataOverrides
from documents.data_models import DocumentSource
from documents.models import Tag
from documents.parsers import is_file_ext_supported
from documents.tasks import consume_file

try:
    from inotifyrecursive import INotify
    from inotifyrecursive import flags
except ImportError:  # pragma: no cover
    INotify = flags = None

logger = logging.getLogger("paperless.management.consumer")


def _tags_from_path(filepath) -> list[int]:
    """
    Walk up the directory tree from filepath to CONSUMPTION_DIR
    and get or create Tag IDs for every directory.

    Returns set of Tag models
    """
    db.close_old_connections()
    tag_ids = set()
    path_parts = Path(filepath).relative_to(settings.CONSUMPTION_DIR).parent.parts
    for part in path_parts:
        tag_ids.add(
            Tag.objects.get_or_create(name__iexact=part, defaults={"name": part})[0].pk,
        )

    return list(tag_ids)


def _is_ignored(filepath: str) -> bool:
    """
    Checks if the given file should be ignored, based on configured
    patterns.

    Returns True if the file is ignored, False otherwise
    """
    filepath = os.path.abspath(
        os.path.normpath(filepath),
    )

    # Trim out the consume directory, leaving only filename and it's
    # path relative to the consume directory
    filepath_relative = PurePath(filepath).relative_to(settings.CONSUMPTION_DIR)

    # March through the components of the path, including directories and the filename
    # looking for anything matching
    # foo/bar/baz/file.pdf -> (foo, bar, baz, file.pdf)
    parts = []
    for part in filepath_relative.parts:
        # If the part is not the name (ie, it's a dir)
        # Need to append the trailing slash or fnmatch doesn't match
        # fnmatch("dir", "dir/*") == False
        # fnmatch("dir/", "dir/*") == True
        if part != filepath_relative.name:
            part = part + "/"
        parts.append(part)

    for pattern in settings.CONSUMER_IGNORE_PATTERNS:
        if len(filter(parts, pattern)):
            return True

    return False


def _consume(filepath: str) -> None:
    if os.path.isdir(filepath) or _is_ignored(filepath):
        return

    if not os.path.isfile(filepath):
        logger.debug(f"Not consuming file {filepath}: File has moved.")
        return

    if not is_file_ext_supported(os.path.splitext(filepath)[1]):
        logger.warning(f"Not consuming file {filepath}: Unknown file extension.")
        return

    # Total wait time: up to 500ms
    os_error_retry_count: Final[int] = 50
    os_error_retry_wait: Final[float] = 0.01

    read_try_count = 0
    file_open_ok = False
    os_error_str = None

    while (read_try_count < os_error_retry_count) and not file_open_ok:
        try:
            with open(filepath, "rb"):
                file_open_ok = True
        except OSError as e:
            read_try_count += 1
            os_error_str = str(e)
            sleep(os_error_retry_wait)

    if read_try_count >= os_error_retry_count:
        logger.warning(f"Not consuming file {filepath}: OS reports {os_error_str}")
        return

    tag_ids = None
    try:
        if settings.CONSUMER_SUBDIRS_AS_TAGS:
            tag_ids = _tags_from_path(filepath)
    except Exception:
        logger.exception("Error creating tags from path")

    try:
        logger.info(f"Adding {filepath} to the task queue.")
        consume_file.delay(
            ConsumableDocument(
                source=DocumentSource.ConsumeFolder,
                original_file=filepath,
            ),
            DocumentMetadataOverrides(tag_ids=tag_ids),
        )
    except Exception:
        # Catch all so that the consumer won't crash.
        # This is also what the test case is listening for to check for
        # errors.
        logger.exception("Error while consuming document")


class Command(BaseCommand):
    """
    On every iteration of an infinite loop, consume what we can from the
    consumption directory.
    """

    # This is here primarily for the tests and is irrelevant in production.
    stop_flag = Event()
    # Also only for testing, configures in one place the timeout used before checking
    # the stop flag
    testing_timeout_s: Final[float] = 0.5
    testing_timeout_ms: Final[int] = int(testing_timeout_s * 1000)

    def add_arguments(self, parser):
        parser.add_argument(
            "directory",
            default=settings.CONSUMPTION_DIR,
            nargs="?",
            help="The consumption directory.",
        )
        parser.add_argument("--oneshot", action="store_true", help="Run only once.")

        # Only use during unit testing, will configure a timeout
        # Leaving it unset or false and the consumer will exit when it
        # receives SIGINT
        parser.add_argument(
            "--testing",
            action="store_true",
            help="Flag used only for unit testing",
            default=False,
        )

    def handle(self, *args, **options):
        directory: Final[Path] = Path(options["directory"]).resolve()
        is_recursive: Final[bool] = settings.CONSUMER_RECURSIVE
        is_oneshot: Final[bool] = options["oneshot"]
        is_testing: Final[bool] = options["testing"]

        if not directory:
            raise CommandError("CONSUMPTION_DIR does not appear to be set.")

        if not directory.exists():
            raise CommandError(f"Consumption directory {directory} does not exist")

        if not directory.is_dir():
            raise CommandError(f"Consumption directory {directory} is not a directory")

        # Consumer will need this
        settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

        # Check for existing files at startup
        glob_str = "**/*" if is_recursive else "*"

        for filepath in directory.glob(glob_str):
            _consume(filepath)

        if is_oneshot:
            logger.info("One shot consume requested, exiting")
            return

        use_polling: Final[bool] = settings.CONSUMER_POLLING != 0
        poll_delay_ms: Final[int] = int(settings.CONSUMER_POLLING * 1000)

        if use_polling:
            logger.info(
                f"Polling {directory} for changes every {settings.CONSUMER_POLLING}s ",
            )
        else:
            logger.info(f"Using inotify to watch {directory} for changes")

        read_timeout_ms = 0
        if options["testing"]:
            read_timeout_ms = self.testing_timeout_ms
            logger.debug(f"Configuring initial timeout to {read_timeout_ms}ms")

        inotify_debounce_secs: Final[float] = settings.CONSUMER_INOTIFY_DELAY
        inotify_debounce_ms: Final[int] = int(inotify_debounce_secs * 1000)

        filter = DefaultFilter(ignore_entity_patterns={r"__paperless_write_test_\d+__"})

        notified_files: dict[Path, float] = {}
        while not self.stop_flag.is_set():
            try:
                for changes in watch(
                    directory,
                    watch_filter=filter,
                    rust_timeout=read_timeout_ms,
                    yield_on_timeout=True,
                    force_polling=use_polling,
                    poll_delay_ms=poll_delay_ms,
                    recursive=is_recursive,
                    stop_event=self.stop_flag,
                ):
                    for change_type, path in changes:
                        path = Path(path).resolve()
                        logger.info(f"Got {change_type.name} for {path}")

                        match change_type:
                            case Change.added | Change.modified:
                                logger.info(
                                    f"New event time for {path} at {monotonic()}",
                                )
                                notified_files[path] = monotonic()
                            case Change.deleted:
                                notified_files.pop(path, None)

                    logger.info("Checking for files that are ready")

                    # Check the files against the timeout
                    still_waiting = {}
                    # last_event_time is time of the last inotify event for this file
                    for filepath, last_event_time in notified_files.items():
                        # Current time - last time over the configured timeout
                        waited_long_enough = (
                            monotonic() - last_event_time
                        ) > inotify_debounce_secs

                        # Also make sure the file exists still, some scanners might write a
                        # temporary file first
                        file_still_exists = filepath.exists() and filepath.is_file()

                        logger.info(
                            f"{filepath} - {waited_long_enough} - {file_still_exists}",
                        )

                        if waited_long_enough and file_still_exists:
                            logger.info(f"Consuming {filepath}")
                            _consume(filepath)
                        elif file_still_exists:
                            still_waiting[filepath] = last_event_time

                        # These files are still waiting to hit the timeout
                        notified_files = still_waiting

                    # Always exit the watch loop to reconfigure the timeout
                    break

                if len(notified_files) > 0:
                    logger.info("Using inotify_debounce_ms")
                    read_timeout_ms = inotify_debounce_ms
                elif is_testing:
                    logger.info("Using testing_timeout_ms")
                    read_timeout_ms = self.testing_timeout_ms
                else:
                    logger.info("No files in waiting, configuring indefinite timeout")
                    read_timeout_ms = 0
                logger.info(f"Configuring timeout to {read_timeout_ms}ms")
            except KeyboardInterrupt:
                self.stop_flag.set()

        logger.debug("Consumer exiting.")
