"""Optional AI Copilot provider layer.

Helios remains deterministic: analytics compute in the engine, while AI can only
explain, critique, and draft from a sanitized Helios payload.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ._common import dedupe as _dedupe

SCHEMA_KEYS = (
    "summary",
    "key_points",
    "risks",
    "what_would_invalidate",
    "stance",
    "advisor_language",
    "compliance_caveats",
    "used_numbers",
    "missing_information",
    "data_quality_statement",
    "provider",
    "model",
    "generated_at",
)
VALID_PROVIDERS = {"none", "local", "anthropic", "openai", "dual", "hybrid"}
VALID_LOCAL_BACKENDS = {"ollama", "openai_compatible"}
FORBIDDEN_PHRASES = (
    "guaranteed",
    "risk-free",
    "certain profit",
    "will make money",
    "safe investment",
    "no downside",
)
BLOCKED_KEYS = {
    "raw",
    "file",
    "files",
    "csv",
    "bytes",
    "content",
    "upload",
    "price_history",
    "history",
    "series",
    "dates",
    "close",
    "open",
    "high",
    "low",
    "volume",
    "strategy_curve",
    "benchmark_curve",
    "drawdown_curve",
    "rolling_sharpe_curve",
}
NAME_KEYS = {"name", "display_name", "client_name", "model_name", "title"}
# Keys whose ticker->number dict values carry portfolio composition (holdings-equivalent).
COMPOSITION_WEIGHT_KEYS = {"weights", "allocations", "composition", "target_weights", "holdings"}
_TICKER_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")
_ACTION_UPGRADE_RE = re.compile(
    r"\b(?:strong buy|buy|accumulate|overweight|load(?:ing)? up"
    r"|add(?:ing)?\s+(?:\w+\s+){0,2}exposure"
    r"|increas(?:e|ing)\s+(?:\w+\s+){0,2}(?:position|allocation|exposure|weight|stake|holding)s?)\b"
)
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_LOCAL_MODEL = ""
MAX_LIST_ITEMS = 16
MAX_STRING_LEN = 800
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?%?")
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class AIError(Exception):
    status_code = 503

    def __init__(self, message: str, status: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status or {}


class AIUnavailableError(AIError):
    pass


class AITimeoutError(AIError):
    pass


class AIProviderError(AIError):
    pass


@dataclass
class AIConfig:
    enabled: bool
    provider: str
    local_backend: str
    local_base_url: str
    local_model: str
    timeout_s: float
    local_require_localhost: bool
    anthropic_key_present: bool
    anthropic_model: str
    openai_key_present: bool
    openai_model: str
    redact_client_names: bool
    send_holdings: bool
    cache_ttl_s: int

    @classmethod
    def from_env(cls) -> "AIConfig":
        provider = _env("HELIOS_AI_PROVIDER", "none").lower()
        local_backend = _env("HELIOS_LOCAL_AI_BACKEND", "ollama").lower()
        return cls(
            enabled=_truthy(_env("HELIOS_AI_ENABLED", "0")),
            provider=provider if provider in VALID_PROVIDERS else "none",
            local_backend=local_backend if local_backend in VALID_LOCAL_BACKENDS else "ollama",
            local_base_url=_env("HELIOS_LOCAL_AI_BASE_URL", "http://127.0.0.1:11434"),
            local_model=_env("HELIOS_LOCAL_AI_MODEL", DEFAULT_LOCAL_MODEL),
            timeout_s=_safe_float(_env("HELIOS_LOCAL_AI_TIMEOUT_S", "45"), 45.0, 1.0, 120.0),
            local_require_localhost=_truthy(_env("HELIOS_LOCAL_AI_REQUIRE_LOCALHOST", "1")),
            anthropic_key_present=bool(os.environ.get("ANTHROPIC_API_KEY")),
            anthropic_model=_env("HELIOS_AI_MODEL_ANTHROPIC", DEFAULT_ANTHROPIC_MODEL),
            openai_key_present=bool(os.environ.get("OPENAI_API_KEY")),
            openai_model=_env("HELIOS_AI_MODEL_OPENAI", DEFAULT_OPENAI_MODEL),
            redact_client_names=_truthy(_env("HELIOS_AI_REDACT_CLIENT_NAMES", "1")),
            send_holdings=_truthy(_env("HELIOS_AI_SEND_HOLDINGS", "0")),
            cache_ttl_s=int(_safe_float(_env("HELIOS_AI_CACHE_TTL_S", "900"), 900, 0, 86_400)),
        )


class AIProvider:
    provider = "base"
    mode = "none"

    def __init__(self, config: AIConfig):
        self.config = config
        self.model = ""

    def status(self) -> dict[str, Any]:
        return _status(
            provider=self.provider,
            mode=self.mode,
            model=self.model,
            enabled=self.config.enabled,
            available=False,
            reason="Provider is not implemented.",
        )

    def explain_opportunity(self, payload: dict[str, Any], regenerate: bool = False) -> dict[str, Any]:
        return self._run("opportunity_explain", payload, regenerate=regenerate)

    def critique_opportunity(self, payload: dict[str, Any], regenerate: bool = False) -> dict[str, Any]:
        return self._run("opportunity_critique", payload, regenerate=regenerate)

    def summarize_strategy(self, payload: dict[str, Any], regenerate: bool = False) -> dict[str, Any]:
        return self._run("strategy_summary", payload, regenerate=regenerate)

    def summarize_portfolio_clinic(self, payload: dict[str, Any], regenerate: bool = False) -> dict[str, Any]:
        return self._run("clinic_summary", payload, regenerate=regenerate)

    def write_advisor_report(self, payload: dict[str, Any], regenerate: bool = False) -> dict[str, Any]:
        return self._run("report_narrative", payload, regenerate=regenerate)

    def answer_question(self, payload: dict[str, Any], question: str, regenerate: bool = False) -> dict[str, Any]:
        return self._run("question", payload, question=question, regenerate=regenerate)

    def chat(self, messages: list, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Multi-turn research dialogue over a sanitized Helios context.

        Free-form prose (not the JSON task schema) so the operator can argue
        back and forth with the copilot. Providers without dialogue support
        raise honestly rather than degrading to one-shot answers.
        """
        st = self.status()
        if not st.get("available"):
            raise AIUnavailableError(st.get("reason") or "AI provider unavailable.", st)
        raise AIProviderError(
            "Dialogue mode requires the Anthropic (Claude) provider.", st)

    def _run(
        self,
        task: str,
        payload: dict[str, Any],
        question: str = "",
        regenerate: bool = False,
    ) -> dict[str, Any]:
        st = self.status()
        if not st.get("available"):
            raise AIUnavailableError(st.get("reason") or "AI provider unavailable.", st)
        sanitized = sanitize_payload(payload, self.config)
        cache_key = _cache_key(self.provider, self.model, task, sanitized, question)
        if not regenerate:
            cached = _cache_get(cache_key, self.config.cache_ttl_s)
            if cached:
                cached = dict(cached)
                cached["cached"] = True
                return cached
        prompt = build_prompt(task, sanitized, question)
        raw_text = self._complete(prompt)
        result = parse_provider_json(raw_text, self.provider, self.model)
        result = validate_ai_output(result, sanitized, self.provider, self.model, task)
        _cache_set(cache_key, result)
        return result

    def _complete(self, prompt: dict[str, Any]) -> str:
        raise AIProviderError("Provider completion is not implemented.", self.status())


