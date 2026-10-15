"""Embedding providers. The model name is stored with every vector."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings


@dataclass
class Embedder:
    model_name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class LocalHashEmbedder(Embedder):
    """Deterministic bag-of-words vectors for offline runs and tests."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embed(text, self.dimensions) for text in texts]


class OpenAIEmbedder(Embedder):
    def __init__(self, model_name: str, dimensions: int, api_key: str):
        super().__init__(model_name=model_name, dimensions=dimensions)
        self.api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, timeout=get_settings().llm_timeout_seconds)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            response = client.embeddings.create(model=self.model_name, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)
        return vectors


class VoyageEmbedder(Embedder):
    def __init__(self, model_name: str, dimensions: int, api_key: str):
        super().__init__(model_name=model_name, dimensions=dimensions)
        self.api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        timeout = get_settings().llm_timeout_seconds
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            response = httpx.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model_name, "input": batch},
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()["data"]
            data = sorted(data, key=lambda item: item.get("index", 0))
            vectors.extend(item["embedding"] for item in data)
        return vectors


def build_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    provider = settings.embedding_provider
    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI embeddings")
        model = settings.embedding_model
        if model == "local-hash-v1":
            model = "text-embedding-3-small"
        return OpenAIEmbedder(model, settings.embedding_dimensions, settings.openai_api_key)
    if provider == "voyage":
        if not settings.voyage_api_key:
            raise RuntimeError("VOYAGE_API_KEY is required for Voyage embeddings")
        return VoyageEmbedder(settings.embedding_model, settings.embedding_dimensions, settings.voyage_api_key)
    return LocalHashEmbedder(settings.embedding_model, settings.embedding_dimensions)


def _hash_embed(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    from app.rag.retrieve import tokenize

    tokens = tokenize(text)
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
