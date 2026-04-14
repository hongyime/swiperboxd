import random


def exponential_backoff_seconds(attempt: int, base: float = 0.5, cap: float = 30.0, jitter: bool = True) -> float:
    value = min(cap, base * (2 ** max(attempt, 0)))
    if not jitter:
        return value
    return value * (0.5 + random.random() * 0.5)


def should_trigger_proxy_fallback(status_code: int) -> bool:
    return status_code in {403, 429}
