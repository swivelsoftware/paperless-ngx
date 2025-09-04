import gzip
import hashlib
import logging
import os
import shutil
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass
from pathlib import Path

import brotli
import humanize
from django.contrib.staticfiles.storage import StaticFilesStorage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FileInfo:
    file_path_str: str
    file_path_path: Path
    checksum: str
    original_size: int
    gzip_size: int | None = None
    brotli_size: int | None = None


class DeduplicatedCompressedStaticFilesStorage(StaticFilesStorage):
    # File extensions that should be compressed
    COMPRESSIBLE_EXTENSIONS = {
        ".css",
        ".js",
        ".html",
        ".htm",
        ".xml",
        ".json",
        ".txt",
        ".svg",
        ".md",
        ".rst",
        ".csv",
        ".tsv",
        ".yaml",
        ".yml",
        ".map",
    }

    # Minimum file size to compress (bytes)
    MIN_COMPRESS_SIZE = 1024  # 1KB

    # Maximum number of threads for parallel processing
    MAX_WORKERS = min(32, (os.cpu_count() or 1) + 4)

    # Chunk size for file reading
    CHUNK_SIZE = 64 * 1024  # 64KB

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # --- MODIFIED: Added path_to_file_info for easy lookup ---
        self.hash_to_files: dict[str, list[FileInfo]] = defaultdict(list)
        self.path_to_file_info: dict[str, FileInfo] = {}
        self.linked_files: set[Path] = set()
        self.compression_stats = {
            "brotli": 0,
            "gzip": 0,
            "skipped_linked": 0,
            "skipped_other": 0,
            "errors": 0,
        }
        self._lock = threading.Lock()

    def post_process(self, paths: list[str], **options):
        """
        Post-process collected files: deduplicate first, then compress.
        Django 5.2 compatible with proper options handling.
        """
        start_time = time.time()

        # Step 1: Build hash map for deduplication (parallel)
        self._build_file_hash_map_parallel(paths)

        # Step 2: Create hard links for duplicate files
        self._create_hard_links()

        # Step 3: Compress files (parallel, skip linked duplicates)
        self._compress_files_parallel(paths)

        # Step 4: Provide user a summary of the compression
        self._log_compression_summary()

        processing_time = time.time() - start_time
        logger.info(f"Post-processing complete in {processing_time:.2f}s.")

        # Return list of processed files
        processed_files = []
        for path in paths:
            processed_files.append((path, path, True))
            # Add compressed variants
            file_path = self.path(path)
            if Path(file_path + ".br").exists():
                processed_files.append((path + ".br", path + ".br", True))
            if Path(file_path + ".gz").exists():
                processed_files.append((path + ".gz", path + ".gz", True))

        return processed_files

    def _build_file_hash_map_parallel(self, file_paths: list[str]):
        """Build a map of file hashes using parallel processing."""
        logger.info(
            f"Hashing {len(file_paths)} files with {self.MAX_WORKERS} workers...",
        )

        def hash_file(path: str):
            """Hash a single file."""
            try:
                file_path = Path(self.path(path))
                if not file_path.is_file():
                    return None, None, None

                file_hash = self._get_file_hash_fast(file_path)
                file_size = file_path.stat().st_size
                return path, file_hash, file_size
            except Exception as e:
                logger.warning(f"Error hashing file {path}: {e}")
                return path, None, None

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            future_to_path = {
                executor.submit(hash_file, path): path for path in file_paths
            }

            for future in as_completed(future_to_path):
                path, file_hash, file_size = future.result()
                if path is not None and file_hash is not None and file_size is not None:
                    with self._lock:
                        file_info = FileInfo(
                            file_path_str=path,
                            file_path_path=Path(self.path(path)),
                            checksum=file_hash,
                            original_size=file_size,
                        )
                        self.hash_to_files[file_hash].append(file_info)
                        self.path_to_file_info[path] = file_info

        duplicates = sum(1 for files in self.hash_to_files.values() if len(files) > 1)
        logger.info(f"Found {duplicates} sets of duplicate files")

    def _get_file_hash_fast(self, file_path: Path):
        """Calculate SHA-256 hash of file content with optimized reading."""
        hash_sha256 = hashlib.sha256()
        try:
            with file_path.open("rb") as f:
                while chunk := f.read(self.CHUNK_SIZE):
                    hash_sha256.update(chunk)
        except OSError as e:
            logger.warning(f"Could not read file {file_path}: {e}")
            raise
        return hash_sha256.hexdigest()

    def _create_hard_links(self):
        """Create hard links for duplicate files."""
        logger.info("Creating hard links for duplicate files...")

        linked_count = 0
        for file_info_list in self.hash_to_files.values():
            if len(file_info_list) <= 1:
                continue

            # Sort by file size (desc) then path length (asc) to keep best original
            file_info_list.sort(key=lambda x: (-x.original_size, len(x.file_path_str)))
            original_file_info = file_info_list[0]
            duplicate_info = file_info_list[1:]

            for duplicate_file_info in duplicate_info:
                try:
                    # Remove duplicate file and create hard link
                    if duplicate_file_info.file_path_path.exists():
                        duplicate_file_info.file_path_path.unlink()

                    # Create hard link
                    os.link(
                        original_file_info.file_path_path,
                        duplicate_file_info.file_path_path,
                    )

                    with self._lock:
                        self.linked_files.add(duplicate_file_info.file_path_path)

                    linked_count += 1

                    logger.info(
                        f"Linked {duplicate_file_info.file_path_path} -> {original_file_info.file_path_path}",
                    )

                except OSError as e:
                    logger.error(
                        f"Hard link failed for {original_file_info.file_path_path}, copying instead: {e}",
                    )
                    # Fall back to copying if hard linking fails
                    try:
                        import shutil

                        shutil.copy2(
                            original_file_info.file_path_path,
                            original_file_info.file_path_path,
                        )
                        logger.error(
                            f"Copied {original_file_info.file_path_path} (hard link failed)",
                        )
                    except Exception as copy_error:
                        logger.error(
                            f"Failed to copy {original_file_info.file_path_path}: {copy_error}",
                        )

        if linked_count > 0:
            logger.info(f"Created {linked_count} hard links")

    def _compress_files_parallel(self, file_paths: list[str]):
        """Compress files using parallel processing and update FileInfo objects."""
        # Identify files to compress, excluding hard links
        compressible_files = [
            self.path_to_file_info[path]
            for path in file_paths
            if self.path_to_file_info[path].file_path_path not in self.linked_files
            and self._should_compress_file(path)
        ]

        if not compressible_files:
            logger.info("No new files to compress")
            return

        logger.info(
            f"Compressing {len(compressible_files)} files with {self.MAX_WORKERS} workers...",
        )

        def compress_file(file_info: FileInfo):
            """Compress a single file and update its FileInfo by side-effect."""
            brotli_size = None
            gzip_size = None
            error = None
            try:
                brotli_size = self._compress_file_brotli(str(file_info.file_path_path))
                gzip_size = self._compress_file_gzip(str(file_info.file_path_path))
                # Store the compressed sizes
                file_info.brotli_size = brotli_size
                file_info.gzip_size = gzip_size
            except Exception as e:
                error = str(e)
                logger.warning(f"Error compressing {file_info.file_path_str}: {e}")
            return {
                "brotli": brotli_size is not None,
                "gzip": gzip_size is not None,
                "error": error,
            }

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            future_to_info = {
                executor.submit(compress_file, info): info
                for info in compressible_files
            }

            for future in as_completed(future_to_info):
                result = future.result()
                with self._lock:
                    if result["brotli"]:
                        self.compression_stats["brotli"] += 1
                    if result["gzip"]:
                        self.compression_stats["gzip"] += 1
                    if result["error"]:
                        self.compression_stats["errors"] += 1
                    if (
                        not result["brotli"]
                        and not result["gzip"]
                        and not result["error"]
                    ):
                        self.compression_stats["skipped_other"] += 1

        self.compression_stats["skipped_linked"] = len(self.linked_files)
        logger.info(f"File count stats: {self.compression_stats}")

    def _should_compress_file(self, path: str):
        """Determine if a file should be compressed."""
        file_ext = Path(path).suffix.lower()
        if file_ext not in self.COMPRESSIBLE_EXTENSIONS:
            return False
        try:
            if Path(self.path(path)).stat().st_size < self.MIN_COMPRESS_SIZE:
                return False
        except OSError:
            return False
        return True

    def _compress_file_brotli(self, file_path: str) -> int | None:
        """Compress file using Brotli, returns compressed size or None."""
        brotli_path = Path(file_path + ".br")
        try:
            with Path(file_path).open("rb") as f_in:
                original_data = f_in.read()
            compressed_data = brotli.compress(
                original_data,
                quality=10,
                lgwin=22,  # Window size
                lgblock=0,  # Auto block size
            )
            if len(compressed_data) < len(original_data) * 0.95:
                with brotli_path.open("wb") as f_out:
                    f_out.write(compressed_data)
                return len(compressed_data)
            return None
        except Exception as e:
            logger.warning(f"Brotli compression failed for {file_path}: {e}")
            return None

    def _compress_file_gzip(self, file_path: str) -> int | None:
        """Compress file using GZip, returns compressed size or None."""
        gzip_path = Path(file_path + ".gz")
        file_path_path = Path(file_path)
        try:
            original_size = file_path_path.stat().st_size
            with (
                file_path_path.open("rb") as f_in,
                gzip.open(
                    gzip_path,
                    "wb",
                    compresslevel=7,
                ) as f_out,
            ):
                shutil.copyfileobj(f_in, f_out, length=self.CHUNK_SIZE)

            compressed_size = gzip_path.stat().st_size
            if compressed_size < original_size * 0.95:
                return compressed_size
            else:
                gzip_path.unlink()
                return None
        except Exception as e:
            logger.warning(f"GZip compression failed for {file_path}: {e}")
            if gzip_path.exists():
                try:
                    gzip_path.unlink()
                except OSError:
                    pass
            return None

    def _log_compression_summary(self):
        """Calculates and logs the total size savings from compression."""
        total_original_size = 0
        total_brotli_size = 0
        total_gzip_size = 0

        # Only consider the original files, not the duplicates, for size calculation
        unique_files = {
            file_list[0].checksum: file_list[0]
            for file_list in self.hash_to_files.values()
        }

        for file_info in unique_files.values():
            if self._should_compress_file(file_info.file_path_str):
                total_original_size += file_info.original_size
                if file_info.brotli_size:
                    total_brotli_size += file_info.brotli_size
                if file_info.gzip_size:
                    total_gzip_size += file_info.gzip_size

        def get_savings(original: int, compressed: int) -> str:
            if original == 0:
                return "0.00%"
            return f"{(1 - compressed / original) * 100:.2f}%"

        logger.info(
            f"Total Original Size (compressible files): {humanize.naturalsize(total_original_size)}",
        )
        if total_brotli_size > 0:
            logger.info(
                f"Total Brotli Size: {humanize.naturalsize(total_brotli_size)} "
                f"(Savings: {get_savings(total_original_size, total_brotli_size)})",
            )
        if total_gzip_size > 0:
            logger.info(
                f"Total Gzip Size:   {humanize.naturalsize(total_gzip_size)} "
                f"(Savings: {get_savings(total_original_size, total_gzip_size)})",
            )
