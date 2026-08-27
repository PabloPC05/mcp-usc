from __future__ import annotations

import asyncio
import json
import sys
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SUBJECTS: dict[str, dict[str, Any]] = {
    "G4012223": {"name": "Sistemas Operativos I", "degree": "informatics", "course": 2},
    "G4012227": {"name": "Sistemas Operativos II", "degree": "informatics", "course": 2},
    "G4012322": {"name": "Administración de Sistemas y Redes", "degree": "informatics", "course": 3},
    "G4012224": {"name": "Redes", "degree": "informatics", "course": 2},
    "G4012328": {"name": "Inteligencia Artificial", "degree": "informatics", "course": 3},
    "G4012455": {"name": "Aprendizaje Automático", "degree": "informatics", "course": 4},
    "G4012326": {"name": "Computación Distribuida", "degree": "informatics", "course": 3},
    "G4012329": {"name": "Seguridad de la Información", "degree": "informatics", "course": 4},
    "G4012421": {"name": "Interacción Persona-Ordenador", "degree": "informatics", "course": 4},
    "G1011449": {"name": "Ecuaciones Diferenciales", "degree": "math", "course": 4},
    "G1011442": {"name": "Variable Compleja", "degree": "math", "course": 3},
    "G1011132": {"name": "Ecuaciones Algebraicas", "degree": "math", "course": 3},
    "G1012226": {"name": "Geometría Lineal", "degree": "math", "course": 2},
}
ACADEMIC_YEAR = "2026/2027"
SEMESTER = 1


def fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def result_to_python(result: Any) -> Any:
    data = result.model_dump(mode="json", by_alias=True)
    structured = data.get("structuredContent") or data.get("structured_content")
    if isinstance(structured, dict) and structured:
        if set(structured) == {"result"}:
            return structured["result"]
        return structured
    for item in data.get("content", []):
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return data


