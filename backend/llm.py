import asyncio
import httpx
import os
import json
import re
from fastapi import HTTPException

PROVIDER = os.getenv("PROVIDER", "groq")
MODEL_FAST = os.getenv("MODEL_FAST", "llama-3.1-8b-instant")
MODEL_REASONING = os.getenv("MODEL_REASONING", "qwen/qwen3-32b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


async def _call_groq(model: str, messages: list[dict], timeout: float = 120.0, max_retries: int = 6) -> str:
    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    json={"model": model, "messages": messages},
                )
            except httpx.ConnectError:
                raise HTTPException(status_code=503, detail="Cannot connect to Groq API.")

        if resp.status_code == 429:
            body = resp.json()
            msg = body.get("error", {}).get("message", "")
            m = re.search(r"try again in (\d+\.?\d*)s", msg)
            wait = float(m.group(1)) + 1.5 if m else 20.0
            print(f"[rate limit] {model} — sleeping {wait:.1f}s (attempt {attempt + 1}/{max_retries})", flush=True)
            await asyncio.sleep(wait)
            continue

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"Groq error: {e.response.text}")

        return resp.json()["choices"][0]["message"]["content"]

    raise HTTPException(status_code=429, detail=f"Groq rate limit exceeded after {max_retries} retries.")


async def _call_ollama(model: str, prompt: str, timeout: float = 120.0) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                headers={"ngrok-skip-browser-warning": "true"},
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Ollama at {OLLAMA_BASE_URL}.")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"Ollama error: {e.response.text}")
    return resp.json()["response"]


async def call_fast(prompt: str) -> str:
    """Fast model — concept extraction, short tasks."""
    if PROVIDER == "ollama":
        return await _call_ollama(MODEL_FAST, prompt)
    messages = [{"role": "user", "content": prompt}]
    return await _call_groq(MODEL_FAST, messages)


async def call_reasoning(system: str, user: str) -> str:
    """Reasoning model — article writing, query synthesis."""
    if PROVIDER == "ollama":
        prompt = f"{system}\n\n{user}"
        return await _call_ollama(MODEL_REASONING, prompt, timeout=240.0)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return await _call_groq(MODEL_REASONING, messages, timeout=240.0)


def parse_json_array(text: str) -> list[str]:
    """Extract a JSON array from LLM output that may contain surrounding text."""
    # Strip <think>...</think> blocks (reasoning models)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if not match:
        return []
    try:
        result = json.loads(match.group())
        return [str(s).strip() for s in result if str(s).strip()]
    except json.JSONDecodeError:
        return []