class NoopProvider(AIProvider):
    provider = "none"
    mode = "disabled"

    def status(self) -> dict[str, Any]:
        return _status(
            provider="none",
            mode="disabled",
            model="",
            enabled=False,
            available=False,
            reason="AI Copilot is off. Helios analytics still work normally.",
        )


class AnthropicProvider(AIProvider):
    provider = "anthropic"
    mode = "cloud"

    def __init__(self, config: AIConfig):
        super().__init__(config)
        self.model = config.anthropic_model or DEFAULT_ANTHROPIC_MODEL

    def status(self) -> dict[str, Any]:
        if not self.config.enabled:
            return NoopProvider(self.config).status()
        if not self.config.anthropic_key_present:
            return _status(
                provider=self.provider,
                mode=self.mode,
                model=self.model,
                enabled=True,
                available=False,
                reason="Claude provider is configured but ANTHROPIC_API_KEY is missing.",
            )
        return _status(
            provider=self.provider,
            mode=self.mode,
            model=self.model,
            enabled=True,
            available=True,
            reason="Claude provider is configured. Sanitized Helios metrics may be sent to Anthropic.",
            privacy_warning="Sends sanitized Helios metrics to Anthropic Claude.",
        )

    def _complete(self, prompt: dict[str, Any]) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise AIUnavailableError("ANTHROPIC_API_KEY is missing.", self.status())
        # No sampling params: Opus 4.7+ rejects temperature/top_p/top_k with a 400.
        body = {
            "model": self.model,
            "max_tokens": 1800,
            "system": prompt["system"],
            "messages": [{"role": "user", "content": prompt["user"]}],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            response = _post_json(
                "https://api.anthropic.com/v1/messages",
                body,
                headers,
                timeout=self.config.timeout_s,
            )
        except TimeoutError as exc:
            raise AITimeoutError("Claude request timed out.", self.status()) from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise AIProviderError("Claude rate limit reached; try again later.", self.status()) from exc
            raise AIProviderError(f"Claude provider returned HTTP {exc.code}.", self.status()) from exc
        except urllib.error.URLError as exc:
            raise AIProviderError("Claude provider is unreachable.", self.status()) from exc
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, list):
            raise AIProviderError("Claude provider returned an unexpected response.", self.status())
        text = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return text.strip()

    def chat(self, messages: list, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        st = self.status()
        if not st.get("available"):
            raise AIUnavailableError(st.get("reason") or "AI provider unavailable.", st)
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise AIUnavailableError("ANTHROPIC_API_KEY is missing.", st)
        history = _clean_dialogue_messages(messages)
        sanitized = sanitize_payload(payload or {}, self.config)
        convo: list[dict[str, str]] = [{
            "role": "user",
            "content": ("HELIOS CONTEXT (sanitized engine output — authoritative numbers, do not "
                        "recompute them):\n" + json.dumps(sanitized, sort_keys=True, separators=(",", ":"))),
        }]
        convo.extend(history)
        # No sampling params: Opus 4.7+ rejects temperature/top_p/top_k with a 400.
        body = {
            "model": self.model,
            "max_tokens": 2500,
            "system": DIALOGUE_SYSTEM,
            "messages": convo,
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            response = _post_json(
                "https://api.anthropic.com/v1/messages",
                body,
                headers,
                timeout=max(self.config.timeout_s, 60),
            )
        except TimeoutError as exc:
            raise AITimeoutError("Claude request timed out.", st) from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise AIProviderError("Claude rate limit reached; try again later.", st) from exc
            raise AIProviderError(f"Claude provider returned HTTP {exc.code}.", st) from exc
        except urllib.error.URLError as exc:
            raise AIProviderError("Claude provider is unreachable.", st) from exc
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, list):
            raise AIProviderError("Claude provider returned an unexpected response.", st)
        reply = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict)).strip()
        if not reply:
            raise AIProviderError("Claude returned an empty reply.", st)
        return {
            "reply": reply,
            "provider": self.provider,
            "model": self.model,
            "n_history_messages": len(history),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


class OllamaProvider(AIProvider):
    provider = "local"
    mode = "local"

    def __init__(self, config: AIConfig):
        super().__init__(config)
        self.model = config.local_model

    def status(self) -> dict[str, Any]:
        base_status = _local_base_status(self.config, self.model, backend="ollama")
        if not base_status.get("available"):
            return base_status
        try:
            tags = _get_json(_join_url(self.config.local_base_url, "/api/tags"), timeout=min(self.config.timeout_s, 2.0))
        except Exception:
            return _status(
                provider="local",
                mode="local",
                model=self.model,
                enabled=True,
                available=False,
                reason="Local Ollama server is not running or is unreachable.",
                privacy_warning="Runs on this machine when a local model server is configured.",
            )
        models = [m.get("name", "") for m in tags.get("models", []) if isinstance(m, dict)]
        if self.model and self.model not in models:
            return _status(
                provider="local",
                mode="local",
                model=self.model,
                enabled=True,
                available=False,
                reason="Configured Ollama model is not available locally. Install/pull models manually outside Helios.",
                privacy_warning="Runs on this machine when a local model server is configured.",
            )
        return base_status

    def _complete(self, prompt: dict[str, Any]) -> str:
        body = {
            "model": self.model,
            "format": "json",
            "stream": False,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "options": {"temperature": 0.1},
        }
        try:
            response = _post_json(_join_url(self.config.local_base_url, "/api/chat"), body, {}, self.config.timeout_s)
        except TimeoutError as exc:
            raise AITimeoutError("Local AI request timed out.", self.status()) from exc
        except Exception as exc:
            raise AIProviderError("Local AI server is unreachable.", self.status()) from exc
        message = response.get("message") if isinstance(response, dict) else {}
        return str((message or {}).get("content") or "")


class OpenAICompatibleLocalProvider(AIProvider):
    provider = "local"
    mode = "local"

    def __init__(self, config: AIConfig):
        super().__init__(config)
        self.model = config.local_model

    def status(self) -> dict[str, Any]:
        base_status = _local_base_status(self.config, self.model, backend="openai_compatible")
        if not base_status.get("available"):
            return base_status
        try:
            models = _get_json(_join_url(self.config.local_base_url, "/v1/models"), timeout=min(self.config.timeout_s, 2.0))
        except Exception:
            return _status(
                provider="local",
                mode="local",
                model=self.model,
                enabled=True,
                available=False,
                reason="OpenAI-compatible local server is not running or is unreachable.",
                privacy_warning="Runs on this machine when a local model server is configured.",
            )
        ids = [m.get("id", "") for m in models.get("data", []) if isinstance(m, dict)]
        if self.model and ids and self.model not in ids:
            return _status(
                provider="local",
                mode="local",
                model=self.model,
                enabled=True,
                available=False,
                reason="Configured local model is not listed by the server.",
                privacy_warning="Runs on this machine when a local model server is configured.",
            )
        return base_status

    def _complete(self, prompt: dict[str, Any]) -> str:
        body = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
        }
        try:
            response = _post_json(_join_url(self.config.local_base_url, "/v1/chat/completions"), body, {}, self.config.timeout_s)
        except TimeoutError as exc:
            raise AITimeoutError("Local AI request timed out.", self.status()) from exc
        except Exception as exc:
            raise AIProviderError("OpenAI-compatible local server is unreachable.", self.status()) from exc
        return _choice_text(response)


