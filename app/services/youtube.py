import httpx
from app.core.config import settings


class YouTubeSearchService:
    BASE = "https://www.googleapis.com/youtube/v3/search"

    async def search(self, query: str, max_results: int = 10) -> list[dict]:
        if not settings.youtube_api_key:
            return []

        params = {
            "key": settings.youtube_api_key,
            "part": "snippet",
            "type": "video",
            "q": query,
            "maxResults": min(max_results, 50),
        }

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(self.BASE, params=params)
            r.raise_for_status()
            data = r.json()

        out = []
        for item in data.get("items", []):
            vid = item["id"]["videoId"]
            snippet = item["snippet"]
            out.append(
                {
                    "title": snippet["title"],
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "channel": snippet["channelTitle"],
                    "description": snippet.get("description", ""),
                    "published_at": snippet.get("publishedAt"),
                }
            )
        return out
