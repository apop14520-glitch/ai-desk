"""Provider adapters: three heads behind one interface.

Each adapter returns a GenResult so the orchestrator never has to know which
SDK produced an answer. Phase 1 only needs generate(); streaming and health
checks come later, when something actually consumes them.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict, field

# Price per million tokens (input, output). Update from the provider dashboards
# and bump CHECKED_ON — stale prices silently corrupt every cost number below.
CHECKED_ON = "2026-09-23"
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gemini-3.8-flash": (0.75, 3.75),
}


def price(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Cost in USD, or None when the model is missing from PRICES.

    None is deliberate: a silent 0.0 would read as "this call was free".
    """
    rates = PRICES.get(model)
    if rates is None:
        return None
    return (tokens_in * rates[0] + tokens_out * rates[1]) / 1_000_000


@dataclass
class GenResult:
    head: str  # logical head: "openai" | "anthropic" | "google"
    model: str
    via: str  # how we reached it, e.g. "api" or "agent_sdk" — fallback audit trail
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Provider:
    """Base adapter. Subclasses implement _call()."""

    head: str
    model: str
    via: str = "api"
    max_tokens: int = 4000
    timeout_s: float = 120.0

    async def generate(self, prompt: str) -> GenResult:
        """Never raises: a failed head must not take the council down with it."""
        started = time.perf_counter()
        result = GenResult(head=self.head, model=self.model, via=self.via)
        try:
            text, tokens_in, tokens_out = await self._call(prompt)
            result.text = text
            result.tokens_in = tokens_in
            result.tokens_out = tokens_out
            result.cost_usd = price(self.model, tokens_in, tokens_out)
        except Exception as exc:
            # Type + message only. Exception args can carry request bodies, and
            # those can carry the key.
            result.error = f"{type(exc).__name__}: {exc}"
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    async def _call(self, prompt: str) -> tuple[str, int, int]:
        raise NotImplementedError


@dataclass
class OpenAIProvider(Provider):
    head: str = "openai"
    model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.4-mini"))

    async def _call(self, prompt: str) -> tuple[str, int, int]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(timeout=self.timeout_s)
        response = await client.responses.create(
            model=self.model,
            input=prompt,
            max_output_tokens=self.max_tokens,
        )
        usage = response.usage
        return (
            response.output_text,
            getattr(usage, "input_tokens", 0),
            getattr(usage, "output_tokens", 0),
        )


@dataclass
class GeminiProvider(Provider):
    head: str = "google"
    model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))

    async def _call(self, prompt: str) -> tuple[str, int, int]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        response = await client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=self.max_tokens),
        )
        usage = response.usage_metadata
        return (
            response.text or "",
            getattr(usage, "prompt_token_count", 0) or 0,
            getattr(usage, "candidates_token_count", 0) or 0,
        )


@dataclass
class ClaudeAPIProvider(Provider):
    """Claude over the Anthropic API — billed per token, always available."""

    head: str = "anthropic"
    model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5"))

    async def _call(self, prompt: str) -> tuple[str, int, int]:
        import anthropic

        client = anthropic.AsyncAnthropic(timeout=self.timeout_s)
        response = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # Short council answers don't need deep reasoning, and low effort
            # keeps the three heads comparable on latency.
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return text, response.usage.input_tokens, response.usage.output_tokens


@dataclass
class ClaudeAgentSDKProvider(Provider):
    """Claude over the Agent SDK, drawing on the Claude plan's monthly credit.

    Tools are disabled and settings are not loaded from disk: we want a plain
    completion, not a coding agent with filesystem access.
    """

    head: str = "anthropic"
    via: str = "agent_sdk"
    model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5"))

    async def _call(self, prompt: str) -> tuple[str, int, int]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            model=self.model,
            allowed_tools=[],
            max_turns=1,
            setting_sources=[],
        )

        parts: list[str] = []
        tokens_in = tokens_out = 0
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                parts += [b.text for b in message.content if isinstance(b, TextBlock)]
            elif isinstance(message, ResultMessage):
                # The usage shape here is less stable than the REST API's, so
                # read it defensively rather than trusting a fixed path.
                usage = getattr(message, "usage", None) or {}
                if not isinstance(usage, dict):
                    usage = getattr(usage, "__dict__", {})
                tokens_in = usage.get("input_tokens", 0) or 0
                tokens_out = usage.get("output_tokens", 0) or 0
        return "".join(parts), tokens_in, tokens_out


def anthropic_provider() -> Provider:
    """Prefer the subscription credit; fall back to the metered API.

    This is the fallback chain from the architecture, proven at the smallest
    possible scale before anything depends on it.
    """
    if os.getenv("ANTHROPIC_VIA", "agent_sdk") == "agent_sdk":
        try:
            import claude_agent_sdk  # noqa: F401

            return ClaudeAgentSDKProvider()
        except ImportError:
            pass
    return ClaudeAPIProvider()


def build_heads() -> list[Provider]:
    return [OpenAIProvider(), anthropic_provider(), GeminiProvider()]
