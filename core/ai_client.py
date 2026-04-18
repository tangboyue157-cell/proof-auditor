"""Unified AI API client for Proof Auditor.

Supports multiple providers through a single interface:
  - Anthropic (Claude)
  - OpenAI (GPT / codex-proxy)
  - Google (Gemini)
  - OpenRouter (200+ models)

Usage:
    from core.ai_client import AIClient

    client = AIClient(provider="anthropic")  # or "openai", "gemini", "openrouter"
    response = client.chat("Translate this proof to Lean 4: ...")

Environment variables:
    ANTHROPIC_API_KEY   — for Claude models
    OPENAI_API_KEY      — for GPT models
    GEMINI_API_KEY      — for Gemini models
    OPENROUTER_API_KEY  — for OpenRouter
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _load_dotenv():
    """Load .env file from project root if it exists."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                # Don't override existing env vars
                if key and key not in os.environ:
                    os.environ[key] = value


_load_dotenv()


# Default models per provider
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "gemini": "gemini-2.5-pro-preview-05-06",
    "openrouter": "anthropic/claude-sonnet-4",
}

# API key environment variable names
API_KEY_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

TIMEOUT = 300  # 5 minutes


@dataclass
class AIResponse:
    """Response from an AI provider."""
    content: str
    model: str
    provider: str
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0


