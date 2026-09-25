"""Minimal client for any OpenAI-compatible /chat/completions endpoint.

Works with NVIDIA NIM (default), OpenAI, Together, Groq, OpenRouter, or a local
vLLM/Ollama server — switch providers by changing LLM_BASE_URL / LLM_MODEL / LLM_API_KEY.
"""
import json
import re
import urllib.error
import urllib.request


class LLMError(Exception):
    """Raised when the LLM API can't be reached or returns something unusable."""


class ChatClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def provider_label(self) -> str:
        return "NVIDIA NIM" if "nvidia" in self.base_url else "LLM API"

    def chat(self, system: str, user: str, temperature: float = 0.2, max_tokens: int = 3000) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise LLMError(f"LLM API returned HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"Could not reach LLM API: {e}") from e
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("LLM API response had an unexpected format") from e


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates code fences / <think> blocks)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("LLM reply did not contain JSON")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM reply was not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise LLMError("LLM reply JSON was not an object")
    return data
