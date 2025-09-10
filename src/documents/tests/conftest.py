import zoneinfo

import pytest
from django.contrib.auth import get_user_model
from pytest_django.fixtures import SettingsWrapper
from rest_framework.test import APIClient


@pytest.fixture()
def settings_timezone(settings: SettingsWrapper) -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(settings.TIME_ZONE)


@pytest.fixture
def rest_api_client():
    """
    The basic DRF ApiClient
    """
    yield APIClient()


@pytest.fixture
def authenticated_rest_api_client(rest_api_client: APIClient):
    """
    The basic DRF ApiClient which has been authenticated
    """
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username="testuser", password="password")
    rest_api_client.force_authenticate(user=user)
    yield rest_api_client


# @pytest.fixture(autouse=True)
def configure_whitenoise_middleware(request, settings):
    """
    By default, remove Whitenoise middleware from tests.
    Only include it when test is marked with @pytest.mark.use_whitenoise
    """
    # Check if the test is marked to use whitenoise
    use_whitenoise_marker = request.node.get_closest_marker("use_whitenoise")

    if not use_whitenoise_marker:
        # Filter out whitenoise middleware using pytest-django's settings fixture
        middleware_without_whitenoise = [
            mw
            for mw in settings.MIDDLEWARE
            if "whitenoise.middleware.WhiteNoiseMiddleware" not in mw.lower()
        ]

        settings.MIDDLEWARE = middleware_without_whitenoise
    yield
