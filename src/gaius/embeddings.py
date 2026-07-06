from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True)
class EmbedderStatus:
    name: str
    available: bool
    detail: str


class BaseEmbedder:
    name = "none"
    dimensions = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []


class FastEmbedEmbedder(BaseEmbedder):
    name = "fastembed"

    def __init__(self, model_name: str | None = None) -> None:
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model_name) if model_name else TextEmbedding()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(v) for v in self.model.embed(texts)]


class OpenAIEmbedder(BaseEmbedder):
    name = "openai"

    def __init__(self, config: Config) -> None:
        missing = []
        if not config.openai_base_url:
            missing.append("openai.base_url")
        if not config.openai_key_env:
            missing.append("openai.key_env")
        if not config.openai_model:
            missing.append("openai.model")
        if missing:
            raise ValueError(f"openai embedder requires {', '.join(missing)} in config")
        key = os.environ.get(config.openai_key_env or "")
        if not key:
            raise ValueError(f"openai embedder requires env var {config.openai_key_env} to be set")
        self.url = (config.openai_base_url or "").rstrip("/") + "/embeddings"
        self.key = key
        self.model = config.openai_model or ""

    def embed(self, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"model": self.model, "input": texts}).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
        return [list(item["embedding"]) for item in payload["data"]]


def get_embedder(config: Config) -> BaseEmbedder:
    choice = config.embedder
    if choice == "none":
        return BaseEmbedder()
    if choice == "openai":
        return OpenAIEmbedder(config)
    if choice in {"auto", "fastembed"}:
        try:
            return FastEmbedEmbedder(config.fastembed_model)
        except Exception:
            if choice == "fastembed":
                raise
    return BaseEmbedder()


def embedder_status(config: Config) -> EmbedderStatus:
    if config.embedder == "none":
        return EmbedderStatus("none", True, "FTS5-only mode")
    try:
        embedder = get_embedder(config)
    except Exception as exc:
        return EmbedderStatus(config.embedder, False, str(exc))
    if embedder.name == "none":
        return EmbedderStatus("none", True, "fastembed unavailable; using FTS5-only mode")
    return EmbedderStatus(embedder.name, True, "available")
