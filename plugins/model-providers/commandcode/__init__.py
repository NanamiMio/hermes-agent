"""CommandCode provider profiles: ``commandcode`` (chat_completions) and
``commandcode-anthropic`` (anthropic_messages, Bearer auth — see
``agent/anthropic_adapter.py``). Same key and base URL for both."""

import json
import logging
import urllib.parse
import urllib.request

from hermes_cli.urllib_security import open_credentialed_url
from providers import get_provider_profile, register_provider
from providers.base import ProviderProfile, _profile_user_agent

logger = logging.getLogger(__name__)

_COMMANDCODE_BASE = "https://api.commandcode.ai/provider/v1"
_COMMANDCODE_MODELS_URL = f"{_COMMANDCODE_BASE}/models"

# The account portal (``/alpha/*``) sits at the API origin, not under ``/provider/v1``.
_COMMANDCODE_API_ORIGIN = "https://api.commandcode.ai"


def _commandcode_api_origin(base_url: str | None) -> str:
    """Caller's base URL → the origin ``/alpha/*`` routes live on."""
    raw = (base_url or "").strip().rstrip("/")
    if not raw or "/provider/v1" in raw:
        return _COMMANDCODE_API_ORIGIN
    parsed = urllib.parse.urlsplit(raw)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else _COMMANDCODE_API_ORIGIN


def _commandcode_get_json(url: str, token: str) -> dict | None:
    """GET one Command Code JSON route; fail-open → None (``/usage`` must never raise)."""
    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "cli")
        with open_credentialed_url(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode())
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("commandcode usage %s: %s", url, exc)
        return None


def _commandcode_window(block: dict, label: str):
    """One ``windowLimits`` entry (``used``/``cap``/``resetAt`` in USD + epoch ms)."""
    from agent.account_usage import AccountUsageWindow

    used, cap = block.get("used"), block.get("cap")
    if not isinstance(used, (int, float)) or not isinstance(cap, (int, float)) or cap <= 0:
        return None
    reset_at = block.get("resetAt")
    reset_dt = None
    if isinstance(reset_at, (int, float)) and reset_at > 0:
        from datetime import datetime, timezone

        reset_dt = datetime.fromtimestamp(float(reset_at) / 1000.0, tz=timezone.utc)
    return AccountUsageWindow(
        label=label,
        used_percent=max(0.0, min(100.0, float(used) / float(cap) * 100.0)),
        reset_at=reset_dt,
        detail=f"${float(used):.2f} of ${float(cap):.2f} used",
    )


def _commandcode_usage_view(credits_payload: dict, summary: dict | None) -> tuple[list, list[str]]:
    """``/alpha/billing/credits`` (+ ``/alpha/usage/summary``) → windows and detail lines."""
    limits = credits_payload.get("windowLimits") or {}
    windows = [
        window
        for window in (
            _commandcode_window(limits.get("fiveHour") or {}, "5-hour"),
            _commandcode_window(limits.get("weekly") or {}, "Weekly"),
        )
        if window is not None
    ]
    credits = credits_payload.get("credits") or {}
    details: list[str] = []
    monthly = credits.get("monthlyCredits")
    if isinstance(monthly, (int, float)):
        details.append(f"Monthly credits left: ${float(monthly):.2f}")
    purchased = credits.get("purchasedCredits")
    if isinstance(purchased, (int, float)) and purchased > 0:
        details.append(f"Purchased credits: ${float(purchased):.2f}")
    if isinstance(summary, dict) and isinstance(summary.get("totalCost"), (int, float)):
        count = summary.get("totalCount")
        suffix = f" over {int(count)} calls" if isinstance(count, (int, float)) else ""
        details.append(f"This billing period: ${float(summary['totalCost']):.2f}{suffix}")
    return windows, details


class CommandCodeProfile(ProviderProfile):
    """CommandCode — OpenAI-compatible chat completions endpoint."""

    def fetch_models(
        self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0
    ) -> list[str] | None:
        """Public (unauthenticated) /models endpoint. The picker passes base_url
        unconditionally, so only a value differing from the default is a custom endpoint."""
        caller_base = (base_url or "").strip().rstrip("/")
        custom = caller_base and caller_base != _COMMANDCODE_BASE
        models_url = caller_base + "/models" if custom else _COMMANDCODE_MODELS_URL
        try:
            req = urllib.request.Request(models_url)
            req.add_header("Accept", "application/json")
            req.add_header("User-Agent", _profile_user_agent())
            with open_credentialed_url(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
        except Exception as exc:
            logger.debug("fetch_models(commandcode): %s", exc)
            return None


    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, model: str | None = None, **context
    ) -> tuple[dict, dict]:
        """DeepSeek ids (``deepseek/deepseek-v4-flash``) get the native DeepSeek wire
        controls: DeepSeek V4+ defaults to thinking when ``thinking`` is omitted, so
        without them ``/reasoning`` never reaches the request (#95232). Other model
        families stay a no-op — CommandCode declares no reasoning vocabulary for them."""
        m = (model or "").strip()
        if not m.lower().startswith("deepseek/"):
            return {}, {}
        # Registry lookup, not a module import: the deepseek shim is only a loader-injected
        # sys.modules entry, and the registry honours a user override of the profile.
        native = get_provider_profile("deepseek")
        if native is None:
            return {}, {}
        return native.build_api_kwargs_extras(
            reasoning_config=reasoning_config, model=m.split("/", 1)[1], **context,
        )


