"""Tracing hooks.

This file intentionally avoids binding to one provider. Students can plug in LangSmith,
Langfuse, OpenTelemetry, or simple JSON traces.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any

from multi_agent_research_lab.core.config import get_settings

logger = logging.getLogger(__name__)

_langfuse_client: Any | None = None
_langfuse_ready = False


def _get_langfuse() -> Any | None:
    """Khởi tạo Langfuse client một lần. Trả về None nếu thiếu key hoặc SDK."""

    global _langfuse_client, _langfuse_ready
    if _langfuse_ready:
        return _langfuse_client

    _langfuse_ready = True
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.info("Langfuse chưa cấu hình, chỉ dùng local span.")
        return None
    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        logger.info("Langfuse tracing đã bật host=%s", settings.langfuse_host)
    except Exception as exc:  # noqa: BLE001 - tracing hỏng không được làm chết workflow
        logger.warning("Không khởi tạo được Langfuse (%s), fallback local span.", exc)
        _langfuse_client = None
    return _langfuse_client


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """Span đo thời gian, đồng thời đẩy lên Langfuse nếu provider khả dụng."""

    started = perf_counter()
    span: dict[str, Any] = {"name": name, "attributes": attributes or {}, "duration_seconds": None}
    client = _get_langfuse()
    ctx = None
    provider_span = None
    if client is not None:
        try:
            ctx = client.start_as_current_observation(name=name, input=attributes or {})
            provider_span = ctx.__enter__()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Không tạo được Langfuse span %s: %s", name, exc)
            ctx = None
    try:
        yield span
    finally:
        span["duration_seconds"] = perf_counter() - started
        if ctx is not None:
            try:
                if provider_span is not None:
                    provider_span.update(output=span["attributes"])
                ctx.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Không đóng được Langfuse span %s: %s", name, exc)


@contextmanager
def workflow_trace(name: str, query: str) -> Iterator[dict[str, Any]]:
    """Span gốc bao toàn bộ một lần chạy, ghi lại trace URL và flush khi kết thúc."""

    client = _get_langfuse()
    with trace_span(name, {"query": query}) as span:
        if client is not None:
            try:
                span["trace_url"] = client.get_trace_url()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Không lấy được Langfuse trace URL: %s", exc)
        yield span
    if client is not None:
        try:
            client.flush()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Langfuse flush lỗi: %s", exc)


def tracing_enabled() -> bool:
    """True khi Langfuse provider đang hoạt động."""

    return _get_langfuse() is not None
