"""What leaves as a broker comment: short enough for the terminal, unique enough to trust.

The MetaTrader5 Python module refuses a comment of 30 characters or more with
``(-2, 'Invalid "comment" argument')``, before any IPC; measured on 2026-10-09 against
version 5.0.6231 with ``order_check``, which routes nothing (probe and raw output in
``docs/research/execution-diagnostic/tools/``). An order carrying 31 characters therefore
never reached the broker, and the reconciliation was looking for a comment nobody had sent.

The comment is the *only* way back to an order whose answer was lost: `place` deduplicates on
the idempotency key locally, then adopts the broker's position by matching this string. A
comment that two orders can share would make the agent adopt a position that is not its own;
a comment the search cannot recompute is worthless. Both halves are pinned here:

* what leaves fits the terminal's measured limit;
* what leaves is a pure function of the idempotency key, so the same key always recomputes it
  and two different keys never collide — the digest is never truncated to make room.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.execution.conftest import FakeLog, request

from tradingagent.core.mode import TradingMode
from tradingagent.execution.mt5_broker import COMMENT_LIMIT, HASH_LENGTH, MT5Broker, key_comment
from tradingagent.execution.simulator import SimulatedTerminal
from tradingagent.risk.model import OrderRequest
from tradingagent.runtime.pipeline import order_comment

# Measured on MetaTrader5 5.0.6231: 0..29 accepted, 30..33 refused. The MQL5 EA bridge
# truncates at 31 instead (`docs/ea/protocole-pont.md`), so 29 is the narrower of the two.
MODULE_COMMENT_LIMIT = 29
EA_COMMENT_LIMIT = 31

# The shape the production pipeline uses: `SignalPipeline._execute` fills `OrderRequest.comment`
# with `order_comment(idempotency_key)`, twelve hex characters, and that value is what the
# broker used to treat as a prefix.
PRODUCTION_KEY = "trend_breakout@1.0.1:BTCUSD:M15:2026-10-09T03:45Z"


def broker(terminal: SimulatedTerminal, log: FakeLog) -> MT5Broker:
    return MT5Broker(terminal, log, login=terminal.login, mode=TradingMode.DEMO)


def demo_terminal() -> SimulatedTerminal:
    terminal = SimulatedTerminal()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    return terminal


def comment_sent_to_the_terminal(terminal: SimulatedTerminal) -> str:
    """The comment the terminal actually received: the simulator stores it on the position.

    This is the same field the MT5 adapter reads back (`PositionInfo.comment`), so it is what
    validation and reconciliation see on a real account.
    """
    positions = terminal.positions()
    assert len(positions) == 1
    return positions[0].comment


def test_the_pipeline_comment_is_the_shape_that_used_to_break_the_send() -> None:
    """A guard on this file: if the caller's comment stops being an issue, these tests lie."""
    expected = "ta-" + hashlib.sha256(PRODUCTION_KEY.encode()).hexdigest()[:12]

    assert order_comment(PRODUCTION_KEY) == expected
    assert len(order_comment(PRODUCTION_KEY)) == 15


def test_the_comment_sent_fits_the_terminal_limit() -> None:
    terminal, log = demo_terminal(), FakeLog()

    asyncio.run(
        broker(terminal, log).place(
            request(key=PRODUCTION_KEY, comment=order_comment(PRODUCTION_KEY))
        )
    )

    sent = comment_sent_to_the_terminal(terminal)
    assert len(sent) <= MODULE_COMMENT_LIMIT, f"{sent!r} is longer than the module accepts"
    assert len(sent) <= EA_COMMENT_LIMIT


def test_a_lost_answer_is_recovered_whatever_comment_the_caller_passed() -> None:
    """The nominal race: the order fills, the answer is lost, the position is adopted by
    comment. It only works if the comment sent is the one the search recomputes."""
    terminal, log = demo_terminal(), FakeLog()
    terminal.lost_answers = 1

    result = asyncio.run(
        broker(terminal, log).place(
            request(key=PRODUCTION_KEY, comment=order_comment(PRODUCTION_KEY))
        )
    )

    assert result.accepted is True, result.message
    assert "no second order sent" in result.message
    assert terminal.calls.count("order_send") == 1


@pytest.mark.parametrize(
    "caller_comment",
    ["ta", "", "ta-2fd16bf5cebc", "x" * 200, "tc-2fd16bf5cebc9f10"],
    ids=["default", "empty", "production", "absurdly long", "close-shaped"],
)
def test_the_comment_sent_is_a_pure_function_of_the_idempotency_key(caller_comment: str) -> None:
    terminal, log = demo_terminal(), FakeLog()

    asyncio.run(broker(terminal, log).place(request(key=PRODUCTION_KEY, comment=caller_comment)))

    sent = comment_sent_to_the_terminal(terminal)
    assert sent == key_comment(PRODUCTION_KEY)
    assert len(sent) <= MODULE_COMMENT_LIMIT