class OpenAIProvider(AIProvider):
    provider = "openai"
    mode = "cloud"

    def __init__(self, config: AIConfig):
        super().__init__(config)
        self.model = config.openai_model or DEFAULT_OPENAI_MODEL

    def status(self) -> dict[str, Any]:
        if not self.config.enabled:
            return NoopProvider(self.config).status()
        if not self.config.openai_key_present:
            return _status(
                provider=self.provider,
                mode=self.mode,
                model=self.model,
                enabled=True,
                available=False,
                reason="OpenAI provider is configured but OPENAI_API_KEY is missing.",
                privacy_warning="Sends sanitized Helios metrics to OpenAI.",
            )
        return _status(
            provider=self.provider,
            mode=self.mode,
            model=self.model,
            enabled=True,
            available=True,
            reason="OpenAI provider is configured. Sanitized Helios metrics may be sent to OpenAI.",
            privacy_warning="Sends sanitized Helios metrics to OpenAI.",
        )

    def _complete(self, prompt: dict[str, Any]) -> str:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise AIUnavailableError("OPENAI_API_KEY is missing.", self.status())
        body = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        try:
            response = _post_json("https://api.openai.com/v1/chat/completions", body, headers, self.config.timeout_s)
        except TimeoutError as exc:
            raise AITimeoutError("OpenAI request timed out.", self.status()) from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise AIProviderError("OpenAI rate limit reached; try again later.", self.status()) from exc
            raise AIProviderError(f"OpenAI provider returned HTTP {exc.code}.", self.status()) from exc
        except urllib.error.URLError as exc:
            raise AIProviderError("OpenAI provider is unreachable.", self.status()) from exc
        return _choice_text(response)


