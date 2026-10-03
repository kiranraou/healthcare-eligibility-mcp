"""Claude-powered explanation of an eligibility result (single API call, no tools)."""

import json
import os

import anthropic

from src.config import get_settings


def _client_options(api_key: str | None, workspace_id: str | None) -> dict:
    """Use the key passed in (e.g. typed into the app), else ANTHROPIC_API_KEY from .env."""
    options: dict = {"api_key": api_key} if api_key else {}
    # Keys not scoped to a workspace must name one on every request.
    workspace_id = workspace_id or os.getenv("ANTHROPIC_WORKSPACE_ID")
    if workspace_id:
        options["default_headers"] = {"anthropic-workspace-id": workspace_id}
    return options


def make_client(api_key: str | None = None, workspace_id: str | None = None) -> anthropic.Anthropic:
    return anthropic.Anthropic(**_client_options(api_key, workspace_id))


def make_async_client(api_key: str | None = None, workspace_id: str | None = None) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(**_client_options(api_key, workspace_id))


FALLBACK_BETA = "server-side-fallback-2026-07-01"

EXPLAIN_SYSTEM = """You are a revenue cycle management (RCM) eligibility analyst at a medical group.
You receive the structured result of an X12 270/271 eligibility check. Explain it for the front desk
and billing team: the eligibility status, the root cause of any problem (cite the payer's AAA reject
code or the rule that fired), the likely denial category and CARC codes, the patient's financial
responsibility when benefits are known, and the recommended actions with their owners.
Use only the data provided; if something is unknown, say so. Be concise and use short headed sections."""


class LLMRefusalError(RuntimeError):
    """Claude (and its fallback models) declined the request."""


def _text(message) -> str:
    return "\n".join(b.text for b in message.content if b.type == "text").strip()


def explain_eligibility(result: dict, client: anthropic.Anthropic | None = None) -> str:
    """Turn a service.check_eligibility() result into a plain-language RCM explanation."""
    client = client or make_client()
    payload = {k: v for k, v in result.items() if k not in ("x12_270", "x12_271")}
    message = client.beta.messages.create(
        model=get_settings().anthropic_model,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": "low"},
        system=EXPLAIN_SYSTEM,
        messages=[{
            "role": "user",
            "content": "Eligibility check result:\n" + json.dumps(payload, indent=2, default=str),
        }],
    )
    if message.stop_reason == "refusal":
        raise LLMRefusalError("Claude declined to explain this eligibility result.")
    return _text(message)
