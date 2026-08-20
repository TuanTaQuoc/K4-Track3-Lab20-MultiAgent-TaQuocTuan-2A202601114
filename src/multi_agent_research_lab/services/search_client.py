"""Search client abstraction for ResearcherAgent."""

import logging

from multi_agent_research_lab.core.config import Settings, get_settings
from multi_agent_research_lab.core.schemas import SourceDocument

logger = logging.getLogger(__name__)


class SearchClient:
    """Tavily-backed search client, có fallback mock khi thiếu API key."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = None
        if self._settings.tavily_api_key:
            try:
                from tavily import TavilyClient  # type: ignore[import-untyped]

                self._client = TavilyClient(api_key=self._settings.tavily_api_key)
            except ImportError:
                logger.warning("tavily-python chưa cài, SearchClient sẽ dùng mock source.")

    def search(self, query: str, max_results: int = 5) -> list[SourceDocument]:
        """Trả về các document liên quan tới query.

        Dùng Tavily khi có key; nếu không có key hoặc provider lỗi thì fallback sang
        mock source để workflow vẫn chạy được offline (guardrail cho lab).
        """

        if self._client is None:
            return self._mock_sources(query, max_results)

        try:
            payload = self._client.search(
                query=query,
                max_results=max_results,
                search_depth="advanced",
            )
        except Exception as exc:  # noqa: BLE001 - fallback có chủ đích, không để agent chết
            logger.warning("Tavily search thất bại (%s), fallback sang mock source.", exc)
            return self._mock_sources(query, max_results)

        documents: list[SourceDocument] = []
        for item in payload.get("results", [])[:max_results]:
            snippet = (item.get("content") or "").strip()
            if not snippet:
                continue  # source filtering: bỏ kết quả rỗng
            documents.append(
                SourceDocument(
                    title=item.get("title") or "Untitled source",
                    url=item.get("url"),
                    snippet=snippet,
                    metadata={"score": item.get("score"), "provider": "tavily"},
                )
            )
        logger.info("search query=%r results=%d provider=tavily", query, len(documents))
        return documents or self._mock_sources(query, max_results)

    @staticmethod
    def _mock_sources(query: str, max_results: int) -> list[SourceDocument]:
        """Nguồn giả lập để lab chạy được khi không có search provider."""

        logger.info("search query=%r provider=mock", query)
        return [
            SourceDocument(
                title=f"[MOCK] Overview of: {query}",
                url=None,
                snippet=(
                    f"Đây là nguồn mock cho truy vấn {query!r}. "
                    "Không có search provider thật nên nội dung này chỉ để giữ workflow chạy. "
                    "Mọi kết luận rút ra từ đây phải được đánh dấu là chưa kiểm chứng."
                ),
                metadata={"provider": "mock", "verified": False},
            )
        ][:max_results]
