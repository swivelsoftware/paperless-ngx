from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

from paperless.signals.signals import handle_failed_login
from paperless.signals.signals import handle_social_account_updated


class PaperlessConfig(AppConfig):
    name = "paperless"

    verbose_name = _("Paperless")

    def ready(self):
        from django.contrib.auth.signals import user_login_failed

        user_login_failed.connect(handle_failed_login)

        from allauth.socialaccount.signals import social_account_updated

        social_account_updated.connect(handle_social_account_updated)

        from paperless.signals import document_consumption_finished
        from paperless.signals import document_updated
        from paperless.signals.handlers import add_inbox_tags
        from paperless.signals.handlers import add_to_index
        from paperless.signals.handlers import run_workflows_added
        from paperless.signals.handlers import run_workflows_updated
        from paperless.signals.handlers import set_correspondent
        from paperless.signals.handlers import set_document_type
        from paperless.signals.handlers import set_storage_path
        from paperless.signals.handlers import set_tags

        document_consumption_finished.connect(add_inbox_tags)
        document_consumption_finished.connect(set_correspondent)
        document_consumption_finished.connect(set_document_type)
        document_consumption_finished.connect(set_tags)
        document_consumption_finished.connect(set_storage_path)
        document_consumption_finished.connect(add_to_index)
        document_consumption_finished.connect(run_workflows_added)
        document_updated.connect(run_workflows_updated)

        import paperless.schema  # noqa: F401

        AppConfig.ready(self)
