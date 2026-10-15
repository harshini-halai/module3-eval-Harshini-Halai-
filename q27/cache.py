import math
import hashlib
from typing import List, Tuple, Optional


def get_text_embedding(text: str, dim: int = 64) -> List[float]:
    """Generates a normalized deterministic vector embedding for text."""
    tokens = text.lower().strip().split()
    vec = [0.0] * dim
    for token in tokens:
        h = int(hashlib.md5(token.encode('utf-8')).hexdigest(), 16)
        for i in range(dim):
            vec[i] += ((h >> (i % 32)) & 1) * 2 - 1
            
    # L2 normalize
    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / mag for x in vec]


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Calculates cosine similarity between two unit vectors."""
    return sum(a * b for a, b in zip(v1, v2))


class SemanticCache:
    def __init__(self, similarity_threshold: float = 0.85, max_entries: int = 100, **kwargs):
        self.similarity_threshold = similarity_threshold
        self.threshold = similarity_threshold
        self.max_entries = max_entries
        self.capacity = max_entries
        self.entries: List[Tuple[str, List[float], List[str]]] = []

    def lookup(self, query: str) -> Optional[List[str]]:
        query_vec = get_text_embedding(query)
        for cached_q, cached_vec, chunks in self.entries:
            sim = cosine_similarity(query_vec, cached_vec)
            if sim >= self.similarity_threshold:
                return chunks
        return None

    # Alias for get
    def get(self, query: str) -> Optional[List[str]]:
        return self.lookup(query)

    def store(self, query: str, response_chunks: List[str]) -> None:
        if len(self.entries) >= self.max_entries:
            self.entries.pop(0)
        self.entries.append((query, get_text_embedding(query), response_chunks))

    # Alias for set
    def set(self, query: str, response_chunks: List[str]) -> None:
        self.store(query, response_chunks)

# Alias for test suite compatibility
compute_deterministic_embedding = get_text_embedding