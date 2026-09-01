"""Embedding provider boundary for the persistent vector index."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from math import sqrt
import re
from typing import Protocol, runtime_checkable


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding provider is unavailable or returns invalid data."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]+")


class HashEmbeddingProvider:
    """Deterministic local embedding for tests and offline smoke checks only."""

    def __init__(self, dimension: int = 128) -> None:
        if dimension < 16:
            raise ValueError("hash embedding dimension must be at least 16")
        self._dimension = dimension

    @property
    def provider_id(self) -> str:
        return f"local-hash-v1:{self.dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _features(self, text: str) -> list[str]:
        normalized = " ".join(text.casefold().split())
        words = _TOKEN_PATTERN.findall(normalized)
        character_ngrams = [
            normalized[index : index + 3]
            for index in range(max(0, len(normalized) - 2))
            if " " not in normalized[index : index + 3]
        ]
        return [*words, *character_ngrams] or [normalized]

    def _embed(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingProviderError("embedding text must be a non-empty string")
        vector = [0.0] * self.dimension
        for feature in self._features(text):
            digest = sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign
        norm = sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise EmbeddingProviderError("hash embedding produced a zero vector")
        return [value / norm for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class SentenceTransformerKoreanProvider:
    """Lazy connection point for a real multilingual/Korean embedding model.

    The optional dependency and model are never downloaded merely by importing this
    module. Loading failures are converted into an explicit provider error.
    """

    def __init__(
        self,
        model_name: str = "intfloat/multilingual-e5-base",
        *,
        dimension: int = 768,
        device: str = "cpu",
        local_files_only: bool = False,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must be non-empty")
        if dimension < 1:
            raise ValueError("embedding dimension must be positive")
        self.model_name = model_name
        self._dimension = dimension
        self.device = device
        self.local_files_only = local_files_only
        self._model = None

    @property
    def provider_id(self) -> str:
        return f"sentence-transformers:{self.model_name}:{self.dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingProviderError(
                "sentence-transformers is required for the Korean embedding provider"
            ) from exc
        try:
            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
                local_files_only=self.local_files_only,
                trust_remote_code=False,
            )
        except Exception as exc:
            raise EmbeddingProviderError(
                f"failed to load embedding model {self.model_name!r}"
            ) from exc
        # sentence-transformers 는 이 메서드를 get_embedding_dimension 으로
        # 개명하는 중이다(구명은 FutureWarning). 새 이름이 있으면 그걸 쓴다.
        _dim_getter = getattr(
            self._model, "get_embedding_dimension", None
        ) or self._model.get_sentence_embedding_dimension
        actual_dimension = _dim_getter()
        if actual_dimension != self.dimension:
            self._model = None
            raise EmbeddingProviderError(
                f"embedding dimension mismatch: configured {self.dimension}, "
                f"model returned {actual_dimension}"
            )
        return self._model

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingProviderError("embedding texts must be non-empty strings")
        try:
            values = self._load().encode(
                list(texts),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise EmbeddingProviderError("embedding model inference failed") from exc
        result = values.tolist()
        if any(len(vector) != self.dimension for vector in result):
            raise EmbeddingProviderError("embedding provider returned an invalid dimension")
        return result

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode passages with the asymmetric prefix required by E5 models."""

        return self._encode([f"passage: {text}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        """Encode a query with the asymmetric prefix required by E5 models."""

        return self._encode([f"query: {text}"])[0]