class CommandCodeAnthropicProfile(CommandCodeProfile):
    """CommandCode — Anthropic Messages API-compatible endpoint."""

    def fetch_models(
        self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0
    ) -> list[str] | None:
        """Public /models endpoint, filtered to Anthropic-family models."""
        all_models = super().fetch_models(api_key=api_key, base_url=base_url, timeout=timeout)
        return None if all_models is None else [m for m in all_models if m.startswith("claude-")]


commandcode = CommandCodeProfile(
    name="commandcode", aliases=("commandcode-chat",), api_mode="chat_completions",
    # Same key as the anthropic profile; distinct base-URL override vars so each
    # profile renders its own card on the desktop Keys tab (rows keyed by env var).
    env_vars=("COMMANDCODE_API_KEY", "COMMANDCODE_BASE_URL"),
    display_name="CommandCode", description="CommandCode — 20+ models via OpenAI-compatible API",
    signup_url="https://commandcode.ai/", base_url=_COMMANDCODE_BASE, models_url=_COMMANDCODE_MODELS_URL,
    fallback_models=(
        "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash", "Qwen/Qwen3.7-Max", "Qwen/Qwen3.6-Plus",
        "moonshotai/Kimi-K2.6", "zai-org/GLM-5.1", "MiniMaxAI/MiniMax-M2.7", "stepfun/Step-3.5-Flash",
        "xiaomi/mimo-v2.5-pro", "google/gemini-3.5-flash", "gpt-5.5",
    ),
    default_aux_model="deepseek/deepseek-v4-flash",
)

commandcode_anthropic = CommandCodeAnthropicProfile(
    name="commandcode-anthropic", aliases=("commandcode-claude",), api_mode="anthropic_messages",
    env_vars=("COMMANDCODE_API_KEY", "COMMANDCODE_ANTHROPIC_BASE_URL"),
    display_name="CommandCode (Anthropic)",
    description="CommandCode — Claude models via Anthropic Messages API",
    signup_url="https://commandcode.ai/", base_url=_COMMANDCODE_BASE, models_url=_COMMANDCODE_MODELS_URL,
    fallback_models=("claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"),
    default_aux_model="claude-haiku-4-5-20251001",
)

register_provider(commandcode)
register_provider(commandcode_anthropic)


class CommandCodeOAuthProfile(CommandCodeProfile):
    """Command Code OAuth profile."""

    def fetch_models(
        self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0
    ) -> list[str] | None:
        models = super().fetch_models(api_key=api_key, base_url=base_url, timeout=timeout)
        if not models:
            return list(self.fallback_models)
        free = ["meituan/LongCat-2.0:free", "poolside/laguna-s-2.1-free"]
        for f in reversed(free):
            if f in models:
                models.remove(f)
            models.insert(0, f)
        return models

    def fetch_account_usage(self, *, base_url: str | None = None, api_key: str | None = None):
        """Credits and 5-hour/weekly windows from the Command Code portal.

        ``/alpha/billing/credits`` is what the official CLI's ``/usage`` reads, and it accepts
        the CLI/OAuth token — the Provider API under ``/provider/v1`` does not — so this is the
        account view for Go-tier credentials. Fail-open → None.
        """
        token = (api_key or "").strip()
        if not token:
            try:
                from hermes_cli.auth_commandcode import resolve_commandcode_runtime_credentials

                token = str(resolve_commandcode_runtime_credentials().get("api_key") or "").strip()
            except Exception as exc:
                logger.debug("fetch_account_usage(commandcode-oauth): credentials: %s", exc)
                return None
        if not token:
            return None
        origin = _commandcode_api_origin(base_url)
        credits_payload = _commandcode_get_json(f"{origin}/alpha/billing/credits", token)
        if credits_payload is None:
            return None
        windows, details = _commandcode_usage_view(
            credits_payload, _commandcode_get_json(f"{origin}/alpha/usage/summary", token)
        )
        from datetime import datetime, timezone

        from agent.account_usage import AccountUsageSnapshot

        return AccountUsageSnapshot(
            provider="commandcode-oauth",
            source="billing-api",
            fetched_at=datetime.now(timezone.utc),
            title="Command Code limits",
            windows=tuple(windows),
            details=tuple(details),
            raw=credits_payload,
        )


commandcode_oauth = CommandCodeOAuthProfile(
    name="commandcode-oauth", aliases=("command-code", "commandcode-auth"), api_mode="commandcode_alpha",
    env_vars=(),
    display_name="Command Code OAuth",
    description="Command Code — browser OAuth or CLI account (~/.commandcode/auth.json)",
    signup_url="https://commandcode.ai/", base_url="https://api.commandcode.ai", models_url=_COMMANDCODE_MODELS_URL,
    auth_type="oauth_external",
    fallback_models=(
        "command-code/deepseek-deepseek-v4-flash",
        "command-code/deepseek-deepseek-v4-flash-vision-exp",
        "command-code/deepseek-deepseek-v4-pro",
        "command-code/meituan-LongCat-2.0:free",
        "command-code/meta-muse-spark-1.3-contributor",
        "command-code/MiniMaxAI-MiniMax-M3",
        "command-code/moonshotai-Kimi-K3",
        "command-code/poolside-laguna-s-2.1-free",
        "command-code/Qwen-Qwen3.8-Max-0902",
        "command-code/xai-grok-4.5",
        "command-code/xiaomi-mimo-v2.5-pro",
        "command-code/z-ai-glm-5.3-flash",
    ),
    default_aux_model="command-code/meituan-LongCat-2.0:free",
)

register_provider(commandcode_oauth)
