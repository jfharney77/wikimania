import json
import os
import re
from dataclasses import dataclass
from typing import Any


PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "together": "https://api.together.xyz/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
}


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    model_fast: str
    model_reasoning: str
    api_key: str
    api_base_url: str
    ollama_base_url: str
    default_timeout_seconds: float
    reasoning_timeout_seconds: float
    max_retries: int

    @classmethod
    def from_env(cls) -> "LLMRuntimeConfig":
        provider = os.getenv("PROVIDER", "cerebras")
        api_key = os.getenv("API_KEY") or os.getenv("GROQ_API_KEY", "")
        api_base_url = os.getenv("API_BASE_URL") or PROVIDER_BASE_URLS.get(
            provider,
            PROVIDER_BASE_URLS["groq"],
        )

        return cls(
            provider=provider,
            model_fast=os.getenv("MODEL_FAST", "llama3.1-8b"),
            model_reasoning=os.getenv("MODEL_REASONING", "gpt-oss-120b"),
            api_key=api_key,
            api_base_url=api_base_url,
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            default_timeout_seconds=_parse_float_env("LLM_TIMEOUT_SECONDS", 120.0),
            reasoning_timeout_seconds=_parse_float_env("LLM_REASONING_TIMEOUT_SECONDS", 240.0),
            max_retries=_parse_int_env("LLM_MAX_RETRIES", 6),
        )


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def remove_thinking_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_json_array(text: str) -> list[Any]:
    cleaned = remove_thinking_blocks(text)
    match = re.search(r"\[.*?\]", cleaned, re.DOTALL)
    if not match:
        return []

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    return parsed if isinstance(parsed, list) else []


def extract_string_array(text: str) -> list[str]:
    return [str(item).strip() for item in extract_json_array(text) if str(item).strip()]


def build_messages(system: str | None, user: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    return messages
