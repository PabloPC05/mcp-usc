from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CODES = ["G1011449", "G1011442", "G1011132", "G1012226"]
YEARS = ["2025/2026", "2024/2025", "2023/2024"]


def decode(result):
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


async def main():
    env = dict(os.environ)
    env["USC_HTTP_TIMEOUT"] = "12"
    params = StdioServerParameters(command=sys.executable, args=["-c", "from mcp_usc.cli import main; main()"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            cat = decode(await session.call_tool("list_usc_degrees", arguments={}))
            maths = [d for d in cat.get("degrees", []) if d.get("name") == "Grao en Matemáticas"]
            if len(maths) != 1:
                raise RuntimeError(f"Math degree ambiguous: {maths!r}")
            url = maths[0]["url"]
            out = {}
            for year in YEARS:
                r = await session.call_tool("locate_usc_subject_codes", arguments={"subject_codes": CODES, "academic_year": year, "degree_urls": [url], "concurrency": 2})
                out[year] = decode(r)
            print("OLD_MATH_CODE_MAP=" + json.dumps(out, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
