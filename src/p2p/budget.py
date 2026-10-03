"""Hard per-case limits (spec: 10 requests incl. retries, 30k completion tokens, 10 min) with safety margins."""
from __future__ import annotations

import time

MIN_USEFUL_TOKENS = 256  # below this a call is not worth making


class Budget:
    def __init__(self, max_calls=10, max_completion=30_000, deadline_s=600,
                 soft_calls=8, soft_completion=26_000, soft_deadline_s=540, t0: float | None = None):
        self.max_calls, self.max_completion, self.deadline_s = max_calls, max_completion, deadline_s
        self.soft_calls, self.soft_completion, self.soft_deadline_s = soft_calls, soft_completion, soft_deadline_s
        self.t0 = time.monotonic() if t0 is None else t0
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.cached_tokens = 0
        self.estimated = False  # True if some failed attempt had no usage and we charged its max_tokens

    # time
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def time_left(self) -> float:
        """Seconds left before the soft deadline."""
        return self.soft_deadline_s - self.elapsed()

    # tokens
    def remaining_completion(self) -> int:
        return max(0, self.soft_completion - self.completion_tokens)

    def clamp_max_tokens(self, requested: int) -> int:
        return max(0, min(int(requested), self.remaining_completion()))

    def can_call(self, requested_max_tokens: int = MIN_USEFUL_TOKENS) -> bool:
        need = min(int(requested_max_tokens), MIN_USEFUL_TOKENS)
        return (self.calls < self.soft_calls
                and self.time_left() > 20
                and self.remaining_completion() >= need)

    def record(self, usage: dict | None, requested_max_tokens: int | None = None) -> None:
        """Count one HTTP attempt. Call this for EVERY attempt, including retries and failures.
        If usage is missing, charge requested_max_tokens (worst case) so we never overspend."""
        self.calls += 1
        if not usage:
            if requested_max_tokens:
                self.completion_tokens += int(requested_max_tokens)
                self.estimated = True
            return
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        ctd = usage.get("completion_tokens_details") or {}
        ptd = usage.get("prompt_tokens_details") or {}
        self.reasoning_tokens += int(usage.get("reasoning_tokens") or ctd.get("reasoning_tokens") or 0)
        self.cached_tokens += int(usage.get("cached_tokens") or ptd.get("cached_tokens") or 0)

    def totals(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "usage_estimated": self.estimated,
        }
