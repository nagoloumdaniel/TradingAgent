"""The losing streaks are pure functions of the closed trades and an injected moment."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from tradingagent.risk.streak import (
    DayLossRun,
    longest_loss_run_today,
    losing_streak,
    losses_today,
    utc_day_start,
)

NOW = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)
MIDNIGHT = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)


def trade(pnl: str, hours_ago: float) -> tuple[D, datetime]:
    return D(pnl), NOW - timedelta(hours=hours_ago)


def test_the_utc_day_starts_at_midnight() -> None:
    assert utc_day_start(NOW) == MIDNIGHT


def test_no_trade_means_no_streak() -> None:
    assert losses_today([], NOW) == (0, None)


def test_two_losses_today_make_a_streak_of_two() -> None:
    streak, last = losses_today([trade("-1", 1), trade("-2", 2)], NOW)
    assert streak == 2
    assert last == NOW - timedelta(hours=1)


def test_three_losses_today_make_a_streak_of_three() -> None:
    assert losses_today([trade("-1", 1), trade("-2", 2), trade("-3", 3)], NOW)[0] == 3


def test_a_win_between_losses_resets_the_streak() -> None:
    # Newest first: loss, loss, win, loss -> the streak ending now is 2.
    trades = [trade("-1", 1), trade("-1", 2), trade("5", 3), trade("-1", 4)]
    assert losses_today(trades, NOW)[0] == 2


def test_a_win_as_latest_trade_means_no_streak() -> None:
    assert losses_today([trade("2", 1), trade("-1", 2), trade("-1", 3)], NOW)[0] == 0


def test_a_break_even_trade_ends_the_streak() -> None:
    assert losses_today([trade("0", 1), trade("-1", 2)], NOW)[0] == 0


def test_losses_closed_yesterday_do_not_count_today() -> None:
    # 15h in, 16h ago is 23:00 yesterday: only the first loss belongs to today.
    trades = [trade("-1", 1), trade("-1", 16), trade("-1", 17)]
    assert losses_today(trades, NOW)[0] == 1
    # The plain streak, used by the hour-based cooldown, still sees all three.
    assert losing_streak(trades)[0] == 3


def test_only_yesterday_losses_leave_today_clean() -> None:
    assert losses_today([trade("-1", 20), trade("-1", 21), trade("-1", 22)], NOW) == (0, None)


def test_a_loss_exactly_at_midnight_belongs_to_the_new_day() -> None:
    assert losses_today([(D(-1), MIDNIGHT)], NOW)[0] == 1
    assert losses_today([(D(-1), MIDNIGHT - timedelta(microseconds=1))], NOW)[0] == 0


def test_the_streak_resumes_counting_from_zero_the_next_day() -> None:
    trades = [trade("-1", 1), trade("-1", 2), trade("-1", 3)]
    assert losses_today(trades, NOW)[0] == 3
    tomorrow = MIDNIGHT + timedelta(days=1, minutes=1)
    assert losses_today(trades, tomorrow) == (0, None)


# --- The daily stop is a lock: the longest run of the day, not the run ending now ----------


def chronological(*pnls_hours: tuple[str, float]) -> list[tuple[D, datetime]]:
    """Trades as (pnl, hours before NOW), in any order."""
    return [trade(pnl, hours) for pnl, hours in pnls_hours]


def test_three_losses_then_a_late_win_stay_locked() -> None:
    # A position opened earlier closes in profit after the third loss: the day stays stopped.
    trades = chronological(("-1", 5), ("-1", 4), ("-1", 3), ("6", 1))
    newest_first = sorted(trades, key=lambda pair: pair[1], reverse=True)
    assert losses_today(newest_first, NOW)[0] == 0  # the run ending now is broken...
    assert longest_loss_run_today(trades, NOW) == DayLossRun(3, NOW.date())  # ...the lock is not


def test_two_losses_a_win_and_a_loss_are_not_a_lock() -> None:
    trades = chronological(("-1", 5), ("-1", 4), ("3", 3), ("-1", 1))
    assert longest_loss_run_today(trades, NOW).longest == 2


def test_a_break_even_trade_breaks_the_run_of_the_lock() -> None:
    trades = chronological(("-1", 5), ("-1", 4), ("0", 3), ("-1", 1))
    assert longest_loss_run_today(trades, NOW).longest == 2


def test_the_lock_does_not_depend_on_the_order_of_the_input() -> None:
    trades = chronological(("6", 1), ("-1", 3), ("-1", 5), ("-1", 4))
    assert longest_loss_run_today(trades, NOW).longest == 3


def test_a_run_of_yesterday_does_not_lock_today() -> None:
    trades = chronological(("-1", 20), ("-1", 21), ("-1", 22), ("-1", 1))
    assert longest_loss_run_today(trades, NOW).longest == 1


def test_the_lock_lifts_at_midnight_utc() -> None:
    trades = chronological(("-1", 5), ("-1", 4), ("-1", 3))
    assert longest_loss_run_today(trades, NOW).longest == 3
    just_before = MIDNIGHT + timedelta(days=1) - timedelta(microseconds=1)
    assert longest_loss_run_today(trades, just_before).longest == 3
    midnight = MIDNIGHT + timedelta(days=1)
    assert longest_loss_run_today(trades, midnight) == DayLossRun(0, midnight.date())


def test_a_loss_exactly_at_midnight_opens_the_new_day_and_old_ones_are_ignored() -> None:
    trades = [(D(-1), MIDNIGHT - timedelta(minutes=2)), (D(-1), MIDNIGHT - timedelta(minutes=1))]
    trades.append((D(-1), MIDNIGHT))
    assert longest_loss_run_today(trades, NOW).longest == 1


def test_no_trade_means_no_lock() -> None:
    assert longest_loss_run_today([], NOW) == DayLossRun(0, NOW.date())
