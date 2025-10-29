#!/usr/bin/env python3
"""
quick_keyword_api_check.py

Minimal connectivity test for an OpenRouter-style chat completion API.
It requests two keywords from a short abstract and prints the response.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap

import requests


def getenv(name: str, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if not value:
        return default.strip()
    return value


# API_URL = getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
# API_KEY = getenv("OPENROUTER_API_KEY", "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")
API_URL = getenv("OPENROUTER_API_URL", "https://api.siliconflow.cn/v1/chat/completions")
API_KEY = getenv("OPENROUTER_API_KEY", "sk-jezzaxcyhijfasbbjcgmomdkqluhumkpkdemcefqwhwjvwmg")
# MODEL = getenv("OPENROUTER_KEYWORD_MODEL", "deepseek/deepseek-r1-0528-qwen3-8b:free")
# MODEL = getenv("OPENROUTER_KEYWORD_MODEL", "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B")
# MODEL = getenv("OPENROUTER_KEYWORD_MODEL", "qwen/qwen3-235b-a22b:free")
MODEL = getenv("OPENROUTER_KEYWORD_MODEL", "Qwen/Qwen2.5-7B-Instruct")
REFERER = getenv("OPENROUTER_HTTP_REFERER")
TITLE = getenv("OPENROUTER_X_TITLE", "Keyword Probe")

if not API_KEY:
    print("Missing OPENROUTER_API_KEY. Export it before running this script.", file=sys.stderr)
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
if REFERER:
    headers["HTTP-Referer"] = REFERER
if TITLE:
    headers["X-Title"] = TITLE

prompt = textwrap.dedent(
    """
    Extract two concise keywords (single words or short noun phrases) from the abstract below.
    Respond with a JSON object like {"keywords": ["keyword1", "keyword2"]} (max two items).

    Abstract:
    Autoregressive video diffusion models are capable of long rollouts that are stable and
    consistent with history, but they are unable to guide the current generation with conditioning
    from the future. In camera-guided video generation with a predefined camera trajectory, this
    limitation leads to collisions with the generated scene...
    """
).strip()

payload = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": "You extract concise research keywords."},
        {"role": "user", "content": prompt},
    ],
    "temperature": 0.2,
}

print(f"POST {API_URL} (model={MODEL})")

try:
    response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
except requests.RequestException as exc:
    print(f"❌ Request failed: {exc}", file=sys.stderr)
    if hasattr(exc, "response") and exc.response is not None:
        print(f"Status: {exc.response.status_code}", file=sys.stderr)
        print(f"Body: {exc.response.text}", file=sys.stderr)
    sys.exit(2)

try:
    data = response.json()
except ValueError as exc:
    print(f"❌ Could not parse JSON: {exc}", file=sys.stderr)
    print(response.text)
    sys.exit(3)

content = (
    data.get("choices", [{}])[0]
    .get("message", {})
    .get("content", "")
    .strip()
)

print("✅ Raw API JSON:")
print(json.dumps(data, indent=2, ensure_ascii=False))
print("\n✅ Message content:\n", content, "\n", sep="")

parsed = None
try:
    parsed = json.loads(content)
except json.JSONDecodeError:
    import re

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            parsed = None

if isinstance(parsed, dict):
    keywords = parsed.get("keywords")
    if isinstance(keywords, list):
        print("Parsed keywords:", keywords)
    else:
        print("Parsed response is missing a 'keywords' list.")
else:
    print("Response was not valid JSON; adjust the prompt or parse manually.")
