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

_COURSE_CODE = re.compile(r"(?<![A-Z0-9])G\d{7}(?!\d)", re.IGNORECASE)
_ACADEMIC_YEAR = re.compile(r"^(20\d{2})/(20\d{2})$")
_EMBEDDED_ACADEMIC_YEAR = re.compile(
    r"(?<!\d)(20\d{2})\s*[/\-]\s*(20)?(\d{2})(?!\d)"
)


@dataclass(frozen=True, slots=True)
class ExamSubjectProfile:
    code: str
    name: str
    plan_id: int
    center: str
    calendar_url: str
    subject_url: str | None = None

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


# Exact, public mappings for the subjects identified in the user's double-degree courses.
# A homonymous title is never enough to select a plan: every lookup starts from the G code.
_PROFILES = (
    ExamSubjectProfile(
        "G4012330",
        "Ciberseguridade",
        17573,
        "Escola Técnica Superior de Enxeñaría",
        ETSE_EXAM_CALENDAR_URL,
    ),
    ExamSubjectProfile(
        "G4012222",
        "Algoritmos e Estruturas de Datos",
        17573,
        "Escola Técnica Superior de Enxeñaría",
        ETSE_EXAM_CALENDAR_URL,
    ),
    ExamSubjectProfile(
        "G4012221",
        "Bases de Datos I",
        17573,
        "Escola Técnica Superior de Enxeñaría",
        ETSE_EXAM_CALENDAR_URL,
    ),
    ExamSubjectProfile(
        "G1012106",
        "Cálculo numérico nunha variable",
        19955,
        "Facultade de Matemáticas",
        MATHEMATICS_EXAM_CALENDAR_URL,
        (
            "https://www.usc.gal/gl/estudos/graos/enxenaria-arquitectura/"
            "dobre-grao-enxenaria-informatica-matematicas-0/20252026/"
            "calculo-numerico-variable-20874-19957-11-109205"
        ),
    ),
    ExamSubjectProfile(
        "G1011321",
        "Cálculo Vectorial e Integración de Lebesgue",
        17573,
        "Facultade de Matemáticas",
        MATHEMATICS_EXAM_CALENDAR_URL,
        (
            "https://www.usc.gal/gl/estudos/graos/enxenaria-arquitectura/"
            "dobre-grao-enxenaria-informatica-matematicas-2a-edicion/20252026/"
            "calculo-vectorial-integracion-lebesgue-18403-17568-2-75978"
        ),
    ),
    ExamSubjectProfile(
        "G4012327",
        "Compiladores e Intérpretes",
        17573,
        "Escola Técnica Superior de Enxeñaría",
        ETSE_EXAM_CALENDAR_URL,
    ),
    ExamSubjectProfile(
        "G4012454",
        "Modelos e Técnicas de Optimización",
        17573,
        "Escola Técnica Superior de Enxeñaría",
        ETSE_EXAM_CALENDAR_URL,
    ),
    ExamSubjectProfile(
        "G1011227",
        "Programación Linear e Enteira",
        17573,
        "Facultade de Matemáticas",
        MATHEMATICS_EXAM_CALENDAR_URL,
        (
            "https://www.usc.gal/gl/estudos/graos/enxenaria-arquitectura/"
            "dobre-grao-enxenaria-informatica-matematicas-2a-edicion/20252026/"
            "programacion-linear-enteira-18403-17568-2-75975"
        ),
    ),
    ExamSubjectProfile(
        "G4012321",
        "Teoría de Autómatas e Linguaxes Formais",
        17573,
        "Escola Técnica Superior de Enxeñaría",
        ETSE_EXAM_CALENDAR_URL,
    ),
    ExamSubjectProfile(
        "G1011330",
        "Topoloxía Xeral",
        17573,
        "Facultade de Matemáticas",
        MATHEMATICS_EXAM_CALENDAR_URL,
        (
            "https://www.usc.gal/gl/estudos/graos/enxenaria-arquitectura/"
            "dobre-grao-enxenaria-informatica-matematicas-2a-edicion/20252026/"
            "topoloxia-xeral-18403-17568-2-75987"
        ),
    ),
)

EXAM_SUBJECT_PROFILES = {profile.code: profile for profile in _PROFILES}
OFFICIAL_EXAM_CALENDAR_URLS = (ETSE_EXAM_CALENDAR_URL, MATHEMATICS_EXAM_CALENDAR_URL)


def normalise_subject_code(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("El código de asignatura debe ser texto")
    code = value.strip().upper()
    if not _COURSE_CODE.fullmatch(code):
        raise ValueError("El código de asignatura debe tener el formato G seguido de 7 cifras")
    return code


def normalise_subject_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def extract_subject_code(*values: object) -> str | None:
    for value in values:
        match = _COURSE_CODE.search(str(value or ""))
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
