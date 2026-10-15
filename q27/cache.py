import math
import hashlib
from typing import List, Tuple, Optional, Any, Union


def get_text_embedding(text: str, dim: int = 64) -> List[float]:
    """
    Generates a deterministic normalized embedding vector for the given text.
    Handles case-insensitivity, basic tokenization, and hashing.
    """
    tokens = text.lower().strip().split()
    if not tokens:
        return [0.0] * dim

    vec = [0.0] * dim
    for token in tokens:
        h = int(hashlib.md5(token.encode('utf-8')).hexdigest(), 16)
        for i in range(dim):
            vec[i] += ((h >> (i % 32)) & 1) * 2 - 1

    # L2 Normalization to unit length
    mag = math.sqrt(sum(x * x for x in vec))
    if mag == 0.0:
        return [0.0] * dim
    return [x / mag for x in vec]


# Test suite compatibility alias
compute_deterministic_embedding = get_text_embedding


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """
    Computes cosine similarity between two unit-normalized vectors.
    Returns a float between -1.0 and 1.0.
    """
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    return float(sum(a * b for a, b in zip(v1, v2)))


class SemanticCache:
    def __init__(self, similarity_threshold: float = 0.85, max_entries: int = 100, **kwargs):
        self.similarity_threshold = similarity_threshold
        self.threshold = similarity_threshold
        self.max_entries = max_entries
        self.capacity = max_entries
        # Entries structure: list of tuples -> (query_text, embedding_vector, stored_value)
        self.entries: List[Tuple[str, List[float], Any]] = []

    def put(self, query: str, value: Any) -> None:
        """
        Stores query, its vector embedding, and response value in the cache.
        Supports FIFO eviction when max capacity is reached.
        """
        query_vec = compute_deterministic_embedding(query)
        if len(self.entries) >= self.max_entries:
            self.entries.pop(0)
        self.entries.append((query, query_vec, value))

    def get(self, query: str) -> Tuple[Optional[Any], float]:
        """
        Calculates similarity with all cached queries.
        Returns a tuple: (hit_val, score) as required by the test suite.
        If threshold is not met, returns (None, score).
        """
        if not self.entries:
            return None, 0.0

        query_vec = compute_deterministic_embedding(query)
        best_score = -1.0
        best_val = None

        for cached_q, cached_vec, stored_val in self.entries:
            sim = cosine_similarity(query_vec, cached_vec)
            if sim > best_score:
                best_score = sim
                best_val = stored_val

        # Check against similarity threshold
        if best_score >= self.similarity_threshold:
            return best_val, round(best_score, 4)

        return None, round(max(0.0, best_score), 4)

    # -------------------------------------------------------------
    # Compatibility Methods for Gateway Server (app.py)
    # -------------------------------------------------------------
    def lookup(self, query: str) -> Optional[List[str]]:
        """Used by app.py streaming generator."""
        val, _ = self.get(query)
        if val is not None:
            if isinstance(val, str):
                return [f"{w} " for w in val.split()]
            if isinstance(val, list):
                return val
            return [str(val)]
        return None

    def store(self, query: str, response_chunks: Any) -> None:
        """Used by app.py to save streamed chunks."""
        self.put(query, response_chunks)

    def set(self, query: str, value: Any) -> None:
        self.put(query, value)

    def clear(self) -> None:
        self.entries.clear()