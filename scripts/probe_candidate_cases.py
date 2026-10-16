"""Cheap probe of candidate cases for the pilot film (Part 10, item 5): how
much public material each one has. Brave only (plus the free Wikimedia Commons
file search for rights-known pictures). Writes numbers, never page text.

    python scripts/probe_candidate_cases.py "Lizzie Borden" "Hinterkaifeck" ...
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.documentary.visuals.research import brave_backend  # noqa: E402
from app.research_engine.search import SearchError  # noqa: E402


async def commons_count(client: httpx.AsyncClient, title: str) -> int:
    r = await client.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "list": "search", "srsearch": f"{title} filetype:bitmap",
        "srnamespace": 6, "srlimit": 50})
    return len((r.json().get("query") or {}).get("search") or [])


async def probe(title: str) -> dict:
    brave = brave_backend()
    out = {"case": title}
    domains: set[str] = set()
    for q in (f"{title} case", f"{title} murder trial evidence", f"{title} documentary history"):
        try:
            for r in await brave.search(q, "en", limit=20):
                domains.add(urlparse(r.url).hostname or "")
        except SearchError:
            pass
        await asyncio.sleep(1.1)
    out["web_domains"] = len(domains)
    try:
        yt = await brave.search(f"site:youtube.com {title} documentary", "en", limit=20)
        out["youtube_results"] = len(yt)
    except SearchError:
        out["youtube_results"] = 0
    await asyncio.sleep(1.1)
    for kind in ("images", "videos"):
        try:
            out[f"brave_{kind}"] = len(await brave.search_media(kind, title, "en", 20))
        except SearchError:
            out[f"brave_{kind}"] = 0
        await asyncio.sleep(1.1)
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "TrueCrimeStudio-probe/1.0"}) as client:
        out["commons_files"] = await commons_count(client, title)
    return out


async def main(titles: list[str]) -> None:
    print("| case | distinct web domains | YouTube results | Brave images | Brave videos | Commons files |")
    print("|---|---|---|---|---|---|")
    for t in titles:
        r = await probe(t)
        print(f"| {r['case']} | {r['web_domains']} | {r['youtube_results']} | {r['brave_images']} "
              f"| {r['brave_videos']} | {r['commons_files']} |")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
