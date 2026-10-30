"""The optional `news:` section of config/agent.yaml, and its defaults.

Everything has a default, so an agent.yaml without the section behaves exactly like one with an
empty `news: {}`: the gate is on, with the windows the operator asked for (15 minutes either
side of a high-impact announcement, 30 for the major ones).
"""

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BlackoutConfig(_Strict):
    """When a signal is refused because an announcement is about to land or just did."""

    enabled: bool = True
    high_before_minutes: int = Field(default=15, ge=0, le=240)
    high_after_minutes: int = Field(default=15, ge=0, le=240)
    # FOMC, non-farm payrolls, CPI and the rate decisions: wider, because the first move is
    # followed by a reversal that stops out a position opened inside it.
    major_before_minutes: int = Field(default=30, ge=0, le=240)
    major_after_minutes: int = Field(default=30, ge=0, le=240)
    # Medium-impact events are off by default: blocking on every one would leave the agent
    # blind for a large share of the day.
    medium_enabled: bool = False
    medium_before_minutes: int = Field(default=5, ge=0, le=120)
    medium_after_minutes: int = Field(default=5, ge=0, le=120)
    # A market is "concerned" by an event whose currency is at least this relevant to it
    # (see `model.currency_relevance`): 0.8 keeps USD and the market's own currency.
    min_relevance: float = Field(default=0.8, ge=0.0, le=1.0)


class NewsConfig(_Strict):
    enabled: bool = True
    blackout: BlackoutConfig = BlackoutConfig()
    # The calendar feed is rate-limited by its host (HTTP 429, Retry-After ~5 minutes): polling
    # faster than every 5 minutes only earns refusals.
    poll_minutes: int = Field(default=20, ge=5, le=240)
    # The hour (UTC) of the daily pass: refresh everything, then write the day's brief.
    daily_hour_utc: int = Field(default=6, ge=0, le=23)
    horizon_days: int = Field(default=7, ge=1, le=14)
    headline_lookback_hours: int = Field(default=36, ge=1, le=168)
    # Past this age without a successful refresh the gate says it is blind (and fails open).
    max_staleness_minutes: int = Field(default=360, ge=15, le=2880)
    # What the gate does when it cannot see the calendar (never refreshed, stale, or an empty
    # weekday export). False (default, also the simulated/demo behaviour): fail open, let the
    # signal through and say so (`news_gate_degraded`). True AND live mode: refuse the signal with
    # a motif instead. Reject-only: it never creates or loosens anything.
    fail_closed_when_blind: bool = False
    # The AI cost cap for news, per UTC day. The deterministic reading never stops.
    ai_daily_budget_eur: float = Field(default=0.30, ge=0.0, le=50.0)
    ai_max_events_per_call: int = Field(default=25, ge=1, le=60)
    ai_timeout_seconds: float = Field(default=90.0, ge=5.0, le=600.0)


DEFAULT_NEWS_CONFIG = NewsConfig()