@dataclass
class CostEntry:
    """A single API call cost record."""
    round_name: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class CostTracker:
    """Tracks cumulative token usage and latency across an audit session.

    Usage:
        tracker = CostTracker()
        tracker.set_round("R1_translation")
        # ... API calls automatically tracked ...
        print(tracker.summary())
    """

    def __init__(self):
        self.entries: list[CostEntry] = []
        self.current_round: str = "unknown"

    def set_round(self, name: str) -> None:
        """Set the current pipeline round for cost attribution."""
        self.current_round = name

    def record(self, response: AIResponse) -> None:
        """Record costs from an AI response."""
        usage = response.usage or {}
        self.entries.append(CostEntry(
            round_name=self.current_round,
            model=response.model,
            provider=response.provider,
            input_tokens=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            output_tokens=usage.get("output_tokens", usage.get("completion_tokens", 0)),
            latency_ms=response.latency_ms,
        ))

    @property
    def total_input_tokens(self) -> int:
        return sum(e.input_tokens for e in self.entries)

    @property
    def total_output_tokens(self) -> int:
        return sum(e.output_tokens for e in self.entries)

    @property
    def total_calls(self) -> int:
        return len(self.entries)

    @property
    def total_latency_s(self) -> float:
        return sum(e.latency_ms for e in self.entries) / 1000

    def per_round(self) -> dict[str, dict]:
        """Get cost breakdown per pipeline round."""
        rounds: dict[str, dict] = {}
        for e in self.entries:
            if e.round_name not in rounds:
                rounds[e.round_name] = {"calls": 0, "input_tokens": 0,
                                         "output_tokens": 0, "latency_ms": 0}
            r = rounds[e.round_name]
            r["calls"] += 1
            r["input_tokens"] += e.input_tokens
            r["output_tokens"] += e.output_tokens
            r["latency_ms"] += e.latency_ms
        return rounds

    def summary(self) -> str:
        """Human-readable cost summary."""
        lines = ["Cost Summary:"]
        lines.append(f"  Total API calls: {self.total_calls}")
        lines.append(f"  Total input tokens: {self.total_input_tokens:,}")
        lines.append(f"  Total output tokens: {self.total_output_tokens:,}")
        lines.append(f"  Total latency: {self.total_latency_s:.1f}s")
        lines.append("  Per-round breakdown:")
        for name, data in self.per_round().items():
            lines.append(
                f"    {name}: {data['calls']} calls, "
                f"{data['input_tokens']:,}+{data['output_tokens']:,} tokens, "
                f"{data['latency_ms']/1000:.1f}s"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize for JSON report."""
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_latency_s": round(self.total_latency_s, 1),
            "per_round": self.per_round(),
        }


# Global cost tracker (shared across all AI calls in one audit)
_global_tracker: Optional[CostTracker] = None


def get_cost_tracker() -> CostTracker:
    """Get or create the global cost tracker."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = CostTracker()
    return _global_tracker


def reset_cost_tracker() -> CostTracker:
    """Reset and return a fresh cost tracker."""
    global _global_tracker
    _global_tracker = CostTracker()
    return _global_tracker


def _post(url: str, headers: dict, body: dict) -> dict:
    """Send a POST request and return parsed JSON."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else ""
        raise RuntimeError(f"API error {e.code}: {detail}") from e


class AIClient:
    """Unified client for AI API calls.

    Example:
        client = AIClient(provider="anthropic")
        resp = client.chat("Prove that sqrt(2) is irrational.")
        print(resp.content)
    """

    def __init__(
        self,
        provider: str = "anthropic",
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ):
        if provider not in API_KEY_VARS:
            raise ValueError(f"Unknown provider: {provider}. Use: {list(API_KEY_VARS.keys())}")

        self.provider = provider
        self.model = model or DEFAULT_MODELS[provider]
        self.system_prompt = system_prompt or ""

        key_var = API_KEY_VARS[provider]
        self.api_key = os.environ.get(key_var, "")
        if not self.api_key:
            raise RuntimeError(
                f"API key not set. Please set {key_var} environment variable.\n"
                f"Example: export {key_var}='your-key-here'"
            )

    def chat(self, user_message: str, temperature: float = 0.3) -> AIResponse:
        """Send a chat message and get a response.

        Automatically tracks cost via the global CostTracker.

        Args:
            user_message: The user's message/prompt.
            temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative).

        Returns:
            AIResponse with the model's response.
        """
        dispatch = {
            "anthropic": self._call_anthropic,
            "openai": self._call_openai,
            "gemini": self._call_gemini,
            "openrouter": self._call_openrouter,
        }
        t0 = time.time()
        response = dispatch[self.provider](user_message, temperature)
        response.latency_ms = (time.time() - t0) * 1000

        # Track cost
        tracker = get_cost_tracker()
        tracker.record(response)

        return response

    def _call_anthropic(self, message: str, temperature: float) -> AIResponse:
        data = _post(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            {
                "model": self.model,
                "max_tokens": 8192,
                "temperature": temperature,
                "system": self.system_prompt,
                "messages": [{"role": "user", "content": message}],
            },
        )
        content = "".join(
            block["text"] for block in data.get("content", []) if block.get("type") == "text"
        )
        return AIResponse(
            content=content,
            model=self.model,
            provider="anthropic",
            usage=data.get("usage", {}),
        )

    def _call_openai(self, message: str, temperature: float) -> AIResponse:
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        data = _post(
            f"{base}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            {
                "model": self.model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": message},
                ],
            },
        )
        return AIResponse(
            content=data["choices"][0]["message"]["content"],
            model=self.model,
            provider="openai",
            usage=data.get("usage", {}),
        )

    def _call_gemini(self, message: str, temperature: float) -> AIResponse:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        data = _post(
            url,
            {"x-goog-api-key": self.api_key},
            {
                "system_instruction": {"parts": [{"text": self.system_prompt}]},
                "contents": [{"parts": [{"text": message}]}],
                "generationConfig": {"temperature": temperature},
            },
        )
        parts = data["candidates"][0]["content"]["parts"]
        content = "\n".join(p["text"] for p in parts if "text" in p)
        return AIResponse(
            content=content,
            model=self.model,
            provider="gemini",
            usage=data.get("usageMetadata", {}),
        )

    def _call_openrouter(self, message: str, temperature: float) -> AIResponse:
        data = _post(
            "https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            {
                "model": self.model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": message},
                ],
            },
        )
        return AIResponse(
            content=data["choices"][0]["message"]["content"],
            model=self.model,
            provider="openrouter",
            usage=data.get("usage", {}),
        )


def check_available_providers() -> dict[str, bool]:
    """Check which AI providers have API keys configured.

    Returns:
        Dict mapping provider name → whether API key is available.
    """
    return {
        provider: bool(os.environ.get(key_var, ""))
        for provider, key_var in API_KEY_VARS.items()
    }


if __name__ == "__main__":
    # Quick self-check
    print("=== Proof Auditor AI Client Status ===\n")
    providers = check_available_providers()
    for name, available in providers.items():
        status = "✅ Ready" if available else "❌ Not configured"
        key_var = API_KEY_VARS[name]
        print(f"  {name:12s} ({key_var}): {status}")

    available = [p for p, ok in providers.items() if ok]
    if not available:
        print("\n⚠️  No API keys configured. Set at least one:")
        print("   export ANTHROPIC_API_KEY='sk-ant-...'")
        print("   export OPENAI_API_KEY='sk-...'")
        print("   export GEMINI_API_KEY='AIza...'")
    else:
        print(f"\n✅ Ready to use: {', '.join(available)}")
