import pytest

from mcp_usc.security import UnsafeUrlError, html_to_text, redact_secret, validate_usc_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.usc.gal/gl/centro",
        "https://assets.usc.gal/file.pdf",
        "https://usc.es/page",
    ],
)
def test_accepts_official_public_hosts(url: str) -> None:
    assert validate_usc_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://www.usc.gal/gl/centro",
        "https://usc.gal.evil.example/file",
        "https://user:pass@www.usc.gal/file",
        "https://www.usc.gal:8443/file",
        "https://www.usc.gal/file?token=secret",
        "https://www.usc.gal/file?sesskey=secret",
        "file:///etc/passwd",
    ],
)
def test_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_usc_url(url)


def test_campus_allowlist_is_narrower() -> None:
    assert validate_usc_url("https://cv.usc.es", campus=True)
    with pytest.raises(UnsafeUrlError):
        validate_usc_url("https://www.usc.es", campus=True)


def test_rejected_url_error_does_not_echo_secret() -> None:
    with pytest.raises(UnsafeUrlError) as caught:
        validate_usc_url("https://www.usc.gal/file?token=top-secret")
    assert "top-secret" not in str(caught.value)


def test_html_is_text_only_and_bounded() -> None:
    value = "<p>Hola <b>mundo</b></p><script>steal()</script><p>  fin </p>"
    assert html_to_text(value) == "Hola mundo fin"
    assert html_to_text("x" * 20, limit=4) == "xxxx"


def test_redacts_secret() -> None:
    assert redact_secret("token=abc123", "abc123") == "token=[REDACTED]"