class CompositeProvider(AIProvider):
    provider = "hybrid"
    mode = "composite"

    def status(self) -> dict[str, Any]:
        return _status(
            provider=self.config.provider,
            mode="composite",
            model="",
            enabled=self.config.enabled,
            available=False,
            reason="Dual/hybrid provider routing is reserved for a future pass.",
        )


def get_provider(config: AIConfig | None = None) -> AIProvider:
    cfg = config or AIConfig.from_env()
    if not cfg.enabled or cfg.provider == "none":
        return NoopProvider(cfg)
    if cfg.provider == "anthropic":
        return AnthropicProvider(cfg)
    if cfg.provider == "openai":
        return OpenAIProvider(cfg)
    if cfg.provider == "local":
        if cfg.local_backend == "openai_compatible":
            return OpenAICompatibleLocalProvider(cfg)
        return OllamaProvider(cfg)
    if cfg.provider in {"dual", "hybrid"}:
        return CompositeProvider(cfg)
    return NoopProvider(cfg)


def payload_data_quality(payload: dict) -> dict:
    """Summarize the data-quality/provenance facts embedded in an AI request payload."""
    found = _find_quality(payload)
    return {
        "data_mode": found.get("data_mode") or found.get("mode"),
        "display_label": found.get("display_label"),
        "eligible_for_real_research": found.get("eligible_for_real_research"),
        "source": found.get("source"),
        "row_count": found.get("row_count") or found.get("history_days"),
        "first_date": found.get("first_date"),
        "last_date": found.get("last_date"),
        "last_refresh": found.get("last_refresh"),
        "reason": found.get("reason"),
        "required_action": found.get("required_action"),
        "warnings": found.get("warnings") or [],
        "missing_tickers": found.get("missing_tickers") or [],
    }


