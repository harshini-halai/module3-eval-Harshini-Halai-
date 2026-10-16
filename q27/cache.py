import math
import re
from typing import List, Tuple, Optional, Any


def preprocess_text(text: str) -> List[str]:
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    tokens = clean.strip().split()
    normalized = []
    for t in tokens:
        if t.endswith("s") and len(t) > 3:
            t = t[:-1]
        normalized.append(t)
    return normalized


def compute_deterministic_embedding(text: str, dim: int = 64) -> List[float]:
    tokens = preprocess_text(text)
    if not tokens:
        return [0.0] * dim

    vec = [0.0] * dim
    features = list(tokens)
    for token in tokens:
        if len(token) >= 3:
            for i in range(len(token) - 2):
                features.append(token[i:i+3])

    for feat in features:
        h = 0
        for char in feat:
            h = (h * 31 + ord(char)) & 0xFFFFFFFF
        idx1 = h % dim
        idx2 = (h >> 7) % dim
        vec[idx1] += 1.0
        vec[idx2] += 0.5

    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return [0.0] * dim
    return [x / norm for x in vec]


get_text_embedding = compute_deterministic_embedding


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    return float(sum(a * b for a, b in zip(v1, v2)))


class SemanticCache:
    def __init__(self, similarity_threshold: float = 0.85, max_entries: int = 100, **kwargs):
        self.similarity_threshold = similarity_threshold
        self.threshold = similarity_threshold
        self.max_entries = max_entries
        self.capacity = max_entries
        self.entries: List[Tuple[str, List[float], Any]] = []
        self.hits = 0
        self.misses = 0

    def put(self, query: str, value: Any) -> None:
        query_vec = compute_deterministic_embedding(query)
        if len(self.entries) >= self.max_entries:
            self.entries.pop(0)
        self.entries.append((query, query_vec, value))

    def get(self, query: str) -> Tuple[Optional[Any], float]:
        if not self.entries:
            self.misses += 1
            return None, 0.0

        query_vec = compute_deterministic_embedding(query)
        best_score = -1.0
        best_val = None

        for cached_q, cached_vec, stored_val in self.entries:
            sim = cosine_similarity(query_vec, cached_vec)
            if sim > best_score:
                best_score = sim
                best_val = stored_val

        if best_score >= self.similarity_threshold:
            self.hits += 1
            return best_val, round(best_score, 4)

        self.misses += 1
        return None, round(max(0.0, best_score), 4)

    def lookup(self, query: str) -> Optional[List[str]]:
        val, _ = self.get(query)
        if val is not None:
            if isinstance(val, str):
                return [f"{w} " for w in val.split()]
            if isinstance(val, list):
                return val
            return [str(val)]
        return None

    def store(self, query: str, chunks: Any) -> None:
        self.put(query, chunks)

    def set(self, query: str, val: Any) -> None:
        self.put(query, val)

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total > 0 else 0.0

    def clear(self) -> None:
        self.entries.clear()
        self.hits = 0
        self.misses = 0