import httpx
from app.core.config import settings


class SearchProvider:
    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        raise NotImplementedError


class MockSearchProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        return []


class TavilySearchProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        if not settings.tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY is not configured.")

        payload = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post("https://api.tavily.com/search", json=payload)
            r.raise_for_status()
            data = r.json()

        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0),
            }
            for item in data.get("results", [])
        ]


def get_search_provider() -> SearchProvider:
    if settings.search_provider.lower() == "tavily":
        return TavilySearchProvider()
    return MockSearchProvider()