def _find_quality(value) -> dict:
    if isinstance(value, dict):
        provenance_payload = value.get("data_provenance") or value.get("provenance")
        base = provenance_payload if isinstance(provenance_payload, dict) else {}
        keys = {
            "data_mode", "mode", "display_label", "eligible_for_real_research", "source",
            "row_count", "history_days", "first_date", "last_date", "last_refresh",
            "reason", "required_action", "warnings", "missing_tickers",
        }
        out = {key: value.get(key) for key in keys if key in value}
        out.update({key: base.get(key) for key in keys if key not in out and key in base})
        if out:
            return out
        for child in value.values():
            found = _find_quality(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_quality(child)
            if found:
                return found
    return {}


def sanitize_payload(payload: dict[str, Any], config: AIConfig | None = None) -> dict[str, Any]:
    cfg = config or AIConfig.from_env()
    name_patterns = _name_patterns(payload) if cfg.redact_client_names else ()
    sanitized = _sanitize_value(payload, cfg, path=(), name_patterns=name_patterns)
    if not isinstance(sanitized, dict):
        sanitized = {"value": sanitized}
    sanitized["_sanitization"] = {
        "client_model_names_redacted": cfg.redact_client_names,
        # True only when ticker->weight composition data actually remains after sanitization.
        "holdings_sent": bool(cfg.send_holdings and _contains_composition(sanitized)),
        "raw_files_sent": False,
        "full_price_history_sent": False,
    }
    sanitized.setdefault("analysis_only_disclaimer", "Analysis only; Helios does not provide investment advice, order execution, or return guarantees.")
    return sanitized


# Dialogue persona: same integrity rules as the task prompts, free-prose form.
DIALOGUE_SYSTEM = (
    "You are the research copilot for the sole operator of Helios, a private portfolio "
    "decision-support terminal. You are in an ongoing dialogue with a professional portfolio "
    "manager who makes the final call and explicitly reserves the right to disagree with you and "
    "with the engine. Behave like a sharp senior analyst he can argue with: direct, pointed, "
    "specific. Take positions and defend them with the numbers in the context; when he pushes "
    "back, engage with his argument on the merits — concede when he is right, hold your ground "
    "with evidence when he is not. If the engine's rating looks wrong, say so and say why. "
    "No compliance boilerplate, no hedging filler, no 'consult a professional' — he is the "
    "professional. Hard integrity rules that protect real trades (absolute): every market number "
    "you cite must come from the provided context or the conversation; never invent prices, "
    "returns, yields, ratings, news, or fundamentals; never promise outcomes; call thin, stale, "
    "capped, demo, or suspect data what it is, bluntly; the engine's deterministic numbers and "
    "actions are facts to argue about, not things you can rewrite. If you need data that is not "
    "in the context, name exactly what is missing instead of guessing. Answer in plain prose "
    "(short paragraphs or tight bullets), not JSON."
)

DIALOGUE_MAX_MESSAGES = 24
DIALOGUE_MAX_CHARS = 6000


def _clean_dialogue_messages(messages: list) -> list[dict[str, str]]:
    """Validate/bound the client-supplied history: user/assistant roles only,
    strings truncated, capped to the most recent turns, must end on user."""
    cleaned: list[dict[str, str]] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip().lower()
        content = str(m.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        cleaned.append({"role": role, "content": content[:DIALOGUE_MAX_CHARS]})
    cleaned = cleaned[-DIALOGUE_MAX_MESSAGES:]
    if not cleaned or cleaned[-1]["role"] != "user":
        raise ValueError("Dialogue must end with a user message.")
    return cleaned


def build_prompt(task: str, sanitized_payload: dict[str, Any], question: str = "") -> dict[str, str]:
    list_schema_keys = {
        "key_points",
        "risks",
        "what_would_invalidate",
        "compliance_caveats",
        "used_numbers",
        "missing_information",
    }
    system = (
        "You are the research copilot for the sole operator of Helios, a private decision-support "
        "terminal. Your reader is a professional portfolio manager who makes the final call and gets "
        "enough compliance-hedged prose from fund companies — he wants a sharp senior analyst, not a "
        "disclaimer machine. Be direct and pointed: state what the data says, take a position, and say "
        "what you would do and why. If the engine's rating looks wrong given the evidence, say so "
        "plainly in the `stance` field (start it with AGREE or DISAGREE, then your one-paragraph case). "
        "The deterministic Helios numbers and action are never edited by you — you argue with them, you "
        "do not rewrite them. Hard integrity rules (these protect real trades, keep them absolute): use "
        "only facts in the payload; never invent prices, returns, yields, ratings, news, fundamentals, "
        "or analyst opinions; never promise outcomes; call out weak, thin, stale, demo, or blocked data "
        "bluntly rather than smoothing over it; if a number in the payload looks suspect (e.g. a growth "
        "estimate at a cap, a valuation block pinned at a clamp), flag it as suspect. "
        "Return only valid JSON matching the requested schema."
    )
    request = {
        "task": task,
        "question": question,
        "schema": {
            key: "array of concise strings" if key in list_schema_keys else "concise string"
            for key in SCHEMA_KEYS
        },
        "payload": sanitized_payload,
        "rules": {
            "voice": "direct senior analyst; pointed, specific, no hedging boilerplate",
            "stance": "always populate: AGREE or DISAGREE with the engine's action, with your case",
            "allowed_use": "explain, critique, argue a position, red-team, interpret forecasts, draft research notes",
            "forbidden": [
                "invent market facts",
                "recalculate or alter Helios scores and actions",
                "promise profit",
                "hide weak evidence",
                "present demo data as real",
            ],
        },
    }
    return {"system": system, "user": json.dumps(request, sort_keys=True, separators=(",", ":"))}


def parse_provider_json(text: str, provider: str, model: str) -> dict[str, Any]:
    parsed: dict[str, Any]
    try:
        parsed = json.loads(_extract_json(text))
        if not isinstance(parsed, dict):
            raise ValueError("Expected object.")
    except Exception:
        parsed = {
            "summary": "AI provider returned malformed JSON; review is required before use.",
            "key_points": [],
            "risks": ["Provider response was not valid JSON."],
            "what_would_invalidate": [],
            "advisor_language": "",
            "compliance_caveats": ["Malformed provider JSON was not used as decision-grade narrative."],
            "used_numbers": [],
            "missing_information": ["Valid provider JSON response."],
            "data_quality_statement": "AI response requires review.",
            "needs_review": True,
            "malformed_json": True,
        }
    return _normalize_result(parsed, provider, model)


def validate_ai_output(
    result: dict[str, Any],
    sanitized_payload: dict[str, Any],
    provider: str,
    model: str,
    task: str,
) -> dict[str, Any]:
    result = _normalize_result(result, provider, model)
    allowed_numbers = _payload_number_keys(sanitized_payload)
    unsupported = sorted(_numbers_in(_number_claim_content(result)) - allowed_numbers - _allowed_common_numbers())
    if unsupported:
        result["needs_review"] = True
        result["unsupported_numbers"] = unsupported[:12]
        result["compliance_caveats"].append(
            "AI mentioned numeric values not found in the Helios payload; review those statements before use."
        )
    result, blocked = _remove_forbidden_phrases(result)
    if blocked:
        result["needs_review"] = True
        result["blocked_phrases"] = blocked
        result["compliance_caveats"].append(
            "AI language contained prohibited assurance phrasing; those phrases were removed or caveated."
        )
    action = _deterministic_action(sanitized_payload)
    if action:
        result["deterministic_action"] = action
        # Disagreement is a feature, not a violation: the engine's action is
        # immutable, and the AI's dissent is surfaced explicitly so the operator
        # can weigh both. Detected from the stance field, or (fallback) from
        # upgrade language against a HOLD/REVIEW action.
        stance = str(result.get("stance") or "").strip()
        disagrees = stance.lower().startswith("disagree")
        if not stance and action in {"HOLD", "REVIEW"} and _ACTION_UPGRADE_RE.search(_result_text(result).lower()):
            disagrees = True
        result["ai_disagrees_with_action"] = disagrees
    data_mode = _data_mode(sanitized_payload)
    if data_mode:
        result["data_mode"] = data_mode
        stmt = result.get("data_quality_statement", "")
        if data_mode in {"demo", "blocked", "invalid_for_research"} and "not real market evidence" not in stmt.lower():
            result["data_quality_statement"] = (
                f"{stmt} Demo or blocked data is not real market evidence.".strip()
            )
            result["compliance_caveats"].append("Data is demo/blocked and must not be presented as real research evidence.")
        elif data_mode == "mixed" and "not verified real market data" not in stmt.lower():
            result["data_quality_statement"] = (
                f"{stmt} Parts of this evidence are not verified real market data; advisor review is required.".strip()
            )
            result["compliance_caveats"].append(
                "Data mode is mixed; parts of this evidence are not verified real market data and require advisor review."
            )
    result["provider"] = provider
    result["model"] = model
    result["task"] = task
    result["generated_at"] = result.get("generated_at") or datetime.now(timezone.utc).isoformat()
    result["cached"] = False
    result["compliance_caveats"] = _dedupe(result["compliance_caveats"])
    return result


def _sanitize_value(
    value: Any,
    cfg: AIConfig,
    path: tuple[str, ...],
    name_patterns: tuple[re.Pattern, ...] = (),
) -> Any:
    key = path[-1].lower() if path else ""
    if key in BLOCKED_KEYS:
        return _blocked_marker(key)
    if key == "holdings" and not cfg.send_holdings:
        if isinstance(value, list):
            return {"omitted": True, "count": len(value), "reason": "HELIOS_AI_SEND_HOLDINGS=0"}
        return {"omitted": True, "reason": "HELIOS_AI_SEND_HOLDINGS=0"}
    if not cfg.send_holdings and _is_composition_value(key, value):
        # Structural holdings gate: ticker->weight maps and ticker+weight/mrc rows
        # (clinic weights, risk_contributions, suggestions) are holdings-equivalent.
        return {"omitted": True, "count": len(value), "reason": "HELIOS_AI_SEND_HOLDINGS=0"}
    if cfg.redact_client_names and (key in NAME_KEYS or key.endswith("_name")):
        return "[redacted]"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            skey = str(k)[:80]
            out[skey] = _sanitize_value(v, cfg, path + (skey,), name_patterns)
        return out
    if isinstance(value, list):
        if len(value) > MAX_LIST_ITEMS and all(isinstance(item, (int, float, str)) for item in value):
            return {"omitted": True, "count": len(value), "reason": "long array omitted"}
        return [_sanitize_value(item, cfg, path, name_patterns) for item in value[:MAX_LIST_ITEMS]]
    if isinstance(value, str):
        return _redact_secret_like(_redact_names(value, name_patterns))[:MAX_STRING_LEN]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_secret_like(_redact_names(str(value), name_patterns))[:MAX_STRING_LEN]


def _blocked_marker(key: str) -> dict[str, Any]:
    return {"omitted": True, "reason": f"{key} is not sent to AI providers"}


def _is_ticker_weight_map(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    has_number = False
    for k, v in value.items():
        if not isinstance(k, str) or not _TICKER_KEY_RE.match(k):
            return False
        if isinstance(v, bool) or not (isinstance(v, (int, float)) or v is None):
            return False
        has_number = has_number or isinstance(v, (int, float))
    return has_number


def _is_composition_row(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    keys = {str(k).lower() for k in item}
    has_ticker = bool(keys & {"ticker", "symbol"})
    has_weight = any("weight" in k or "mrc" in k for k in keys)
    return has_ticker and has_weight


def _is_composition_value(key: str, value: Any) -> bool:
    if (key in COMPOSITION_WEIGHT_KEYS or key.endswith("_weights")) and _is_ticker_weight_map(value):
        return True
    return isinstance(value, list) and any(_is_composition_row(item) for item in value)


def _contains_composition(value: Any, key: str = "") -> bool:
    if isinstance(value, dict):
        if _is_composition_value(key, value):
            return True
        return any(_contains_composition(v, str(k).lower()) for k, v in value.items())
    if isinstance(value, list):
        if _is_composition_value(key, value):
            return True
        return any(_contains_composition(item, key) for item in value)
    return False


def _collect_name_values(value: Any, path: tuple[str, ...] = ()) -> set[str]:
    key = path[-1].lower() if path else ""
    names: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            names |= _collect_name_values(v, path + (str(k),))
    elif isinstance(value, list):
        for item in value:
            names |= _collect_name_values(item, path)
    elif isinstance(value, str) and (key in NAME_KEYS or key.endswith("_name")):
        stripped = value.strip()
        # Very short names would over-redact unrelated text.
        if len(stripped) >= 3:
            names.add(stripped)
    return names


def _name_patterns(payload: Any) -> tuple[re.Pattern, ...]:
    # Longest names first so partial overlaps do not leave fragments behind.
    names = sorted(_collect_name_values(payload), key=len, reverse=True)
    return tuple(re.compile(re.escape(name), re.IGNORECASE) for name in names)


def _redact_names(value: str, name_patterns: tuple[re.Pattern, ...]) -> str:
    for pattern in name_patterns:
        value = pattern.sub("[redacted]", value)
    return value


def _normalize_result(result: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    normalized = {}
    for key in SCHEMA_KEYS:
        if key in {"key_points", "risks", "what_would_invalidate", "compliance_caveats", "used_numbers", "missing_information"}:
            normalized[key] = _as_list(result.get(key))
        else:
            normalized[key] = str(result.get(key) or "")
    normalized["provider"] = provider
    normalized["model"] = model
    normalized["generated_at"] = normalized["generated_at"] or datetime.now(timezone.utc).isoformat()
    normalized["needs_review"] = bool(result.get("needs_review", False))
    if result.get("malformed_json"):
        normalized["malformed_json"] = True
    return normalized


def _remove_forbidden_phrases(result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    blocked = []
    for phrase in FORBIDDEN_PHRASES:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        if pattern.search(_result_text(result)):
            blocked.append(phrase)
            result = _map_strings(result, lambda s, p=pattern: p.sub("[unsupported assurance removed]", s))
    return result, blocked


def _map_strings(value: Any, fn) -> Any:
    if isinstance(value, dict):
        return {k: _map_strings(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, str):
        return fn(value)
    return value


def _result_text(result: dict[str, Any]) -> str:
    parts = []
    for value in result.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
    return "\n".join(parts)


def _number_claim_content(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key)
        for key in (
            "summary",
            "key_points",
            "risks",
            "what_would_invalidate",
            "advisor_language",
            "compliance_caveats",
            "used_numbers",
            "missing_information",
            "data_quality_statement",
        )
    }


def _number_values_in(value: Any) -> set[float]:
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    out = set()
    for raw in _NUMBER_RE.findall(text):
        norm = raw.strip().rstrip("%")
        if not norm:
            continue
        try:
            num = float(norm)
        except ValueError:
            continue
        if 1900 <= abs(num) <= 2100 and num.is_integer():
            continue
        out.add(num)
    return out


def _numbers_in(value: Any) -> set[str]:
    return {_num_key(num) for num in _number_values_in(value)}


def _payload_number_keys(payload: Any) -> set[str]:
    # A claim is supported if it restates a payload number exactly, rounded to
    # 0-2 decimals, or as a magnitude (e.g. drawdown -12.3% quoted as 12.3%).
    keys: set[str] = set()
    for num in _number_values_in(payload):
        for variant in (num, abs(num)):
            keys.add(_num_key(variant))
            for digits in (0, 1, 2):
                keys.add(_num_key(round(variant, digits)))
    return keys


def _allowed_common_numbers() -> set[str]:
    # Bare small counts only; round numbers like 10 or 100 must come from the payload.
    return {_num_key(n) for n in (0, 1, 2, 3)}


def _num_key(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _deterministic_action(payload: Any) -> str:
    if isinstance(payload, dict):
        action = payload.get("action")
        if isinstance(action, str) and action.upper() in {"BUY", "SELL", "HOLD", "REVIEW"}:
            return action.upper()
        for value in payload.values():
            found = _deterministic_action(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _deterministic_action(item)
            if found:
                return found
    return ""


def _data_mode(payload: Any) -> str:
    # Only keys literally named data_mode carry provenance; generic "mode" keys
    # (e.g. chart modes) must not be mistaken for a data mode.
    if isinstance(payload, dict):
        value = payload.get("data_mode")
        if isinstance(value, str) and value:
            normalized = value.lower()
            return "blocked" if normalized == "invalid_for_research" else normalized
        for value in payload.values():
            found = _data_mode(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _data_mode(item)
            if found:
                return found
    return ""


def _extract_json(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start:end + 1]
    return stripped


def _cache_key(provider: str, model: str, task: str, payload: dict[str, Any], question: str) -> str:
    body = json.dumps({"provider": provider, "model": model, "task": task, "payload": payload, "question": question}, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _cache_get(key: str, ttl_s: int) -> dict[str, Any] | None:
    if ttl_s <= 0:
        return None
    item = _CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > ttl_s:
        _CACHE.pop(key, None)
        return None
    return dict(value)


def _cache_set(key: str, value: dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), dict(value))
    if len(_CACHE) > 128:
        for stale in list(_CACHE)[:32]:
            _CACHE.pop(stale, None)


def _local_base_status(config: AIConfig, model: str, backend: str) -> dict[str, Any]:
    if not config.enabled:
        return NoopProvider(config).status()
    warning = _local_security_warning(config)
    if config.local_require_localhost and not _is_localhost_url(config.local_base_url):
        return _status(
            provider="local",
            mode="local",
            model=model,
            enabled=True,
            available=False,
            reason="Local AI URL is rejected because HELIOS_LOCAL_AI_REQUIRE_LOCALHOST=1.",
            security_warnings=[warning or "Local AI base URL must be localhost."],
        )
    if not model:
        return _status(
            provider="local",
            mode="local",
            model="",
            enabled=True,
            available=False,
            reason="Local AI model is not configured.",
            privacy_warning="Runs on this machine when a local model server is configured.",
            security_warnings=[warning] if warning else [],
        )
    return _status(
        provider="local",
        mode="local",
        model=model,
        enabled=True,
        available=True,
        reason=f"Local {backend} provider is configured.",
        privacy_warning="Runs on this machine when a local model server is configured.",
        security_warnings=[warning] if warning else [],
    )


def _local_security_warning(config: AIConfig) -> str:
    if not _is_localhost_url(config.local_base_url):
        return "Local AI base URL is not localhost; sanitized payloads may leave this machine."
    return ""


def _is_localhost_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return socket.gethostbyname(host).startswith("127.")
    except Exception:
        return False


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except socket.timeout as exc:
        raise TimeoutError from exc


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except socket.timeout as exc:
        raise TimeoutError from exc


def _choice_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    return str((message or {}).get("content") or "")


def _join_url(base: str, path: str) -> str:
    return urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _status(
    *,
    provider: str,
    mode: str,
    model: str,
    enabled: bool,
    available: bool,
    reason: str,
    privacy_warning: str = "",
    security_warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "provider": provider,
        "mode": mode,
        "model": model,
        "available": available,
        "reason": reason,
        "privacy_warning": privacy_warning,
        "security_warnings": [w for w in (security_warnings or []) if w],
        "keys_exposed": False,
        "secrets_stored": False,
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item)[:MAX_STRING_LEN] for item in value if item is not None]
    return [str(value)[:MAX_STRING_LEN]]


def _redact_secret_like(value: str) -> str:
    value = re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[redacted-key]", value)
    value = re.sub(r"sk-[A-Za-z0-9_\-]{12,}", "[redacted-key]", value)
    value = re.sub(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^,\s;]+", r"\1=[redacted]", value)
    return value


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(value: str, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))
