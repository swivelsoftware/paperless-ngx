import os
import textwrap
from pathlib import Path
from unittest import mock

from django.core.checks import Error
from django.core.checks import Warning
from django.test import TestCase
from django.test import override_settings

from paperless.checks import audit_log_check
from paperless.checks import binaries_check
from paperless.checks import changed_password_check
from paperless.checks import debug_mode_check
from paperless.checks import filename_format_check
from paperless.checks import parser_check
from paperless.checks import paths_check
from paperless.checks import settings_values_check
from paperless.models import Document
from paperless.tests.factories import DocumentFactory
from paperless.tests.utils import DirectoriesMixin
from paperless.tests.utils import FileSystemAssertsMixin


class TestDocumentChecks(TestCase):
    def test_changed_password_check_empty_db(self):
        self.assertListEqual(changed_password_check(None), [])

    def test_changed_password_check_no_encryption(self):
        DocumentFactory.create(storage_type=Document.STORAGE_TYPE_UNENCRYPTED)
        self.assertListEqual(changed_password_check(None), [])

    def test_encrypted_missing_passphrase(self):
        DocumentFactory.create(storage_type=Document.STORAGE_TYPE_GPG)
        msgs = changed_password_check(None)
        self.assertEqual(len(msgs), 1)
        msg_text = msgs[0].msg
        self.assertEqual(
            msg_text,
            "The database contains encrypted documents but no password is set.",
        )

    @override_settings(
        PASSPHRASE="test",
    )
    @mock.patch("paperless.db.GnuPG.decrypted")
    @mock.patch("documents.models.Document.source_file")
    def test_encrypted_decrypt_fails(self, mock_decrypted, mock_source_file):
        mock_decrypted.return_value = None
        mock_source_file.return_value = b""

        DocumentFactory.create(storage_type=Document.STORAGE_TYPE_GPG)

        msgs = changed_password_check(None)

        self.assertEqual(len(msgs), 1)
        msg_text = msgs[0].msg
        self.assertEqual(
            msg_text,
            textwrap.dedent(
                """
                The current password doesn't match the password of the
                existing documents.

                If you intend to change your password, you must first export
                all of the old documents, start fresh with the new password
                and then re-import them."
                """,
            ),
        )

    def test_parser_check(self):
        self.assertEqual(parser_check(None), [])

        with mock.patch("documents.checks.document_consumer_declaration.send") as m:
            m.return_value = []

            self.assertEqual(
                parser_check(None),
                [
                    Error(
                        "No parsers found. This is a bug. The consumer won't be "
                        "able to consume any documents without parsers.",
                    ),
                ],
            )

    def test_filename_format_check(self):
        self.assertEqual(filename_format_check(None), [])

        with override_settings(FILENAME_FORMAT="{created}/{title}"):
            self.assertEqual(
                filename_format_check(None),
                [
                    Warning(
                        "Filename format {created}/{title} is using the old style, please update to use double curly brackets",
                        hint="{{ created }}/{{ title }}",
                    ),
                ],
            )


class TestChecks(DirectoriesMixin, TestCase):
    def test_binaries(self):
        self.assertEqual(binaries_check(None), [])

    @override_settings(CONVERT_BINARY="uuuhh")
    def test_binaries_fail(self):
        self.assertEqual(len(binaries_check(None)), 1)

    def test_paths_check(self):
        self.assertEqual(paths_check(None), [])

    @override_settings(
        MEDIA_ROOT="uuh",
        DATA_DIR="whatever",
        CONSUMPTION_DIR="idontcare",
    )
    def test_paths_check_dont_exist(self):
        msgs = paths_check(None)
        self.assertEqual(len(msgs), 3, str(msgs))

        for msg in msgs:
            self.assertTrue(msg.msg.endswith("is set but doesn't exist."))

    def test_paths_check_no_access(self):
        Path(self.dirs.data_dir).chmod(0o000)
        Path(self.dirs.media_dir).chmod(0o000)
        Path(self.dirs.consumption_dir).chmod(0o000)

        self.addCleanup(os.chmod, self.dirs.data_dir, 0o777)
        self.addCleanup(os.chmod, self.dirs.media_dir, 0o777)
        self.addCleanup(os.chmod, self.dirs.consumption_dir, 0o777)

        msgs = paths_check(None)
        self.assertEqual(len(msgs), 3)

        for msg in msgs:
            self.assertTrue(msg.msg.endswith("is not writeable"))

    @override_settings(DEBUG=False)
    def test_debug_disabled(self):
        self.assertEqual(debug_mode_check(None), [])

    @override_settings(DEBUG=True)
    def test_debug_enabled(self):
        self.assertEqual(len(debug_mode_check(None)), 1)


