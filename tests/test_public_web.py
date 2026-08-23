from mcp_usc.public_web import _html_lines, _snippets


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
