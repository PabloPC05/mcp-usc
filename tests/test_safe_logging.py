import logging

from mcp_usc.server import _configure_safe_http_logging


def test_verbose_http_client_logging_is_disabled_for_mcp_process() -> None:
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    original_levels = (httpx_logger.level, httpcore_logger.level)
    try:
        httpx_logger.setLevel(logging.INFO)
        httpcore_logger.setLevel(logging.DEBUG)

        _configure_safe_http_logging()

        assert httpx_logger.level == logging.CRITICAL + 1
        assert httpcore_logger.level == logging.CRITICAL + 1
    finally:
        httpx_logger.setLevel(original_levels[0])
        httpcore_logger.setLevel(original_levels[1])
