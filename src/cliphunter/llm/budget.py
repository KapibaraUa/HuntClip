"""Cost accounting and budget guard (plan section 8: Budget, Prices)."""

from __future__ import annotations

from dataclasses import dataclass

from cliphunter.config import MediaRes, Settings


class BudgetExceeded(RuntimeError):
    """Raised before a call when spent + estimate would exceed the limit (job -> paused_budget)."""


def cost_usd(tokens_in: int, tokens_out: int, settings: Settings) -> float:
    """tokens_out must already include thinking tokens (billed as output)."""
    return (tokens_in * settings.price_in_per_m + tokens_out * settings.price_out_per_m) / 1_000_000


def estimate_video_tokens(duration_s: float, resolution: MediaRes, settings: Settings) -> int:
    return int(duration_s * settings.tokens_per_sec(resolution))


def estimate_video_cost(
    duration_s: float, resolution: MediaRes, settings: Settings, expected_out_tokens: int = 3000
) -> float:
    """Input estimate from tokens/sec plus a rough output allowance (thinking included)."""
    return cost_usd(
        estimate_video_tokens(duration_s, resolution, settings), expected_out_tokens, settings
    )


@dataclass
class Budget:
    limit_usd: float
    spent_usd: float = 0.0

    @property
    def remaining_usd(self) -> float:
        return self.limit_usd - self.spent_usd

    def check(self, estimated_usd: float) -> None:
        if self.spent_usd + estimated_usd > self.limit_usd:
            raise BudgetExceeded(
                f"estimated {estimated_usd:.4f} USD would exceed limit "
                f"{self.limit_usd:.4f} USD (spent {self.spent_usd:.4f})"
            )

    def add(self, usd: float) -> None:
        self.spent_usd += usd
