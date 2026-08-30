"""Thin client for the shared demo-portfolio LLM endpoint.

Configuration comes only from the environment (see config/pipeline.env.example):
  LLM_BASE_URL, LLM_MODEL, LLM_API_KEY

For convenience when running inside the demo-portfolio wrapper, the
OpenAI-compatible `LITELLM_BASE_URL`, `LITELLM_API_KEY` and
`LITELLM_DEFAULT_MODEL` are accepted as a fallback for the corresponding
`LLM_*` variables. The `LLM_*` names always take precedence.

No key is ever hardcoded or defaulted here.
"""

from __future__ import annotations

import os

from openai import OpenAI


class LlmConfigError(RuntimeError):
    pass


def _env_base_url() -> str | None:
    """LLM_BASE_URL, or LITELLM_BASE_URL (with a `/v1` suffix added if absent).

    The litellm proxy is OpenAI-compatible under `/v1`; the wrapper exports
    the bare host in LITELLM_BASE_URL, so normalise it here.
    """
    base_url = os.environ.get("LLM_BASE_URL")
    if base_url:
        return base_url
    litellm = os.environ.get("LITELLM_BASE_URL")
    if not litellm:
        return None
    litellm = litellm.rstrip("/")
    return litellm if litellm.endswith("/v1") else litellm + "/v1"


def get_client() -> tuple[OpenAI, str]:
    base_url = _env_base_url()
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("LITELLM_API_KEY")
    model = os.environ.get("LLM_MODEL") or os.environ.get("LITELLM_DEFAULT_MODEL")
    missing = [
        name for name, val in
        (("LLM_BASE_URL (or LITELLM_BASE_URL)", base_url),
         ("LLM_API_KEY (or LITELLM_API_KEY)", api_key),
         ("LLM_MODEL (or LITELLM_DEFAULT_MODEL)", model))
        if not val
    ]
    if missing:
        raise LlmConfigError(
            f"missing required environment variable(s): {', '.join(missing)}. "
            f"Copy config/pipeline.env.example to .env and fill in real values "
            f"(see repo README for where the demo-portfolio key lives)."
        )
    client = OpenAI(base_url=base_url, api_key=api_key)
    return client, model


def chat_json(system_prompt: str, user_prompt: str, *, retries: int = 1) -> str:
    """Call the chat endpoint, requesting a JSON object response.

    Tries `response_format={"type": "json_object"}` first; if the endpoint
    rejects that parameter, retries once without it (still instructed via
    the system prompt to return strict JSON). Returns the raw response text
    — the caller is responsible for `json.loads` and any retry-on-bad-JSON.
    """
    client, model = get_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
    except Exception as exc:  # endpoint may not support response_format
        print(f"[llm_client] response_format json_object rejected ({exc}); "
              f"retrying without it")
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
        )
    return resp.choices[0].message.content or ""
