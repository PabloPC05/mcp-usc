import httpx

from mcp_usc import USER_AGENT, __version__
from mcp_usc.public_web import PublicUscClient, _html_lines, _snippets, search_exam_sources


def test_html_parser_finds_exam_pdf_and_ignores_scripts() -> None:
    content = b"""
    <html><body><h1>Calendario</h1>
      <table><tr><td>Alxebra</td><td>12/01/2027</td><td>10:00</td></tr></table>
      <a href="/files/exames.pdf">Calendario de exames</a>
      <script>prompt injection</script>
    </body></html>
    """
    lines, links = _html_lines(content)
    assert any("12/01/2027" in line for line in lines)
    assert links == ["/files/exames.pdf"]
    assert all("prompt injection" not in line for line in lines)


def test_snippets_match_subject_query() -> None:
    lines = ["Calendario", "Álxebra Lineal 12/01/2027 10:00 Aula 5", "Outro texto"]
    assert _snippets(lines, ("álxebra",), max_snippets=3) == [
        "Calendario | Álxebra Lineal 12/01/2027 10:00 Aula 5 | Outro texto"
    ]


def test_time_range_is_not_mistaken_for_a_date() -> None:
    lines = ["Clase ordinaria 12:00-14:00", "Exame 11.01.2027 09:30"]
    assert _snippets(lines, (), max_snippets=3) == [
        "Clase ordinaria 12:00-14:00 | Exame 11.01.2027 09:30"
    ]


def test_html_lines_deduplicates_repeated_text_in_order() -> None:
    content = b"<p>Exame 12/01/2027</p><p>Outro</p><li>Exame 12/01/2027</li>"
    lines, _ = _html_lines(content)
    assert lines == ["Exame 12/01/2027", "Outro"]


def _mock_site(pages: dict[str, httpx.Response], requested: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) in pages:
            return pages[str(request.url)]
        return httpx.Response(404, text="missing")

    return httpx.MockTransport(handler)


async def test_crawler_only_follows_links_on_the_same_host(monkeypatch) -> None:
    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("mcp_usc.public_web.asyncio.sleep", no_sleep)
    requested: list[str] = []
    source = "https://www.usc.gal/gl/centro/calendarios"
    html = (
        "<p>Exame 12/01/2027</p>"
        '<a href="/gl/centro/exames-2">Calendario de exames</a>'
        '<a href="https://cv.usc.es/exames">Calendario de exames</a>'
        '<a href="https://intranet.usc.es/exames.pdf">PDF</a>'
    )
    transport = _mock_site(
        {
            source: httpx.Response(200, text=html, headers={"content-type": "text/html"}),
            "https://www.usc.gal/gl/centro/exames-2": httpx.Response(
                200, text="<p>Exame 13/01/2027</p>", headers={"content-type": "text/html"}
            ),
        },
        requested,
    )

    result = await search_exam_sources((source,), transport=transport)

    assert requested == [source, "https://www.usc.gal/gl/centro/exames-2"]
    assert result["documents_checked"] == 2


async def test_crawler_sends_the_package_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, text="<p>x</p>", headers={"content-type": "text/html"})

    client = PublicUscClient(transport=httpx.MockTransport(handler))
    await client.fetch("https://www.usc.gal/gl/centro/calendarios")

    assert USER_AGENT.startswith(f"mcp-usc/{__version__} ")
    assert seen == [USER_AGENT]


async def test_crawler_rejects_redirects_to_another_host(monkeypatch) -> None:
    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("mcp_usc.public_web.asyncio.sleep", no_sleep)
    requested: list[str] = []
    source = "https://www.usc.gal/gl/centro/calendarios"
    transport = _mock_site(
        {source: httpx.Response(302, headers={"location": "https://cv.usc.es/private"})},
        requested,
    )

    result = await search_exam_sources((source,), transport=transport)

    assert requested == [source]
    assert "error" in result["results"][0]  # type: ignore[index]
