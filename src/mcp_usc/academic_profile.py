"""Perfil académico local para consultas de horarios.

El perfil solo contiene preferencias de selección para fuentes públicas. No se
guarda ninguna credencial y ninguna operación de este módulo escribe en USC.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from platformdirs import user_config_path

from .domain import MADRID
from .exam_catalog import normalise_academic_year, normalise_subject_code
from .security import UnsafeUrlError, validate_usc_url

_DEGREE_PATH = re.compile(
    r"^/(?:gl/estudos/graos|es/estudios/grados|en/studies/degrees)/"
    r"[a-z0-9][a-z0-9-]{0,199}/[a-z0-9][a-z0-9-]{0,199}/?$"
)
_GROUP = re.compile(r"[A-Z0-9_-]{1,40}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PROFILE_MAX_BYTES = 64 * 1024
_PROFILE_FILE_ENV = "USC_ACADEMIC_PROFILE_FILE"
DEFAULT_PROFILE_FILENAME = "perfil.json"


def default_profile_path() -> Path:
    """Per-user profile written by `mcp-usc setup` and read when no variable overrides it."""

    return user_config_path("mcp-usc", appauthor=False) / DEFAULT_PROFILE_FILENAME


def _utc_now() -> datetime:
    return datetime.now(UTC)


def madrid_today() -> date:
    """Current calendar date in Europe/Madrid, independent of the host timezone."""

    return _utc_now().astimezone(MADRID).date()


class AcademicProfileError(ValueError):
    """La configuración académica local está incompleta o no es segura."""


def _degree_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcademicProfileError("USC_ACADEMIC_DEGREE_URL es obligatorio")
    candidate = value.strip()
    try:
        validated = validate_usc_url(candidate)
    except UnsafeUrlError as exc:
        raise AcademicProfileError("USC_ACADEMIC_DEGREE_URL no es una URL USC segura") from exc
    parsed = urlparse(validated)
    decoded_path = unquote(parsed.path)
    if (
        (parsed.hostname or "").casefold().rstrip(".") not in {"usc.gal", "www.usc.gal"}
        or parsed.query
        or parsed.fragment
        or _CONTROL.search(decoded_path)
        or "\\" in decoded_path
        or re.search(r"%(?:2f|5c)", parsed.path, re.IGNORECASE)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
        or _DEGREE_PATH.fullmatch(decoded_path) is None
    ):
        raise AcademicProfileError(
            "USC_ACADEMIC_DEGREE_URL debe ser una página oficial de titulación USC"
        )
    return validated


def _positive_int(value: object, name: str, *, maximum: int) -> int:
    # Only real ints or ASCII-digit text: no bools, floats (2.9 must not become 2),
    # bytes, or non-ASCII digits that int() would silently accept.
    if isinstance(value, bool):
        raise AcademicProfileError(f"{name} debe ser un entero positivo")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        result = int(value.strip())
    else:
        raise AcademicProfileError(f"{name} debe ser un entero positivo")
    if not 1 <= result <= maximum:
        raise AcademicProfileError(f"{name} debe estar entre 1 y {maximum}")
    return result


def _optional_positive_int(value: object, name: str, *, maximum: int) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _positive_int(value, name, maximum=maximum)


def _text_list(value: object, name: str, *, maximum: int) -> tuple[str, ...]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return ()
    if isinstance(value, str):
        values = [item.strip() for item in re.split(r"[;,\n]", value)]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise AcademicProfileError(f"{name} debe ser una lista o texto separado por ;")
    if len(values) > maximum:
        raise AcademicProfileError(f"{name} admite como máximo {maximum} elementos")
    if not all(isinstance(item, str) for item in values):
        raise AcademicProfileError(f"Cada elemento de {name} debe ser texto")
    return tuple(item for item in values if item)


def _groups(value: object) -> tuple[str, ...]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return ()
    if isinstance(value, str):
        values = re.split(r"[;,\n]", value)
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise AcademicProfileError("USC_ACADEMIC_GROUP_CODES debe ser una lista o texto separado")
    if len(values) > 30:
        raise AcademicProfileError("USC_ACADEMIC_GROUP_CODES admite como máximo 30 grupos")
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise AcademicProfileError("Cada grupo académico debe ser texto")
        value = raw.strip().upper().lstrip("/")
        if not _GROUP.fullmatch(value):
            raise AcademicProfileError("Cada grupo académico debe ser un código simple")
        if value not in result:
            result.append(value)
    return tuple(result)


def _date(value: object, name: str) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise AcademicProfileError(f"{name} debe tener formato YYYY-MM-DD")
    candidate = value.strip()
    try:
        parsed = date.fromisoformat(candidate)
    except ValueError:
        raise AcademicProfileError(f"{name} debe tener formato YYYY-MM-DD") from None
    # Evita aceptar extensiones de ISO (por ejemplo, un datetime) aunque el
    # parser de la biblioteca pudiera interpretarlas en otro contexto.
    if parsed.isoformat() != candidate:
        raise AcademicProfileError(f"{name} debe tener formato YYYY-MM-DD")
    return candidate


def _inferred_academic_year(today: date) -> str:
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}/{start + 1}"


def _inferred_semester(today: date) -> int:
    return 1 if today.month in {1, 9, 10, 11, 12} else 2


def _resolve_context(
    *,
    academic_year: str | None,
    semester: int | None,
    date_in_week: str | None,
    today: date | None = None,
) -> tuple[str, int, str | None]:
    current = today or madrid_today()
    if academic_year is None:
        resolved_year = _inferred_academic_year(current)
    else:
        try:
            resolved_year = normalise_academic_year(academic_year)
        except (TypeError, ValueError):
            raise AcademicProfileError(
                "academic_year debe tener formato consecutivo YYYY/YYYY"
            ) from None
    if semester is None:
        resolved_semester = _inferred_semester(current)
    else:
        resolved_semester = _positive_int(semester, "semester", maximum=2)
        if resolved_semester not in {1, 2}:
            raise AcademicProfileError("semester debe ser 1 o 2")
    return resolved_year, resolved_semester, _date(date_in_week, "date_in_week")


def resolve_academic_context(
    profile: AcademicProfile | None,
    *,
    academic_year: str | None = None,
    semester: int | None = None,
    date_in_week: str | None = None,
    today: date | None = None,
) -> tuple[str, int, str | None]:
    """Academic year, semester and week from explicit values, the profile, then today."""

    if profile is not None:
        return profile.resolve(
            academic_year=academic_year,
            semester=semester,
            date_in_week=date_in_week,
            today=today,
        )
    return _resolve_context(
        academic_year=academic_year, semester=semester, date_in_week=date_in_week, today=today
    )


@dataclass(frozen=True, slots=True)
class AcademicProfile:
    """Selección académica persistida localmente, sin secretos."""

    degree_url: str
    # Only the whole-course timetable (get_my_class_timetable) needs a course; students
    # with subjects from several courses or degrees leave it out.
    course_number: int | None = None
    program_id: int | None = None
    group_codes: tuple[str, ...] = ()
    academic_year: str | None = None
    semester: int | None = None
    date_in_week: str | None = None
    # Other degrees where the student also has subjects (SICUE, double degrees, electives
    # from another degree). They widen where subject codes are looked up first.
    extra_degree_urls: tuple[str, ...] = ()
    # Optional explicit subject codes, for students who do not sign in to Moodle.
    subject_codes: tuple[str, ...] = ()
    source: str = "environment"

    def __post_init__(self) -> None:
        object.__setattr__(self, "degree_url", _degree_url(self.degree_url))
        if self.course_number is not None:
            object.__setattr__(
                self,
                "course_number",
                _positive_int(self.course_number, "course_number", maximum=12),
            )
        extra = _text_list(self.extra_degree_urls, "extra_degree_urls", maximum=5)
        object.__setattr__(
            self,
            "extra_degree_urls",
            tuple(dict.fromkeys(_degree_url(url) for url in extra if url != self.degree_url)),
        )
        codes = _text_list(self.subject_codes, "subject_codes", maximum=40)
        try:
            normalised = tuple(dict.fromkeys(normalise_subject_code(code) for code in codes))
        except ValueError as exc:
            raise AcademicProfileError(f"subject_codes no es válido: {exc}") from None
        object.__setattr__(self, "subject_codes", normalised)
        object.__setattr__(
            self,
            "program_id",
            _optional_positive_int(self.program_id, "program_id", maximum=99_999_999),
        )
        object.__setattr__(self, "group_codes", _groups(self.group_codes))
        if self.academic_year is not None:
            try:
                year = normalise_academic_year(self.academic_year)
            except (TypeError, ValueError):
                raise AcademicProfileError(
                    "academic_year debe tener formato consecutivo YYYY/YYYY"
                ) from None
            object.__setattr__(self, "academic_year", year)
        if self.semester is not None:
            object.__setattr__(
                self, "semester", _positive_int(self.semester, "semester", maximum=2)
            )
            if self.semester not in {1, 2}:
                raise AcademicProfileError("semester debe ser 1 o 2")
        object.__setattr__(self, "date_in_week", _date(self.date_in_week, "date_in_week"))
        if not isinstance(self.source, str) or not self.source.strip():
            raise AcademicProfileError("source de perfil no válido")

    @property
    def degree_urls(self) -> tuple[str, ...]:
        """Main degree first, then the extra ones: where subject codes are looked up."""

        return (self.degree_url, *self.extra_degree_urls)

    def resolve(
        self,
        *,
        academic_year: str | None = None,
        semester: int | None = None,
        date_in_week: str | None = None,
        today: date | None = None,
    ) -> tuple[str, int, str | None]:
        """Resuelve contexto explícito, perfil y finalmente defaults acotados."""

        return _resolve_context(
            academic_year=academic_year if academic_year is not None else self.academic_year,
            semester=semester if semester is not None else self.semester,
            date_in_week=date_in_week if date_in_week is not None else self.date_in_week,
            today=today,
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "configured": True,
            "source": self.source,
            "degree_url": self.degree_url,
            "course_number": self.course_number,
            "program_id": self.program_id,
            "group_codes": list(self.group_codes),
            "academic_year": self.academic_year,
            "semester": self.semester,
            "date_in_week": self.date_in_week,
            "extra_degree_urls": list(self.extra_degree_urls),
            "subject_codes": list(self.subject_codes),
            "read_only": True,
        }


def _profile_file(path_value: str) -> dict[str, object]:
    path = Path(path_value).expanduser()
    try:
        if path.stat().st_size > _PROFILE_MAX_BYTES:
            raise AcademicProfileError("El perfil académico local supera 64 KiB")
        raw = path.read_text(encoding="utf-8")
    except AcademicProfileError:
        raise
    except (OSError, UnicodeError) as exc:
        raise AcademicProfileError("No se pudo leer USC_ACADEMIC_PROFILE_FILE") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AcademicProfileError("USC_ACADEMIC_PROFILE_FILE no contiene JSON válido") from exc
    if not isinstance(value, dict):
        raise AcademicProfileError("USC_ACADEMIC_PROFILE_FILE debe contener un objeto JSON")
    return value


def _first(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def load_academic_profile(env: Mapping[str, str] | None = None) -> AcademicProfile | None:
    """Carga un perfil de archivo local y/o variables explícitas de entorno."""

    values: dict[str, object] = {}
    source = "environment"
    environ = os.environ if env is None else env
    profile_file = _first(environ, _PROFILE_FILE_ENV)
    if not profile_file and env is None and default_profile_path().is_file():
        profile_file = str(default_profile_path())
    if profile_file:
        values.update(_profile_file(profile_file))
        source = "file"
        file_aliases = {
            "degree": "degree_url",
            "course": "course_number",
            "program": "program_id",
            "groups": "group_codes",
            "extra_degrees": "extra_degree_urls",
            "subjects": "subject_codes",
        }
        for old, new in file_aliases.items():
            if old in values:
                if new in values:
                    raise AcademicProfileError(f"El perfil académico duplica {new}")
                values[new] = values.pop(old)
    aliases = {
        "degree_url": (
            "USC_ACADEMIC_DEGREE_URL",
            "USC_ACADEMIC_PROFILE_DEGREE_URL",
            "USC_DEGREE_URL",
        ),
        "course_number": (
            "USC_ACADEMIC_COURSE_NUMBER",
            "USC_ACADEMIC_COURSE",
            "USC_ACADEMIC_PROFILE_COURSE_NUMBER",
            "USC_COURSE_NUMBER",
        ),
        "program_id": (
            "USC_ACADEMIC_PROGRAM_ID",
            "USC_ACADEMIC_PROGRAM",
            "USC_ACADEMIC_PROFILE_PROGRAM_ID",
            "USC_PROGRAM_ID",
        ),
        "group_codes": (
            "USC_ACADEMIC_GROUP_CODES",
            "USC_ACADEMIC_GROUPS",
            "USC_ACADEMIC_PROFILE_GROUP_CODES",
            "USC_GROUP_CODES",
        ),
        "academic_year": ("USC_ACADEMIC_YEAR",),
        "semester": ("USC_ACADEMIC_SEMESTER",),
        "date_in_week": ("USC_ACADEMIC_DATE_IN_WEEK",),
        "extra_degree_urls": ("USC_ACADEMIC_EXTRA_DEGREE_URLS",),
        "subject_codes": ("USC_ACADEMIC_SUBJECT_CODES",),
    }
    for key, names in aliases.items():
        value = _first(environ, *names)
        if value is not None:
            values[key] = value
    if not values:
        return None
    if "degree_url" not in values:
        raise AcademicProfileError("El perfil académico requiere degree_url explícito")
    allowed = {
        "degree_url",
        "course_number",
        "program_id",
        "group_codes",
        "academic_year",
        "semester",
        "date_in_week",
        "extra_degree_urls",
        "subject_codes",
    }
    unknown = set(values).difference(allowed)
    if unknown:
        raise AcademicProfileError("El perfil académico contiene claves no admitidas")
    try:
        return AcademicProfile(**values, source=source)
    except TypeError as exc:
        raise AcademicProfileError("El perfil académico contiene campos no válidos") from exc