class MCP:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        print(f"CALL {name} {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
        result = await self.session.call_tool(name, arguments=arguments)
        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            raise RuntimeError(f"MCP tool {name} returned an error: {result_to_python(result)!r}")
        return result_to_python(result)


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk(nested)


def collect_weeks(value: Any) -> list[dict[str, Any]]:
    found: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in walk(value):
        if not {"start_date", "end_date", "endpoint_url"}.issubset(item):
            continue
        key = (str(item["start_date"]), str(item["end_date"]), str(item["endpoint_url"]))
        found[key] = dict(item)
    return sorted(found.values(), key=lambda x: (str(x["start_date"]), str(x["endpoint_url"])))


def collect_sessions(value: Any) -> list[dict[str, Any]]:
    found: dict[tuple[Any, ...], dict[str, Any]] = {}
    required = {"date", "start_time", "end_time", "subject_name", "activity_type"}
    for item in walk(value):
        if not required.issubset(item):
            continue
        session = {
            "date": item.get("date"),
            "weekday": item.get("weekday"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "subject_name": item.get("subject_name"),
            "subject_url": item.get("subject_url"),
            "activity_type": item.get("activity_type"),
            "group_code": item.get("group_code"),
            "room": item.get("room"),
        }
        key = tuple(session.values())
        found[key] = session
    return sorted(found.values(), key=lambda x: (str(x["date"]), str(x["start_time"]), str(x["subject_name"]), str(x["group_code"])))


def collect_sources(value: Any) -> list[str]:
    sources: set[str] = set()
    for item in walk(value):
        for key, raw in item.items():
            if isinstance(raw, str) and (key.endswith("_url") or key in {"url", "source_url"}) and raw.startswith(("https://www.usc.gal/", "https://usc.gal/")):
                sources.add(raw)
    return sorted(sources)


def exact_degrees(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    degrees = catalog.get("degrees", [])
    informatics = [d for d in degrees if "grao en enxenaria informatica" in fold(str(d.get("name", ""))) and "dobre" not in fold(str(d.get("name", ""))) and "2a edicion" in fold(str(d.get("name", "")))]
    maths = [d for d in degrees if fold(str(d.get("name", ""))).strip() == "grao en matematicas"]
    if len(informatics) != 1 or len(maths) != 1:
        raise RuntimeError(f"Degree resolution failed: informatics={informatics!r}, math={maths!r}")
    return {"informatics": informatics[0], "math": maths[0]}


def parse_locations(located: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    output = {code: [] for code in SUBJECTS}
    for item in located.get("subjects", []):
        code = str(item.get("subject_code", ""))
        if code in output:
            output[code] = [dict(x) for x in item.get("locations", []) if isinstance(x, dict)]
    return output


def aliases_for(code: str, locations: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    names = {fold(str(SUBJECTS[code]["name"]))}
    urls: set[str] = set()
    for loc in locations:
        name = str(loc.get("subject_name") or "").strip()
        if name:
            names.add(fold(name))
        for key in ("subject_url",):
            url = str(loc.get(key) or "").strip()
            if url:
                urls.add(url.rstrip("/"))
        for url in loc.get("subject_urls", []) or []:
            if isinstance(url, str) and url:
                urls.add(url.rstrip("/"))
    return names, urls


def match_code(session: dict[str, Any], aliases: dict[str, tuple[set[str], set[str]]]) -> str | None:
    url = str(session.get("subject_url") or "").rstrip("/")
    name = fold(str(session.get("subject_name") or ""))
    by_url = [code for code, (_, urls) in aliases.items() if url and url in urls]
    if len(by_url) == 1:
        return by_url[0]
    by_name = [code for code, (names, _) in aliases.items() if name and name in names]
    if len(by_name) == 1:
        return by_name[0]
    return None


def program_ids(discovery: Any, course: int) -> list[int]:
    ids: set[int] = set()
    for item in walk(discovery):
        if item.get("course_number") != course or "program_id" not in item:
            continue
        try:
            ids.add(int(item["program_id"]))
        except (TypeError, ValueError):
            pass
    return sorted(ids)


async def fetch_program(mcp: MCP, degree_url: str, course: int, program_id: int) -> dict[str, Any]:
    base = {
        "degree_url": degree_url,
        "course_number": course,
        "academic_year": ACADEMIC_YEAR,
        "semester": SEMESTER,
        "program_id": program_id,
    }
    initial = await mcp.call("get_degree_class_timetable", base)
    weeks = collect_weeks(initial)
    responses = [initial]
    seen: set[str] = set()
    for week in weeks:
        start = str(week["start_date"])
        if start in seen:
            continue
        seen.add(start)
        args = dict(base)
        args["date_in_week"] = start
        responses.append(await mcp.call("get_degree_class_timetable", args))
    sessions = collect_sessions({"responses": responses})
    sources = collect_sources({"responses": responses})
    return {"program_id": program_id, "weeks": weeks, "sessions": sessions, "sources": sources}


async def fetch_degree_course(mcp: MCP, degree_url: str, course: int) -> dict[str, Any]:
    discovery = await mcp.call("list_degree_timetables", {"degree_url": degree_url, "course_number": course})
    ids = program_ids(discovery, course)
    programs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for pid in ids:
        try:
            programs.append(await fetch_program(mcp, degree_url, course, pid))
        except Exception as exc:
            errors.append({"program_id": pid, "error": type(exc).__name__, "message": str(exc)})
    return {"course": course, "program_ids": ids, "programs": programs, "errors": errors, "discovery_sources": collect_sources(discovery)}


def is_friday(session: dict[str, Any]) -> bool:
    try:
        return date.fromisoformat(str(session["date"])).weekday() == 4
    except (KeyError, TypeError, ValueError):
        weekday = fold(str(session.get("weekday") or ""))
        return any(x in weekday for x in ("viernes", "venres", "friday"))


def lab_signal(session: dict[str, Any]) -> dict[str, bool]:
    activity = fold(str(session.get("activity_type") or ""))
    room = fold(str(session.get("room") or ""))
    activity_hit = any(x in activity for x in ("laborator", "ordenador", "computer", "informatica", "practica"))
    room_hit = any(x in room for x in ("laborator", "aula informatica", "ordenador", "computer"))
    return {"activity": activity_hit, "room": room_hit}


def is_lab(session: dict[str, Any]) -> bool:
    signal = lab_signal(session)
    return signal["activity"] or signal["room"]


def overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.get("date") == b.get("date") and str(a.get("start_time")) < str(b.get("end_time")) and str(b.get("start_time")) < str(a.get("end_time"))


def make_options(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labs = [s for s in sessions if is_lab(s)]
    common = [s for s in labs if not str(s.get("group_code") or "").strip()]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in labs:
        group = str(s.get("group_code") or "").strip()
        if group:
            grouped[group].append(s)
    raw = [(group, common + group_sessions) for group, group_sessions in sorted(grouped.items())] if grouped else [("común", common)] if common else []
    output: list[dict[str, Any]] = []
    for group, group_sessions in raw:
        group_sessions = collect_sessions({"sessions": group_sessions})
        output.append({
            "group": group,
            "has_friday": any(is_friday(s) for s in group_sessions),
            "friday_sessions": [s for s in group_sessions if is_friday(s)],
            "sessions": group_sessions,
            "patterns": sorted({(str(s.get("weekday")), str(s.get("start_time")), str(s.get("end_time")), str(s.get("activity_type")), str(s.get("room"))) for s in group_sessions}),
        })
    return output


def solve(options_by_code: dict[str, list[dict[str, Any]]], limit: int = 50) -> list[dict[str, Any]]:
    constrained = {code: [o for o in opts if not o["has_friday"]] for code, opts in options_by_code.items() if opts}
    if any(not opts for opts in constrained.values()):
        return []
    order = sorted(constrained, key=lambda code: (len(constrained[code]), code))
    solutions: list[dict[str, Any]] = []

    def backtrack(i: int, chosen: dict[str, dict[str, Any]], occupied: list[tuple[str, dict[str, Any]]]) -> None:
        if len(solutions) >= limit:
            return
        if i == len(order):
            solutions.append({code: {"group": option["group"], "patterns": option["patterns"]} for code, option in sorted(chosen.items())})
            return
        code = order[i]
        for option in constrained[code]:
            if any(other_code != code and overlaps(session, other) for session in option["sessions"] for other_code, other in occupied):
                continue
            chosen[code] = option
            added = [(code, s) for s in option["sessions"]]
            occupied.extend(added)
            backtrack(i + 1, chosen, occupied)
            if added:
                del occupied[-len(added):]
            chosen.pop(code, None)

    backtrack(0, {}, [])
    return solutions


async def analyse(mcp: MCP) -> dict[str, Any]:
    catalog = await mcp.call("list_usc_degrees", {})
    degrees = exact_degrees(catalog)
    located = await mcp.call("locate_usc_subject_codes", {
        "subject_codes": list(SUBJECTS),
        "academic_year": ACADEMIC_YEAR,
        "degree_urls": [str(degrees["informatics"]["url"]), str(degrees["math"]["url"])],
        "concurrency": 4,
    })
    locations = parse_locations(located)
    aliases = {code: aliases_for(code, locations[code]) for code in SUBJECTS}

    schedules: dict[str, Any] = {}
    for degree_key, degree in degrees.items():
        courses = sorted({int(v["course"]) for v in SUBJECTS.values() if v["degree"] == degree_key})
        for course in courses:
            schedules[f"{degree_key}-{course}"] = await fetch_degree_course(mcp, str(degree["url"]), course)

    all_sessions: list[dict[str, Any]] = []
    official_sources: set[str] = set()
    schedule_errors: list[dict[str, Any]] = []
    for key, schedule in schedules.items():
        official_sources.update(schedule.get("discovery_sources", []))
        for error in schedule.get("errors", []):
            schedule_errors.append({"schedule": key, **error})
        for program in schedule.get("programs", []):
            all_sessions.extend(program.get("sessions", []))
            official_sources.update(program.get("sources", []))
    all_sessions = collect_sessions({"sessions": all_sessions})

    sessions_by_code = {code: [] for code in SUBJECTS}
    unmatched: list[dict[str, Any]] = []
    for session in all_sessions:
        code = match_code(session, aliases)
        if code:
            sessions_by_code[code].append(session)
        else:
            unmatched.append(session)
    sessions_by_code = {code: collect_sessions({"sessions": sessions}) for code, sessions in sessions_by_code.items()}
    options_by_code = {code: make_options(sessions) for code, sessions in sessions_by_code.items()}

    present = [code for code, sessions in sessions_by_code.items() if sessions]
    absent = [code for code in SUBJECTS if code not in present]
    forced_friday = [code for code in present if options_by_code[code] and all(o["has_friday"] for o in options_by_code[code])]
    no_labs = [code for code in present if not options_by_code[code]]
    solutions = solve(options_by_code)

    return {
        "academic_year": ACADEMIC_YEAR,
        "semester": SEMESTER,
        "degrees": degrees,
        "locations": locations,
        "present_subject_codes": present,
        "absent_subject_codes": absent,
        "sessions_by_code": sessions_by_code,
        "activity_types_by_code": {code: sorted({str(s.get("activity_type")) for s in sessions}) for code, sessions in sessions_by_code.items()},
        "rooms_by_code": {code: sorted({str(s.get("room")) for s in sessions}) for code, sessions in sessions_by_code.items()},
        "lab_options_by_code": options_by_code,
        "forced_friday_codes": forced_friday,
        "no_lab_codes": no_labs,
        "friday_free_solution_count_capped": len(solutions),
        "friday_free_solutions": solutions,
        "schedule_errors": schedule_errors,
        "official_sources": sorted(official_sources),
        "unmatched_sessions": unmatched,
        "schedules": schedules,
        "locate_result": located,
    }


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from mcp_usc.cli import main; main()"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await analyse(MCP(session))

    Path("usc-friday-labs-analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "academic_year": result["academic_year"],
        "semester": result["semester"],
        "present_subject_codes": result["present_subject_codes"],
        "absent_subject_codes": result["absent_subject_codes"],
        "activity_types_by_code": result["activity_types_by_code"],
        "forced_friday_codes": result["forced_friday_codes"],
        "no_lab_codes": result["no_lab_codes"],
        "friday_free_solution_count_capped": result["friday_free_solution_count_capped"],
        "first_solution": (result["friday_free_solutions"] or [None])[0],
        "schedule_errors": result["schedule_errors"],
        "official_sources": result["official_sources"],
        "lab_option_summary": {
            code: [
                {"group": option["group"], "has_friday": option["has_friday"], "patterns": option["patterns"], "friday_sessions": option["friday_sessions"]}
                for option in options
            ]
            for code, options in result["lab_options_by_code"].items()
        },
    }
    Path("usc-friday-labs-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("USC_FRIDAY_LABS_SUMMARY=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
