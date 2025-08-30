import logging
import resource
import time

from django.conf import settings

from paperless import version

logger = logging.getLogger("middleware")


class ApiVersionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated:
            versions = settings.REST_FRAMEWORK["ALLOWED_VERSIONS"]
            response["X-Api-Version"] = versions[len(versions) - 1]
            response["X-Version"] = version.__full_version_str__

        return response


try:
    import psutil

    _PSUTIL = True
except Exception:
    _PSUTIL = False


class MemLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # capture baseline
        ru_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if _PSUTIL:
            p = psutil.Process()
            rss_before = p.memory_info().rss
        else:
            rss_before = 0

        t0 = time.perf_counter()
        try:
            return self.get_response(request)
        finally:
            dur_ms = (time.perf_counter() - t0) * 1000.0

            ru_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # ru_maxrss is KB on Linux; convert to MB
            peak_mb = (ru_after) / 1024.0
            peak_delta_mb = (ru_after - ru_before) / 1024.0

            if _PSUTIL:
                rss_after = p.memory_info().rss
                delta_mb = (rss_after - rss_before) / (1024 * 1024)
                rss_mb = rss_after / (1024 * 1024)
            else:
                delta_mb = 0.0
                rss_mb = 0.0

            logger.debug(
                "mem rss=%.1fMB Δend=%.1fMB peak=%.1fMB Δpeak=%.1fMB dur=%.1fms %s %s",
                rss_mb,
                delta_mb,
                peak_mb,
                peak_delta_mb,
                dur_ms,
                request.method,
                request.path,
            )
