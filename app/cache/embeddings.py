"""Gemini embedding adapter for semantic result-cache lookup."""

import math
from collections.abc import Sequence

DEFAULT_EMBEDDING_MODEL = "models/gemini-embedding-001"
DEFAULT_EMBEDDING_DIMENSIONS = 768


class GeminiEmbedder:
    """Generate normalized Gemini embeddings through an injectable client.

    Gemini's embedding API distinguishes retrieval queries from stored
    documents. Callers should use :meth:`embed_query` for lookups and
    :meth:`embed_document` before inserting a semantic cache entry.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
        client: object | None = None,
    ) -> None:
        if dimensions <= 0:
            raise ValueError("Embedding dimensions must be positive.")
        if client is None:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            client = GoogleGenerativeAIEmbeddings(
                model=model,
                api_key=api_key,
                output_dimensionality=dimensions,
            )
        self._client = client
        self.model = model
        self.dimensions = dimensions

    async def embed(self, text: str) -> tuple[float, ...]:
        """Compatibility alias for a retrieval-query embedding."""
        return await self.embed_query(text)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed a user lookup with Gemini's retrieval-query task type."""
        self._validate_text(text)
        vector = await self._client.aembed_query(
            text,
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=self.dimensions,
        )
        return self._normalize(vector)

    async def embed_document(
        self,
        text: str,
        *,
        title: str | None = None,
    ) -> tuple[float, ...]:
        """Embed one cacheable query as a retrieval document."""
        self._validate_text(text)
        vectors = await self._client.aembed_documents(
            [text],
            task_type="RETRIEVAL_DOCUMENT",
            titles=[title] if title is not None else None,
            output_dimensionality=self.dimensions,
        )
        if len(vectors) != 1:
            raise ValueError("Gemini returned an unexpected embedding count.")
        return self._normalize(vectors[0])

    async def embed_documents(
        self,
        texts: Sequence[str],
        *,
        titles: Sequence[str] | None = None,
    ) -> list[tuple[float, ...]]:
        """Embed a batch of cacheable queries as retrieval documents."""
        materialized = list(texts)
        if not materialized:
            return []
        for text in materialized:
            self._validate_text(text)
        materialized_titles = list(titles) if titles is not None else None
        if materialized_titles is not None and len(materialized_titles) != len(
            materialized
        ):
            raise ValueError("Embedding titles must align with input texts.")
        vectors = await self._client.aembed_documents(
            materialized,
            task_type="RETRIEVAL_DOCUMENT",
            titles=materialized_titles,
            output_dimensionality=self.dimensions,
        )
        if len(vectors) != len(materialized):
            raise ValueError("Gemini returned an unexpected embedding count.")
        return [self._normalize(vector) for vector in vectors]

    @staticmethod
    def _validate_text(text: str) -> None:
        if not text.strip():
            raise ValueError("Embedding text cannot be empty.")

    def _normalize(self, vector: Sequence[float]) -> tuple[float, ...]:
        values = [float(value) for value in vector]
        if len(values) != self.dimensions:
            raise ValueError(
                f"Expected {self.dimensions} embedding dimensions, "
                f"received {len(values)}."
            )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Embedding contains a non-finite value.")
        norm = math.sqrt(math.fsum(value * value for value in values))
        if norm == 0:
            raise ValueError("Embedding vector cannot be all zeroes.")
        return tuple(value / norm for value in values)


GeminiEmbeddingAdapter = GeminiEmbedder
