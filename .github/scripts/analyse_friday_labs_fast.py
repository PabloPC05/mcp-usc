from __future__ import annotations

import asyncio
import json
import os
import sys
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

YEAR = "2026/2027"
SUBJECTS = {
    "G4012223": ("Sistemas Operativos I", "informatics", 2),
    "G4012227": ("Sistemas Operativos II", "informatics", 2),
    "G4012322": ("Administración de Sistemas y Redes", "informatics", 3),
    "G4012224": ("Redes", "informatics", 2),
    "G4012328": ("Inteligencia Artificial", "informatics", 3),
    "G4012455": ("Aprendizaje Automático", "informatics", 4),
    "G4012326": ("Computación Distribuida", "informatics", 3),
    "G4012329": ("Seguridad de la Información", "informatics", 4),
    "G4012421": ("Interacción Persona-Ordenador", "informatics", 4),
    "G1011449": ("Ecuaciones Diferenciales", "math", 4),
    "G1011442": ("Variable Compleja", "math", 3),
    "G1011132": ("Ecuaciones Algebraicas", "math", 3),
    "G1012226": ("Geometría Lineal", "math", 2),
}


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))


def decode(result: Any) -> Any:
    raw = result.model_dump(mode="json", by_alias=True)
    structured = raw.get("structuredContent") or raw.get("structured_content")
    if isinstance(structured, dict) and structured:
        return structured.get("result", structured)
    for item in raw.get("content", []):
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            try:
                return json.loads(item["text"])
            except json.JSONDecodeError:
                return item["text"]
    return raw


class MCP:
    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self.sem = asyncio.Semaphore(6)

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        async with self.sem:
            r = await self.session.call_tool(name, arguments=args)
        if getattr(r, "isError", False) or getattr(r, "is_error", False):
            raise RuntimeError(f"{name}: {decode(r)!r}")
        return decode(r)


def walk(x: Any):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)


def sessions(x: Any) -> list[dict[str, Any]]:
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    need = {"date", "start_time", "end_time", "subject_name", "activity_type"}
    for d in walk(x):
        if need.issubset(d):
            row = {k: d.get(k) for k in ("date", "weekday", "start_time", "end_time", "subject_name", "subject_url", "activity_type", "group_code", "room")}
            out[tuple(row.values())] = row
    return sorted(out.values(), key=lambda r: (str(r["date"]), str(r["start_time"]), str(r["subject_name"]), str(r["group_code"])))


def weeks(x: Any) -> list[str]:
    return sorted({str(d["start_date"]) for d in walk(x) if {"start_date", "end_date", "endpoint_url"}.issubset(d)})


def sources(x: Any) -> list[str]:
    out = set()
    for d in walk(x):
        for k, v in d.items():
            if isinstance(v, str) and (k.endswith("_url") or k in {"url", "source_url"}) and v.startswith(("https://www.usc.gal/", "https://usc.gal/")):
                out.add(v)
    return sorted(out)