class TestSettingsChecksAgainstDefaults(DirectoriesMixin, TestCase):
    def test_all_valid(self):
        """
        GIVEN:
            - Default settings
        WHEN:
            - Settings are validated
        THEN:
            - No system check errors reported
        """
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 0)


class TestOcrSettingsChecks(DirectoriesMixin, TestCase):
    @override_settings(OCR_OUTPUT_TYPE="notapdf")
    def test_invalid_output_type(self):
        """
        GIVEN:
            - Default settings
            - OCR output type is invalid
        WHEN:
            - Settings are validated
        THEN:
            - system check error reported for OCR output type
        """
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn('OCR output type "notapdf"', msg.msg)

    @override_settings(OCR_MODE="makeitso")
    def test_invalid_ocr_type(self):
        """
        GIVEN:
            - Default settings
            - OCR type is invalid
        WHEN:
            - Settings are validated
        THEN:
            - system check error reported for OCR type
        """
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn('OCR output mode "makeitso"', msg.msg)

    @override_settings(OCR_MODE="skip_noarchive")
    def test_deprecated_ocr_type(self):
        """
        GIVEN:
            - Default settings
            - OCR type is deprecated
        WHEN:
            - Settings are validated
        THEN:
            - deprecation warning reported for OCR type
        """
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn("deprecated", msg.msg)

    @override_settings(OCR_SKIP_ARCHIVE_FILE="invalid")
    def test_invalid_ocr_skip_archive_file(self):
        """
        GIVEN:
            - Default settings
            - OCR_SKIP_ARCHIVE_FILE is invalid
        WHEN:
            - Settings are validated
        THEN:
            - system check error reported for OCR_SKIP_ARCHIVE_FILE
        """
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn('OCR_SKIP_ARCHIVE_FILE setting "invalid"', msg.msg)

    @override_settings(OCR_CLEAN="cleanme")
    def test_invalid_ocr_clean(self):
        """
        GIVEN:
            - Default settings
            - OCR cleaning type is invalid
        WHEN:
            - Settings are validated
        THEN:
            - system check error reported for OCR cleaning type
        """
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn('OCR clean mode "cleanme"', msg.msg)


class TestTimezoneSettingsChecks(DirectoriesMixin, TestCase):
    @override_settings(TIME_ZONE="TheMoon\\MyCrater")
    def test_invalid_timezone(self):
        """
        GIVEN:
            - Default settings
            - Timezone is invalid
        WHEN:
            - Settings are validated
        THEN:
            - system check error reported for timezone
        """
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn('Timezone "TheMoon\\MyCrater"', msg.msg)


class TestBarcodeSettingsChecks(DirectoriesMixin, TestCase):
    @override_settings(CONSUMER_BARCODE_SCANNER="Invalid")
    def test_barcode_scanner_invalid(self):
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn('Invalid Barcode Scanner "Invalid"', msg.msg)

    @override_settings(CONSUMER_BARCODE_SCANNER="")
    def test_barcode_scanner_empty(self):
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn('Invalid Barcode Scanner ""', msg.msg)

    @override_settings(CONSUMER_BARCODE_SCANNER="PYZBAR")
    def test_barcode_scanner_valid(self):
        msgs = settings_values_check(None)
        self.assertEqual(len(msgs), 0)


class TestEmailCertSettingsChecks(DirectoriesMixin, FileSystemAssertsMixin, TestCase):
    @override_settings(EMAIL_CERTIFICATE_FILE=Path("/tmp/not_actually_here.pem"))
    def test_not_valid_file(self):
        """
        GIVEN:
            - Default settings
            - Email certificate is set
        WHEN:
            - Email certificate file doesn't exist
        THEN:
            - system check error reported for email certificate
        """
        self.assertIsNotFile("/tmp/not_actually_here.pem")

        msgs = settings_values_check(None)

        self.assertEqual(len(msgs), 1)

        msg = msgs[0]

        self.assertIn("Email cert /tmp/not_actually_here.pem is not a file", msg.msg)


class TestAuditLogChecks(TestCase):
    def test_was_enabled_once(self):
        """
        GIVEN:
            - Audit log is not enabled
        WHEN:
            - Database tables contain audit log entry
        THEN:
            - system check error reported for disabling audit log
        """
        introspect_mock = mock.MagicMock()
        introspect_mock.introspection.table_names.return_value = ["auditlog_logentry"]
        with override_settings(AUDIT_LOG_ENABLED=False):
            with mock.patch.dict(
                "paperless.checks.connections",
                {"default": introspect_mock},
            ):
                msgs = audit_log_check(None)

                self.assertEqual(len(msgs), 1)

                msg = msgs[0]

                self.assertIn(
                    ("auditlog table was found but audit log is disabled."),
                    msg.msg,
                )
