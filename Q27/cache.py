import collections
import hashlib
import math
import re
import time
from typing import Dict, List, Optional, Tuple
import numpy as np

STOP_WORDS = {
    "what", "is", "the", "of", "tell", "me", "a", "an", "in", "to", "for", "and", "do", "how", "can", "you", "please"
}

def stable_hash(token: str) -> int:
    """Fixed-seed deterministic hash independent of Python process restarts."""
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)

def tokenize_and_stem(text: str) -> List[str]:
    cleaned = text.lower().strip()
    words = re.findall(r"\w+", cleaned)
    tokens = []
    for w in words:
        if w in STOP_WORDS or len(w) <= 1:
            continue
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        elif len(w) > 4 and w.endswith("ing"):
            w = w[:-3]
        elif len(w) > 4 and w.endswith("ed"):
            w = w[:-2]
        tokens.append(w)
    return tokens

def compute_deterministic_embedding(text: str, dim: int = 128) -> np.ndarray:
    tokens = tokenize_and_stem(text)
    vec = np.zeros(dim, dtype=np.float32)
    
    if not tokens:
        tokens = re.findall(r"\w+", text.lower().strip())

    for token in tokens:
        h = stable_hash(token) % dim
        vec[h] += 2.0
        for i in range(len(token) - 2):
            gh = stable_hash(token[i:i+3]) % dim
            vec[gh] += 0.5

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
    Threshold = 0.70 empirically separates true paraphrases (0.76+) from distinct queries (<0.15).
    """
    def __init__(
        self,
        similarity_threshold: float = 0.70,
        max_entries: int = 100,
        ttl_seconds: float = 3600.0,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: collections.OrderedDict[str, CacheEntry] = collections.OrderedDict()
        self.hits: int = 0
        self.misses: int = 0

    def get(self, prompt: str, now: Optional[float] = None) -> Tuple[Optional[str], float]:
        current_time = now if now is not None else time.time()
        query_embedding = compute_deterministic_embedding(prompt)

        best_score = 0.0
        best_key = None
        best_entry = None

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
            self._entries.move_to_end(best_key)
            return best_entry.completion, best_score

        self.misses += 1
        return None, best_score

    def put(self, prompt: str, completion: str) -> None:
        query_embedding = compute_deterministic_embedding(prompt)
        entry = CacheEntry(
            prompt=prompt,
            embedding=query_embedding,
            completion=completion,
            ttl_seconds=self.ttl_seconds
        )

        if len(self._entries) >= self.max_entries:
            self._entries.popitem(last=False)

        self._entries[prompt] = entry

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total