@pytest.mark.parametrize("prefix", ["ta", "tc", "x" * 200])
def test_the_digest_is_never_truncated_to_make_room_for_a_prefix(prefix: str) -> None:
    """Uniqueness travels in the digest, so the digest is the part that must survive.

    Truncating from the right, as the first implementation did, let a long prefix eat the
    whole key: every key sharing that prefix produced the identical comment.
    """
    digest = hashlib.sha256(PRODUCTION_KEY.encode()).hexdigest()[:HASH_LENGTH]

    comment = key_comment(PRODUCTION_KEY, prefix)

    assert comment.endswith(digest)
    assert len(comment) <= MODULE_COMMENT_LIMIT
    assert len(digest) == HASH_LENGTH


def test_a_long_prefix_no_longer_makes_every_key_share_one_comment() -> None:
    """The concrete failure the truncation allowed: ten keys, ten times the same string."""
    prefix = "x" * 200

    comments = {key_comment(f"key-{index}", prefix) for index in range(10)}

    assert len(comments) == 10


def test_distinct_keys_never_share_a_comment_across_the_real_key_space() -> None:
    """Uniqueness, exercised over the keys this project really mints.

    Signals are `reference:symbol:timeframe:timestamp` at the strategy's own schedule (M15
    here) and closes are `close:ticket:reason`. Every digest is 64 bits and never truncated.
    """
    references = ("witness@1.1.1", "trend_breakout@1.0.1", "vwap_pullback@1.0.0")
    symbols = ("XAUUSD", "BTCUSD")
    timeframes = ("M1", "M5", "M15")
    start = datetime(2026, 1, 1, tzinfo=UTC)

    keys: list[str] = []
    for reference in references:
        for symbol in symbols:
            for timeframe in timeframes:
                # One M15 signal every fifteen minutes for ninety days: 8 640 instants each.
                keys.extend(
                    f"{reference}:{symbol}:{timeframe}:"
                    f"{(start + timedelta(minutes=15 * step)).strftime('%Y-%m-%dT%H:%MZ')}"
                    for step in range(8640)
                )
    keys.extend(f"close:{ticket}:stop_loss" for ticket in range(1, 20_001))
    assert len(keys) == 175_520
    assert len(set(keys)) == len(keys), "the generated key space already holds a duplicate"

    comments = [key_comment(key) for key in keys]

    assert len(set(comments)) == len(keys), "two distinct keys produced the same comment"
    assert max(len(comment) for comment in comments) <= MODULE_COMMENT_LIMIT


def test_an_entry_comment_and_a_close_comment_never_collide() -> None:
    """`tc-` marks a close, `ta-` an entry, and the keys themselves already differ."""
    entry = key_comment(PRODUCTION_KEY)
    closing = key_comment("close:123456:stop_loss", "tc")

    assert entry != closing
    assert entry.startswith("ta-") and closing.startswith("tc-")
    assert len(entry) <= MODULE_COMMENT_LIMIT and len(closing) <= MODULE_COMMENT_LIMIT


class CommentRecordingLog(FakeLog):
    """A journal that remembers the request it was handed, as `orders.broker_comment` does."""

    def __init__(self) -> None:
        super().__init__()
        self.requested_comment: str | None = None

    def record_request(self, sent: OrderRequest, requested_price: Decimal, at: datetime) -> int:
        self.requested_comment = sent.comment
        return super().record_request(sent, requested_price, at)


def test_the_journal_records_the_comment_the_broker_received() -> None:
    """`orders.broker_comment` must not claim a string the broker never saw.

    The column is written from the request's comment at send time, so it has to carry the
    comment that actually left — not the caller's, which the broker never sees.
    """
    terminal, log = demo_terminal(), CommentRecordingLog()

    asyncio.run(
        broker(terminal, log).place(
            request(key=PRODUCTION_KEY, comment=order_comment(PRODUCTION_KEY))
        )
    )

    assert log.requested_comment == comment_sent_to_the_terminal(terminal)
    assert log.requested_comment == key_comment(PRODUCTION_KEY)


def test_the_comment_limit_is_the_number_the_terminal_accepts() -> None:
    """29, not 31: the constant must carry the measured number, not the MQL5 one."""
    assert COMMENT_LIMIT == MODULE_COMMENT_LIMIT
    assert COMMENT_LIMIT < EA_COMMENT_LIMIT
