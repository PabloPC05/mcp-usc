from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

ETSE_EXAM_CALENDAR_URL = (
    "https://www.usc.gal/gl/centro/escola-tecnica-superior-enxenaria/"
    "calendarios/convocatorias"
)
MATHEMATICS_EXAM_CALENDAR_URL = (
    "https://www.usc.gal/gl/centro/facultade-matematicas/calendarios/convocatorias"
)

SUBJECT_CODE_PATTERN = re.compile(r"G\d{7}[A-Z]?", re.IGNORECASE)
SUBJECT_CODE_SEARCH_PATTERN = re.compile(
    r"(?<![A-Z0-9])G\d{7}[A-Z]?(?![A-Z0-9])", re.IGNORECASE
)
_ACADEMIC_YEAR = re.compile(r"^(20\d{2})/(20\d{2})$")
_EMBEDDED_ACADEMIC_YEAR = re.compile(
    r"(?<!\d)(20\d{2})\s*[/\-]\s*(20)?(\d{2})(?!\d)"
)


OFFICIAL_EXAM_CALENDAR_URLS = (ETSE_EXAM_CALENDAR_URL, MATHEMATICS_EXAM_CALENDAR_URL)


@dataclass(frozen=True, slots=True)
class DegreeExamProfile:
    key: str
    name: str
    study_plan_url: str
    study_plan_catalog_id: int
    calendar_plan_id: int
    calendar_urls: tuple[str, ...] = OFFICIAL_EXAM_CALENDAR_URLS
    crosswalk_method: str = "curated_and_live_exact_subject_match_verified"

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


# This crosswalk is institutional metadata, not a list of the user's subjects. USC does not
# publish a direct foreign key between study-plan IDs and calendar is-type IDs, so both mappings
# are kept explicit and validated against exact subject titles at runtime.
_DOUBLE_DEGREE_ROOT = (
    "https://www.usc.gal/gl/estudos/graos/enxenaria-arquitectura/"
    "dobre-grao-enxenaria-informatica-matematicas"
)
_DEGREES = (
    DegreeExamProfile(
        key="double_degree_current",
        name="Dobre Grao en Enxeñaría Informática e en Matemáticas",
        study_plan_url=f"{_DOUBLE_DEGREE_ROOT}-0",
        study_plan_catalog_id=20872,
        calendar_plan_id=19955,
    ),
    DegreeExamProfile(
        key="double_degree_second_edition",
        name="Dobre Grao en Enxeñaría Informática e en Matemáticas (2ª edición)",
        study_plan_url=f"{_DOUBLE_DEGREE_ROOT}-2a-edicion",
        study_plan_catalog_id=18399,
        calendar_plan_id=17573,
    ),
)

DEGREE_EXAM_PROFILES = {profile.key: profile for profile in _DEGREES}


def normalise_subject_code(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("El código de asignatura debe ser texto")
    code = value.strip().upper()
    if not SUBJECT_CODE_PATTERN.fullmatch(code):
        raise ValueError(
            "El código de asignatura debe tener el formato G, 7 cifras "
            "y un sufijo A-Z opcional"
        )
    return code


def normalise_subject_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def extract_subject_code(*values: object) -> str | None:
    for value in values:
        match = SUBJECT_CODE_SEARCH_PATTERN.search(str(value or ""))
        if match:
            return match.group(0).upper()
    return None


def normalise_academic_year(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("academic_year es obligatorio y debe tener el formato 2025/2026")
    match = _ACADEMIC_YEAR.fullmatch(value.strip())
    if not match:
        raise ValueError("academic_year debe tener el formato 2025/2026")
    start = int(match.group(1))
    end = int(match.group(2))
    if end != start + 1:
        raise ValueError("academic_year debe contener dos años consecutivos")
    return f"{start}/{end}"


def extract_academic_year(*values: object) -> str | None:
    for value in values:
        match = _EMBEDDED_ACADEMIC_YEAR.search(str(value or ""))
        if not match:
            continue
        start = int(match.group(1))
        end = int((match.group(2) or "20") + match.group(3))
        if end == start + 1:
            return f"{start}/{end}"
    return None
