from __future__ import annotations

import asyncio
import difflib
import io
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .domain import MADRID
from .security import html_to_text, validate_usc_url

_EXAM_TERMS = ("exame", "examen", "proba", "prueba", "avaliación", "evaluación")
_STOPWORDS = frozenset({"a", "de", "do", "da", "e", "el", "la", "o", "y"})
_DATE = re.compile(
    r"\b(?:(?:[0-3]?\d[./][01]?\d(?:[./](?:20)?\d{2})?)|"
    r"(?:[0-3]?\d-[01]?\d-(?:20)?\d{2})|"
    r"[0-3]?\d\s+(?:de\s+)?(?:xaneiro|enero|febreiro|febrero|marzo|abril|maio|mayo|"
    r"xuño|junio|xullo|julio|agosto|setembro|septiembre|outubro|octubre|novembro|"
    r"noviembre|decembro|diciembre)(?:\s+(?:de\s+)?20\d{2})?)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class FetchedDocument:
    url: str
    content_type: str
    content: bytes


class PublicUscClient:
    """Small, user-triggered USC fetcher. It deliberately is not a general web crawler."""

    def __init__(self, timeout: float = 30.0, max_bytes: int = 15_000_000) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    async def fetch(self, url: str) -> FetchedDocument:
        current = validate_usc_url(url)
        headers = {
            "User-Agent": "mcp-usc/0.5 (+https://github.com/PabloPC05/mcp-usc)",
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.2",
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            for _ in range(6):
                async with client.stream("GET", current, headers=headers) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise httpx.HTTPStatusError(
                                "Redirección sin destino",
                                request=response.request,
                                response=response,
                            )
                        current = validate_usc_url(urljoin(current, location))
                        continue
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise ValueError(
                                f"El documento supera el límite de {self.max_bytes} bytes"
                            )
                        chunks.append(chunk)
                    return FetchedDocument(
                        url=current,
                        content_type=response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .lower(),
                        content=b"".join(chunks),
                    )
        raise ValueError("Demasiadas redirecciones al consultar la fuente USC")


def _html_lines(content: bytes) -> tuple[list[str], list[str]]:
    soup = BeautifulSoup(content.decode("utf-8", errors="replace"), "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    lines: list[str] = []
    for element in soup.select("h1, h2, h3, h4, p, li, tr"):
        text = html_to_text(element.get_text(" ", strip=True), limit=2_000)
        if text and text not in lines:
            lines.append(text)
    links: list[str] = []
    for link in soup.select("a[href]"):
        href = str(link.get("href", ""))
        label = html_to_text(link.get_text(" ", strip=True)).lower()
        if href.lower().endswith(".pdf") or any(term in label for term in _EXAM_TERMS):
            links.append(href)
    return lines, links


def _pdf_pages(content: bytes) -> Iterable[tuple[int, list[str]]]:
    reader = PdfReader(io.BytesIO(content))
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        lines = [html_to_text(line, limit=2_000) for line in text.splitlines()]
        yield page_number, [line for line in lines if line]


def _normalise_search(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _matches(line: str, query_terms: tuple[str, ...], require_date: bool) -> bool:
    lowered = _normalise_search(line)
    line_terms = re.findall(r"\w+", lowered)

    def term_matches(term: str) -> bool:
        term = _normalise_search(term)
        return term in lowered or any(
            difflib.SequenceMatcher(a=term, b=candidate).ratio() >= 0.84 for candidate in line_terms
        )

    query_match = not query_terms or all(term_matches(term) for term in query_terms)
    if query_terms:
        return query_match
    if require_date:
        return bool(_DATE.search(line))
    return any(term in lowered for term in _EXAM_TERMS)


def _snippets(
    lines: list[str], query_terms: tuple[str, ...], *, max_snippets: int, require_date: bool = True
) -> list[str]:
    if max_snippets <= 0:
        return []
    selected: list[str] = []
    for index, line in enumerate(lines):
        if not _matches(line, query_terms, require_date):
            continue
        start = max(0, index - 1)
        end = min(len(lines), index + 2)
        snippet = " | ".join(lines[start:end])[:2_500]
        if snippet not in selected:
            selected.append(snippet)
        if len(selected) >= max_snippets:
            break
    return selected


async def search_exam_sources(
    source_urls: tuple[str, ...],
    query: str = "",
    *,
    max_documents: int = 8,
    max_snippets_per_document: int = 8,
    timeout: float = 30.0,
) -> dict[str, object]:
    if not source_urls:
        return {
            "results": [],
            "warning": (
                "No hay fuentes configuradas. Define USC_EXAM_SOURCES con URLs oficiales "
                "del centro/titulación."
            ),
        }
    if max_documents < 1 or max_documents > 20:
        raise ValueError("max_documents debe estar entre 1 y 20")

    client = PublicUscClient(timeout=timeout)
    queue = [validate_usc_url(url) for url in source_urls]
    visited: set[str] = set()
    results: list[dict[str, object]] = []
    normalised_query = _normalise_search(query)
    terms = tuple(
        part
        for part in re.findall(r"\w+", normalised_query)
        if len(part) >= 2 and part not in _STOPWORDS
    )

    while queue and len(visited) < max_documents:
        requested = queue.pop(0)
        if requested in visited:
            continue
        visited.add(requested)
        try:
            document = await client.fetch(requested)
            if document.content_type == "application/pdf" or document.url.lower().endswith(".pdf"):
                document_result_count = 0
                for page_number, lines in _pdf_pages(document.content):
                    page_terms = terms
                    if terms and any(_matches(line, terms, False) for line in lines):
                        page_terms = ()
                    snippets = _snippets(
                        lines,
                        page_terms,
                        max_snippets=max_snippets_per_document - document_result_count,
                    )
                    for snippet in snippets:
                        results.append(
                            {
                                "source_url": document.url,
                                "page": page_number,
                                "snippet": snippet,
                                "content_is_untrusted": True,
                            }
                        )
                        document_result_count += 1
                    if document_result_count >= max_snippets_per_document:
                        break
            else:
                lines, links = _html_lines(document.content)
                page_terms = terms
                if terms and any(_matches(line, terms, False) for line in lines[:5]):
                    page_terms = ()
                snippets = _snippets(lines, page_terms, max_snippets=max_snippets_per_document)
                for snippet in snippets:
                    results.append(
                        {
                            "source_url": document.url,
                            "page": None,
                            "snippet": snippet,
                            "content_is_untrusted": True,
                        }
                    )
                for link in links:
                    try:
                        candidate = validate_usc_url(urljoin(document.url, link))
                    except ValueError:
                        continue
                    if candidate not in visited and candidate not in queue:
                        queue.append(candidate)
            await asyncio.sleep(0.25)
        except Exception as exc:
            results.append(
                {
                    "source_url": requested,
                    "page": None,
                    "error": f"No se pudo leer la fuente: {exc}",
                }
            )

    return {
        "query": query,
        "fetched_at": datetime.now(MADRID).isoformat(),
        "documents_checked": len(visited),
        "results": results,
        "note": (
            "Verifica siempre centro, titulación, curso académico y convocatoria "
            "en la URL canónica."
        ),
    }
