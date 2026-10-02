"""Shared LLM client (any OpenAI-compatible server, LM Studio by default) and the line format used by every translator."""
import re

import requests

from . import config


def llm(messages, temperature=0.2, max_tokens=4096):
    model = config.LLM_MODEL
    if not model:
        models = requests.get(f"{config.LLM_URL}/models", timeout=15).json()["data"]
        model = models[0]["id"]
    r = requests.post(f"{config.LLM_URL}/chat/completions", timeout=config.LLM_TIMEOUT,
                      json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def parse_lines(text):
    return {int(m.group(1)): m.group(2).strip() for m in re.finditer(r"^\s*\[(\d+)\]\s*(.+?)\s*$", text, flags=re.M)}
