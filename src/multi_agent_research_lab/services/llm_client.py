"""LLM client abstraction.

Production note: agents should depend on this interface instead of importing an SDK directly.
"""

import logging
from dataclasses import dataclass

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from multi_agent_research_lab.core.config import Settings, get_settings
from multi_agent_research_lab.core.errors import AgentExecutionError

logger = logging.getLogger(__name__)

# Giá gpt-4o-mini (USD / 1M token). Đổi bảng này nếu dùng model khác.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.150, 0.600),
    "gpt-4o": (2.500, 10.000),
    "gpt-4.1-mini": (0.400, 1.600),
}
_DEFAULT_PRICING = (0.150, 0.600)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Ước lượng chi phí một lần gọi model dựa trên bảng giá tĩnh."""

    price_in, price_out = PRICING_USD_PER_MTOK.get(model, _DEFAULT_PRICING)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


@dataclass(frozen=True)
class LLMResponse:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class LLMClient:
    """OpenAI-backed LLM client. Retry/timeout/token logging sống ở đây, không ở agents."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.openai_api_key:
            raise AgentExecutionError("OPENAI_API_KEY chưa được cấu hình trong .env")
        self._client = OpenAI(
            api_key=self._settings.openai_api_key,
            timeout=float(self._settings.timeout_seconds),
        )

    @property
    def model(self) -> str:
        return self._settings.openai_model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Gọi model và trả về completion kèm token usage + cost ước lượng."""

        response = self._client.chat.completions.create(
            model=self._settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else None
        output_tokens = usage.completion_tokens if usage else None
        cost = (
            estimate_cost_usd(self._settings.openai_model, input_tokens, output_tokens)
            if input_tokens is not None and output_tokens is not None
            else None
        )
        logger.info(
            "llm_call model=%s in_tokens=%s out_tokens=%s cost_usd=%s",
            self._settings.openai_model,
            input_tokens,
            output_tokens,
            f"{cost:.6f}" if cost is not None else "n/a",
        )
        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