def degree_pages(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ds = catalog.get("degrees", [])
    inf = [d for d in ds if "grao en enxenaria informatica" in fold(str(d.get("name", ""))) and "dobre" not in fold(str(d.get("name", ""))) and "2a edicion" in fold(str(d.get("name", "")))]
    mat = [d for d in ds if fold(str(d.get("name", ""))).strip() == "grao en matematicas"]
    if len(inf) != 1 or len(mat) != 1:
        raise RuntimeError(f"degree pages ambiguous: {inf!r} {mat!r}")
    return {"informatics": inf[0], "math": mat[0]}


def locate_map(located: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out = {c: [] for c in SUBJECTS}
    for row in located.get("subjects", []):
        c = str(row.get("subject_code", ""))
        if c in out:
            out[c] = [dict(v) for v in row.get("locations", []) if isinstance(v, dict)]
    return out


def aliases(code: str, locs: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    names = {fold(SUBJECTS[code][0])}
    urls = set()
    for loc in locs:
        n = str(loc.get("subject_name") or "").strip()
        if n:
            names.add(fold(n))
        u = str(loc.get("subject_url") or "").strip()
        if u:
            urls.add(u.rstrip("/"))
        for u2 in loc.get("subject_urls", []) or []:
            if isinstance(u2, str) and u2:
                urls.add(u2.rstrip("/"))
    return names, urls


def match(row: dict[str, Any], amap: dict[str, tuple[set[str], set[str]]]) -> str | None:
    u = str(row.get("subject_url") or "").rstrip("/")
    n = fold(str(row.get("subject_name") or ""))
    by_u = [c for c, (_, us) in amap.items() if u and u in us]
    if len(by_u) == 1:
        return by_u[0]
    by_n = [c for c, (ns, _) in amap.items() if n and n in ns]
    return by_n[0] if len(by_n) == 1 else None


def pids(discovery: Any, course: int) -> list[int]:
    out = set()
    for d in walk(discovery):
        if d.get("course_number") == course and "program_id" in d:
            try:
                out.add(int(d["program_id"]))
            except (TypeError, ValueError):
                pass
    return sorted(out)


async def one_program(mcp: MCP, degree_url: str, course: int, pid: int) -> dict[str, Any]:
    base = {"degree_url": degree_url, "course_number": course, "academic_year": YEAR, "semester": 1, "program_id": pid}
    first = await mcp.call("get_degree_class_timetable", base)
    dates = weeks(first)

    async def fetch_week(d: str) -> Any:
        a = dict(base)
        a["date_in_week"] = d
        try:
            return await mcp.call("get_degree_class_timetable", a)
        except Exception as e:
            return {"_error": type(e).__name__, "_message": str(e), "date_in_week": d}

    rest = await asyncio.gather(*(fetch_week(d) for d in dates))
    bundle = [first, *rest]
    return {"program_id": pid, "week_dates": dates, "sessions": sessions(bundle), "sources": sources(bundle), "week_errors": [r for r in rest if isinstance(r, dict) and "_error" in r]}


async def one_course(mcp: MCP, degree_url: str, course: int) -> dict[str, Any]:
    disc = await mcp.call("list_degree_timetables", {"degree_url": degree_url, "course_number": course})
    ids = pids(disc, course)
    results = await asyncio.gather(*(one_program(mcp, degree_url, course, pid) for pid in ids), return_exceptions=True)
    programs, errors = [], []
    for pid, r in zip(ids, results):
        if isinstance(r, BaseException):
            errors.append({"program_id": pid, "error": type(r).__name__, "message": str(r)})
        else:
            programs.append(r)
    return {"course": course, "program_ids": ids, "programs": programs, "errors": errors, "sources": sources(disc)}


def friday(r: dict[str, Any]) -> bool:
    try:
        return date.fromisoformat(str(r["date"])).weekday() == 4
    except Exception:
        return any(x in fold(str(r.get("weekday") or "")) for x in ("viernes", "venres", "friday"))


def lab(r: dict[str, Any]) -> bool:
    a, room = fold(str(r.get("activity_type") or "")), fold(str(r.get("room") or ""))
    return any(x in a for x in ("laborator", "ordenador", "computer", "informatica", "practica")) or any(x in room for x in ("laborator", "aula informatica", "ordenador", "computer"))


def options(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ls = [r for r in rows if lab(r)]
    common = [r for r in ls if not str(r.get("group_code") or "").strip()]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in ls:
        g = str(r.get("group_code") or "").strip()
        if g:
            groups[g].append(r)
    raw = [(g, common + rs) for g, rs in sorted(groups.items())] if groups else [("común", common)] if common else []
    out = []
    for g, rs in raw:
        rs = sessions(rs)
        out.append({"group": g, "has_friday": any(friday(r) for r in rs), "friday_sessions": [r for r in rs if friday(r)], "sessions": rs, "patterns": sorted({(str(r.get("weekday")), str(r.get("start_time")), str(r.get("end_time")), str(r.get("activity_type")), str(r.get("room"))) for r in rs})})
    return out


def overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.get("date") == b.get("date") and str(a.get("start_time")) < str(b.get("end_time")) and str(b.get("start_time")) < str(a.get("end_time"))


def solve(by_code: dict[str, list[dict[str, Any]]], limit: int = 100) -> list[dict[str, Any]]:
    usable = {c: [o for o in os_ if not o["has_friday"]] for c, os_ in by_code.items() if os_}
    if any(not v for v in usable.values()):
        return []
    order = sorted(usable, key=lambda c: (len(usable[c]), c))
    ans: list[dict[str, Any]] = []
    def rec(i: int, chosen: dict[str, dict[str, Any]], occ: list[tuple[str, dict[str, Any]]]) -> None:
        if len(ans) >= limit:
            return
        if i == len(order):
            ans.append({c: {"group": o["group"], "patterns": o["patterns"]} for c, o in sorted(chosen.items())})
            return
        c = order[i]
        for o in usable[c]:
            if any(oc != c and overlap(r, rr) for r in o["sessions"] for oc, rr in occ):
                continue
            chosen[c] = o
            added = [(c, r) for r in o["sessions"]]
            occ.extend(added)
            rec(i + 1, chosen, occ)
            if added:
                del occ[-len(added):]
            chosen.pop(c, None)
    rec(0, {}, [])
    return ans


async def run(mcp: MCP) -> dict[str, Any]:
    catalog = await mcp.call("list_usc_degrees", {})
    deg = degree_pages(catalog)
    loc = await mcp.call("locate_usc_subject_codes", {"subject_codes": list(SUBJECTS), "academic_year": YEAR, "degree_urls": [deg["informatics"]["url"], deg["math"]["url"]], "concurrency": 4})
    lmap = locate_map(loc)
    amap = {c: aliases(c, lmap[c]) for c in SUBJECTS}

    course_jobs = []
    labels = []
    for dk, d in deg.items():
        for course in sorted({v[2] for v in SUBJECTS.values() if v[1] == dk}):
            labels.append(f"{dk}-{course}")
            course_jobs.append(one_course(mcp, d["url"], course))
    course_results = await asyncio.gather(*course_jobs, return_exceptions=True)
    schedules = {}
    for label, r in zip(labels, course_results):
        schedules[label] = {"error": type(r).__name__, "message": str(r)} if isinstance(r, BaseException) else r

    all_rows = []
    official = set()
    errs = []
    for label, sc in schedules.items():
        if "error" in sc and "programs" not in sc:
            errs.append({"schedule": label, **sc})
            continue
        official.update(sc.get("sources", []))
        for e in sc.get("errors", []):
            errs.append({"schedule": label, **e})
        for p in sc.get("programs", []):
            all_rows.extend(p.get("sessions", []))
            official.update(p.get("sources", []))
            for e in p.get("week_errors", []):
                errs.append({"schedule": label, "program_id": p.get("program_id"), **e})
    all_rows = sessions(all_rows)

    by_code = {c: [] for c in SUBJECTS}
    unmatched = []
    for r in all_rows:
        c = match(r, amap)
        (by_code[c] if c else unmatched).append(r)
    by_code = {c: sessions(rs) for c, rs in by_code.items()}
    opts = {c: options(rs) for c, rs in by_code.items()}
    sol = solve(opts)
    present = [c for c, rs in by_code.items() if rs]
    return {
        "year": YEAR,
        "degree_pages": deg,
        "locations": lmap,
        "present": present,
        "absent": [c for c in SUBJECTS if c not in present],
        "activity_types": {c: sorted({str(r.get("activity_type")) for r in rs}) for c, rs in by_code.items()},
        "rooms": {c: sorted({str(r.get("room")) for r in rs}) for c, rs in by_code.items()},
        "lab_options": opts,
        "forced_friday": [c for c in present if opts[c] and all(o["has_friday"] for o in opts[c])],
        "no_labs": [c for c in present if not opts[c]],
        "solution_count_capped": len(sol),
        "solutions": sol,
        "sessions_by_code": by_code,
        "errors": errs,
        "official_sources": sorted(official),
        "unmatched": unmatched,
        "schedules": schedules,
        "locate_result": loc,
    }


async def main() -> None:
    env = dict(os.environ)
    env["USC_HTTP_TIMEOUT"] = "12"
    params = StdioServerParameters(command=sys.executable, args=["-c", "from mcp_usc.cli import main; main()"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await run(MCP(session))
    Path("usc-friday-labs-fast.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    summary = {k: result[k] for k in ("year", "present", "absent", "activity_types", "rooms", "forced_friday", "no_labs", "solution_count_capped", "errors", "official_sources")}
    summary["lab_options"] = {c: [{"group": o["group"], "has_friday": o["has_friday"], "patterns": o["patterns"], "friday_sessions": o["friday_sessions"]} for o in os_] for c, os_ in result["lab_options"].items()}
    summary["first_solution"] = (result["solutions"] or [None])[0]
    Path("usc-friday-labs-fast-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("USC_FAST_SUMMARY=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
