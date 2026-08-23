from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .security import html_to_text

_OOXML_EXTENSIONS = {".docx", ".pptx", ".xlsx"}
_TEXT_EXTENSIONS = {
    ".csv",
    ".html",
    ".htm",
    ".json",
    ".md",
    ".py",
    ".r",
    ".rst",
    ".tex",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _pdf_text(content: bytes, max_pages: int) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(content))
    pages = min(len(reader.pages), max_pages)
    text = "\n".join((reader.pages[index].extract_text() or "") for index in range(pages))
    return text, pages


def _ooxml_text(content: bytes) -> str:
    fragments: list[str] = []
    total_uncompressed = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > 5_000:
            raise ValueError("El documento Office contiene demasiados elementos")
        for member in members:
            total_uncompressed += member.file_size
            if member.file_size > 20 * 1024 * 1024 or total_uncompressed > 50 * 1024 * 1024:
                raise ValueError("El documento Office supera el límite de contenido expandido")
            path = PurePosixPath(member.filename)
            relevant = (
                path.parts[:1] == ("word",)
                or path.parts[:2] == ("ppt", "slides")
                or path.parts[:1] == ("xl",)
            ) and path.suffix == ".xml"
            if not relevant:
                continue
            try:
                root = ElementTree.fromstring(archive.read(member))
            except ElementTree.ParseError:
                continue
            fragments.extend(text.strip() for text in root.itertext() if text.strip())
    return " ".join(fragments)


def extract_resource_text(
    content: bytes,
    *,
    media_type: str,
    filename: str,
    max_chars: int = 100_000,
    max_pdf_pages: int = 100,
) -> dict[str, Any]:
    if not 1 <= max_chars <= 500_000:
        raise ValueError("max_chars debe estar entre 1 y 500000")
    if not 1 <= max_pdf_pages <= 300:
        raise ValueError("max_pdf_pages debe estar entre 1 y 300")
    suffix = PurePosixPath(filename.casefold()).suffix
    normalised_type = media_type.casefold().split(";", 1)[0]
    page_count: int | None = None
    try:
        if normalised_type == "application/pdf" or suffix == ".pdf":
            text, page_count = _pdf_text(content, max_pdf_pages)
        elif normalised_type in {"text/html", "application/xhtml+xml"} or suffix in {
            ".html",
            ".htm",
        }:
            text = html_to_text(_decode_text(content), limit=max_chars + 1)
        elif normalised_type.startswith("text/") or suffix in _TEXT_EXTENSIONS:
            text = _decode_text(content)
        elif suffix in _OOXML_EXTENSIONS:
            text = _ooxml_text(content)
        else:
            return {
                "readable": False,
                "reason": "Formato binario no compatible con extracción de texto",
                "content_is_untrusted": True,
            }
    except (OSError, ValueError, zipfile.BadZipFile, PdfReadError) as exc:
        return {
            "readable": False,
            "reason": f"No se pudo extraer texto: {type(exc).__name__}",
            "content_is_untrusted": True,
        }
    clean = " ".join(text.split())
    result: dict[str, Any] = {
        "readable": True,
        "text": clean[:max_chars],
        "truncated": len(clean) > max_chars,
        "content_is_untrusted": True,
    }
    if page_count is not None:
        result["pages_read"] = page_count
    return result
