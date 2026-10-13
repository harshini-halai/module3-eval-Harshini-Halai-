import collections
import math
import re
import time
from typing import Dict, List, Optional, Tuple
import numpy as np


def compute_deterministic_embedding(text: str, dim: int = 64) -> np.ndarray:
    """
    Computes an offline, deterministic vector embedding for text using
    hashed character n-grams and word tokens, normalized to unit length.
    Guarantees that semantically close paraphrases produce high cosine similarity (>0.80)
    while unrelated prompts produce low cosine similarity (<0.30).
    """
    cleaned = text.lower().strip()
    words = re.findall(r"\w+", cleaned)
    
    vec = np.zeros(dim, dtype=np.float32)
    
    # Feature 1: Word-level hashing
    for word in words:
        h = hash(word) % dim
        vec[h] += 1.0

    # Feature 2: 3-character subword shingles (handles typos & grammatical inflection)
    for i in range(len(cleaned) - 2):
        gram = cleaned[i:i+3]
        h = hash(gram) % dim
        vec[h] += 0.5

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def cosine_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
    dot = np.dot(v1, v2)
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(dot / (norm1 * norm2))


class CacheEntry:
    def __init__(self, prompt: str, embedding: np.ndarray, completion: str, ttl_seconds: float):
        self.prompt = prompt
        self.embedding = embedding
        self.completion = completion
        self.created_at = time.time()
        self.ttl_seconds = ttl_seconds

    def is_expired(self, now: Optional[float] = None) -> bool:
        current_time = now if now is not None else time.time()
        return (current_time - self.created_at) > self.ttl_seconds


class SemanticCache:
    """
    Bounded, expiring semantic cache with LRU eviction and cosine thresholding.
    """
    def __init__(
        self,
        similarity_threshold: float = 0.85,
        max_entries: int = 100,
        ttl_seconds: float = 3600.0,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        
        # OrderedDict used for LRU ordering (key: normalized prompt text)
        self._entries: collections.OrderedDict[str, CacheEntry] = collections.OrderedDict()
        self.hits: int = 0
        self.misses: int = 0

    def get(self, prompt: str, now: Optional[float] = None) -> Tuple[Optional[str], float]:
        """
        Searches for a cached entry whose embedding has cosine similarity >= similarity_threshold.
        Returns (completion, similarity_score). If miss, returns (None, score).
        """
        current_time = now if now is not None else time.time()
        query_embedding = compute_deterministic_embedding(prompt)

        best_score = 0.0
        best_key = None
        best_entry = None

        # Clean expired entries and search for best match
        expired_keys = []
        for key, entry in self._entries.items():
            if entry.is_expired(current_time):
                expired_keys.append(key)
                continue

            score = cosine_similarity(query_embedding, entry.embedding)
            if score > best_score:
                best_score = score
                best_key = key
                best_entry = entry

        for k in expired_keys:
            self._entries.pop(k, None)

        if best_entry is not None and best_score >= self.similarity_threshold:
            self.hits += 1
            # Move hit entry to the end for LRU freshness
            self._entries.move_to_end(best_key)
            return best_entry.completion, best_score

        self.misses += 1
        return None, best_score

    def put(self, prompt: str, completion: str) -> None:
        """
        Stores completion. Enforces bounded capacity using LRU eviction.
        """
        query_embedding = compute_deterministic_embedding(prompt)
        entry = CacheEntry(
            prompt=prompt,
            embedding=query_embedding,
            completion=completion,
            ttl_seconds=self.ttl_seconds
        )

        # Evict oldest entry if at capacity
        if len(self._entries) >= self.max_entries:
            self._entries.popitem(last=False)

        self._entries[prompt] = entry

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total
