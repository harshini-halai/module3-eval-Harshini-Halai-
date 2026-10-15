import math
from typing import List, Optional, Dict, Any

# NOTE: Illustrative pricing for cost accounting modeling
PRICE_PER_1K_INPUT_TOKENS = 0.0015
PRICE_PER_1K_OUTPUT_TOKENS = 0.0020


def calculate_percentile(values: List[float], p: float) -> Optional[float]:
    """
    Calculates the p-th percentile of a list of floats.
    Returns None for empty collections to avoid misrepresenting absence of data as 0 latency.
    """
    if not values:
        return None

    if len(values) == 1:
        return round(values[0], 4)

    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    floor_k = math.floor(k)
    ceil_k = math.ceil(k)

    if floor_k == ceil_k:
        return round(sorted_vals[int(k)], 4)

    d0 = sorted_vals[floor_k] * (ceil_k - k)
    d1 = sorted_vals[ceil_k] * (k - floor_k)
    return round(d0 + d1, 4)


class GatewayMetrics:
    def __init__(self):
        self.ttft_records: List[float] = []
        self.total_duration_records: List[float] = []
        
        # Token & Cost tracking
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        
        self.actual_spend: float = 0.0
        self.avoided_spend: float = 0.0

    def record_request_timings(self, ttft: float, total_duration: float) -> None:
        self.ttft_records.append(ttft)
        self.total_duration_records.append(total_duration)

    def record_cost(self, prompt_tokens: int, completion_tokens: int, is_cache_hit: bool) -> None:
        cost = (
            (prompt_tokens / 1000.0) * PRICE_PER_1K_INPUT_TOKENS
            + (completion_tokens / 1000.0) * PRICE_PER_1K_OUTPUT_TOKENS
        )
        if is_cache_hit:
            self.avoided_spend += cost
        else:
            self.total_input_tokens += prompt_tokens
            self.total_output_tokens += completion_tokens
            self.actual_spend += cost

    def get_summary(self, cache_hit_rate: float = 0.0) -> Dict[str, Any]:
        potential_total = self.actual_spend + self.avoided_spend
        if potential_total > 0:
            savings_pct = (self.avoided_spend / potential_total) * 100.0
        else:
            savings_pct = 0.0

        return {
            "latency": {
                "ttft_p50_seconds": calculate_percentile(self.ttft_records, 50),
                "ttft_p95_seconds": calculate_percentile(self.ttft_records, 95),
                "total_duration_p50_seconds": calculate_percentile(self.total_duration_records, 50),
                "total_duration_p95_seconds": calculate_percentile(self.total_duration_records, 95),
                "request_count": len(self.ttft_records),
            },
            "cache": {
                "hit_rate": round(cache_hit_rate, 4),
            },
            "cost_accounting": {
                "actual_spend_usd": round(self.actual_spend, 6),
                "avoided_spend_usd": round(self.avoided_spend, 6),
                "savings_percentage": round(savings_pct, 2),
                "tokens": {
                    "input_billed": self.total_input_tokens,
                    "output_billed": self.total_output_tokens,
                },
            },
        }

# Alias taaki dono names se import kaam kare:
MetricsTracker = GatewayMetrics