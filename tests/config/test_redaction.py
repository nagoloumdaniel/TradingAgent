import io
import logging
from collections.abc import Iterator

import pytest

from tradingagent.config.redaction import MASK, install_secret_redaction, redact

SECRET = "Zq8wR3tY6uI9oP2"  # pragma: allowlist secret
LONGER = "Zq8wR3tY6uI9oP2-extended"  # pragma: allowlist secret


def test_redact_masks_every_occurrence() -> None:
    assert redact(f"a {SECRET} b {SECRET}", [SECRET]) == f"a {MASK} b {MASK}"


def test_redact_masks_longest_secret_first() -> None:
    assert redact(f"x {LONGER} y", [SECRET, LONGER]) == f"x {MASK} y"


@pytest.mark.parametrize("weak", ["", "abc"])
def test_redact_ignores_values_too_short_to_be_secrets(weak: str) -> None:
    assert redact("abcdef", [weak]) == "abcdef"


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    original_factory = logging.getLogRecordFactory()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("tradingagent")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    install_secret_redaction([SECRET])
    yield stream
    logging.setLogRecordFactory(original_factory)
    logger.removeHandler(handler)
    logger.setLevel(previous_level)


def test_secret_in_message_is_masked(captured: io.StringIO) -> None:
    logging.getLogger("tradingagent.data.feed").info(f"connecting with {SECRET}")
    assert SECRET not in captured.getvalue()
    assert MASK in captured.getvalue()


def test_secret_in_arguments_is_masked(captured: io.StringIO) -> None:
    logging.getLogger("tradingagent.notify").warning("token=%s", SECRET)
    assert SECRET not in captured.getvalue()
    assert MASK in captured.getvalue()


def test_secret_in_exception_traceback_is_masked(captured: io.StringIO) -> None:
    logger = logging.getLogger("tradingagent.execution")
    try:
        raise ValueError(f"rejected token {SECRET}")
    except ValueError:
        logger.exception("order failed")
    assert "order failed" in captured.getvalue()
    assert SECRET not in captured.getvalue()


def test_record_from_unrelated_logger_is_masked(captured: io.StringIO) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    third_party = logging.getLogger("websockets.client")
    third_party.addHandler(handler)
    try:
        third_party.error("handshake with %s failed", SECRET)
    finally:
        third_party.removeHandler(handler)
    assert SECRET not in stream.getvalue()
