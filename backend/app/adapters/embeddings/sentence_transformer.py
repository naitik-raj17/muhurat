"""Local multilingual embeddings with sentence-transformers (no API calls).

The model loads lazily on first use (~470 MB download, cached in the HF cache directory).
Encoding is CPU-bound, so callers run it in a worker thread.
"""

from __future__ import annotations

import threading
from typing import Any


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, dimensions: int) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name, device="cpu")
                got = self._model.get_embedding_dimension()
                if got != self.dimensions:
                    raise RuntimeError(
                        f"{self.model_name} produces {got}-d vectors; config says {self.dimensions}"
                    )
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(
            texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False
        )
        return [list(map(float, v)) for v in vectors]
