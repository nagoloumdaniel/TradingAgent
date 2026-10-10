"""The operator's palette: a grouped /help, explained choices, and inline buttons.

Written before the implementation. Three promises are checked here, and each one is a
promise made to the operator, not to the code:

  * /help groups the whole palette by usage and says, in one sentence, what each command
    is for — the operator understands the palette at a glance, on a phone;
  * a command that offers a choice first states the consequence of every option
    (/mode: PAPER simulates, DEMO trades a demo account, LIVE is real money);
  * a click runs the *same* audited path as the typed command, revalidates what the
    button names at the moment of the click, and never acts on an outdated message.

Command replies are plain text: no HTML tag, 80 characters per line at most, no URL and
no file path — the same invariants as the rest of the control centre.
"""

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.mode import TradingMode
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import (
    COMMAND_HELP,
    USAGE_TAGLINES,
    CommandRequest,
    CommandRouter,
    Usage,
    status_handler,
)
from tradingagent.notify.read_commands import (
    gates_handler,
    market_handler,
    report_handler,
)
from tradingagent.notify.replies import (
    CALLBACK_PREFIX,
    CALLBACK_TTL,
    MAX_CALLBACK_BYTES,
    Button,
    Keyboard,
    Reply,
    decode_callback,
    encode_callback,
    is_fresh,
)
from tradingagent.notify.sensitive_commands import (
    close_all_handler,
    disable_handler,
    emergency_stop_handler,
    enable_handler,
    mode_handler,
    pause_handler,
    restart_handler,
    resume_handler,
)
from tradingagent.notify.service import CommandService
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import AuditLogRow, SystemEventRow

OPERATOR, STRANGER = 111, 999
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # a Tuesday
MARKETS: tuple[tuple[str, bool], ...] = (("XAUUSD", True), ("BTCUSD", True))

# The whole palette, as the operator sees it in /help: nineteen buttons plus /help.
PALETTE = (
    "help",
    "status",
    "markets",
    "marche",
    "signals",
    "positions",
    "performance",
    "report",
    "propositions",
    "portes",
    "mode",
    "disable",
    "enable",
    "pause",
    "resume",
    "close_all",
    "emergency_stop",
    "restart",
    "restart_all",
    "shutdown",
)

HTML_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
#: The only vocabulary a reply may use, and only on the screens written by hand.
ALLOWED_TAGS = frozenset({"b", "i", "code", "pre", "blockquote"})
STRIP_TAGS = re.compile(r"</?(?:" + "|".join(sorted(ALLOWED_TAGS)) + r")>")
URL = re.compile(r"https?://")
WINDOWS_PATH = re.compile(r"[A-Za-z]:\\")
# The only shape a button may carry: a version, a UTC stamp, a command, plain arguments.
CALLBACK_SHAPE = re.compile(r"^c1:\d{1,12}:[a-z_]{1,32}(:[A-Za-z0-9_.@+-]{1,32})*$")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'palette.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


class Clock:
    """A clock the test moves by hand: no sleeping, no flakiness."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now


def service(engine: Engine, clock: Clock | None = None) -> CommandService:
    """The control centre wired like the bot: same handlers, same gate, same audit store,
    so a click and a typed command cannot take two different roads."""
    halts = HaltStore(engine)
    router = CommandRouter()
    router.register("status", "état de l'agent", status_handler(halts, TradingMode.DEMO))
    router.register(
        "marche", "état d'un marché", market_handler(MARKETS, CandleStore(engine), halts, engine)
    )
    router.register("mode", "change le mode", mode_handler(engine))
    router.register("portes", "portes de promotion", gates_handler(engine, markets=MARKETS))
    router.register("report", "rapport à la demande", report_handler(engine, lambda: T0))
    router.register("disable", "désactive un marché", disable_handler(halts, MARKETS))
    router.register("enable", "réactive un marché", enable_handler(halts, MARKETS))
    router.register("pause", "suspend les ordres", pause_handler(halts))
    router.register("resume", "reprend les ordres", resume_handler(halts))
    router.register("close_all", "ferme les positions", close_all_handler(halts))
    router.register("emergency_stop", "arrêt d'urgence", emergency_stop_handler(halts))
    router.register("restart", "redémarre l'agent", restart_handler(engine))
    return CommandService(AccessGate({OPERATOR}), router, AuditStore(engine), now=clock or Clock())


def send(svc: CommandService, text: str, user: int = OPERATOR) -> Reply | None:
    return asyncio.run(svc.handle(user, True, text))


def click(
    svc: CommandService, data: str, user: int = OPERATOR, private: bool = True
) -> Reply | None:
    return asyncio.run(svc.handle_callback(user, private, data))


def buttons(reply: Reply | None) -> list[Button]:
    assert reply is not None and reply.keyboard is not None, f"no buttons on {reply!r}"
    return [button for row in reply.keyboard.rows for button in row]


def labelled(reply: Reply | None, label: str) -> Button:
    for button in buttons(reply):
        if button.label == label:
            return button
    raise AssertionError(f"no button labelled {label!r}: {[b.label for b in buttons(reply)]}")


def assert_readable(answer: str) -> None:
    assert answer.strip(), "a command never answers with a blank page"
    assert len(answer) < 4096, "Telegram cuts at 4096"
    # Width is what the operator sees: tags are markup, not characters on the screen.
    for line in answer.splitlines():
        assert len(STRIP_TAGS.sub("", line)) <= 80, answer
    for tag in HTML_TAG.findall(answer):
        assert tag in ALLOWED_TAGS, f"<{tag}> is not part of the vocabulary a reply may use"
    assert URL.search(answer) is None
    assert WINDOWS_PATH.search(answer) is None


def assert_plain(answer: str) -> None:
    """A data-driven answer carries no markup at all: nothing from data can break it."""
    assert_readable(answer)
    assert HTML_TAG.search(answer) is None, f"a plain reply must carry no tag, got {answer!r}"


def audit_rows(engine: Engine) -> list[AuditLogRow]:
    with Session(engine) as session:
        return list(session.scalars(select(AuditLogRow).order_by(AuditLogRow.id)).all())


def commands_recorded(engine: Engine) -> list[dict[str, Any]]:
    return [row.detail for row in audit_rows(engine) if row.action == "command"]


def mode_events(engine: Engine) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(SystemEventRow).where(SystemEventRow.kind == "mode_command")
            ).all()
        )


def help_text(router: CommandRouter) -> Reply:
    return asyncio.run(router.dispatch(CommandRequest(OPERATOR, "help", (), T0)))


def full_router() -> CommandRouter:
    """A router that knows the whole palette, without a database behind it."""

    async def handler(_: CommandRequest) -> str:
        return "vu"

    router = CommandRouter()
    for name in PALETTE:
        if name != "help":
            router.register(name, "description enregistrée", handler)
    return router


# --- 1. /help: the whole palette, grouped by usage ---------------------------


def test_the_help_table_documents_the_twenty_commands() -> None:
    assert set(COMMAND_HELP) == set(PALETTE)
    assert len(COMMAND_HELP) == 20


def test_every_help_entry_says_what_the_command_is_for_in_one_sentence() -> None:
    for name, (_usage, signature, purpose) in COMMAND_HELP.items():
        assert purpose, f"/{name} has no purpose"
        assert purpose[0].islower(), f"/{name}: a sentence starts in lower case here"
        assert len(purpose.splitlines()) == 1, f"/{name}: one line, not a paragraph"
        assert signature.startswith(f"/{name}"), f"{signature} does not name /{name}"
        assert len(f"  {signature:<20} {purpose}") <= 80, f"/{name} does not fit a phone"


def menu_commands(reply: Reply | None) -> list[str]:
    """The commands the menu's buttons open, in the order they are laid out.

    A button carries `aide <command>`: it opens that command's page rather than running it,
    which is the whole point of a tap-only palette.
    """
    found = (decode_callback(button.data) for button in buttons(reply))
    return [
        decoded.args[0]
        for decoded in found
        if decoded is not None and decoded.command == "aide" and decoded.args
    ]


def test_the_menu_is_one_button_per_command_grouped_by_usage() -> None:
    answer = help_text(full_router())

    assert_readable(answer)
    for usage in Usage:
        assert usage.value in answer, f"the {usage.value} group is missing"
        assert USAGE_TAGLINES[usage] in answer
    # The palette lives in the buttons, not in the text: the operator taps instead of typing.
    assert set(menu_commands(answer)) == set(PALETTE) - {"help"}
    assert "bouton" in answer, "and the message says what a tap does"


def test_help_keeps_the_groups_in_the_reading_order() -> None:
    answer = help_text(full_router())

    positions = [answer.index(usage.value) for usage in Usage]
    assert positions == sorted(positions), "consulter, puis contrôler, puis agir"


def test_a_command_button_opens_its_own_page_with_its_confirmation(engine: Engine) -> None:
    """The operator's request: tap a command, read only that command, and act from there."""
    svc = service(engine)

    menu_reply = send(svc, "/help")
    page_reply = click(svc, labelled(menu_reply, "/pause").data)

    assert page_reply is not None
    assert_readable(page_reply)
    assert "Suspend les nouveaux ordres" in page_reply
    assert "/pause confirmer" in page_reply, "the example is there to copy"
    assert "confirmer" in page_reply.lower()
    # The page carries the action itself, plus the way back to the menu.
    confirm = labelled(page_reply, "Confirmer la suspension")
    assert decode_callback(confirm.data).args == ("confirmer",)  # type: ignore[union-attr]
    back = labelled(page_reply, "◀ Menu")
    assert decode_callback(back.data).command == "help"  # type: ignore[union-attr]


def test_a_page_is_rich_text_and_only_the_pages_are(engine: Engine) -> None:
    """Markup is opt-in per reply: a data-driven answer stays plain and cannot break."""
    svc = service(engine)

    page_reply = send(svc, "/aide pause")
    plain = send(svc, "/status")

    assert page_reply is not None and page_reply.parse_mode == "HTML"
    assert "<b>/pause</b>" in page_reply
    assert plain is not None and plain.parse_mode is None
    assert "<b>" not in plain


def test_a_page_for_an_unread_command_says_so(engine: Engine) -> None:
    answer = send(service(engine), "/aide rm_rf")

    assert answer is not None and "inconnue" in answer.lower()


def test_help_lists_a_command_it_has_no_entry_for() -> None:
    router = full_router()

    async def handler(_: CommandRequest) -> str:
        return "vu"

    router.register("bricole", "commande ajoutée après coup", handler)

    answer = help_text(router)

    labels = [button.label for button in buttons(answer)]
    assert "/bricole" in labels, "an undocumented command still gets its button"
    assert "AUTRES" in answer
    assert_readable(answer)

    page = asyncio.run(router.dispatch(CommandRequest(OPERATOR, "aide", ("bricole",), T0)))
    assert "commande ajoutée après coup" in page


# --- 2. a choice states its consequences first --------------------------------


def test_mode_explains_every_consequence_before_offering_the_choice(engine: Engine) -> None:
    answer = send(service(engine), "/mode")

    assert answer is not None
    assert_readable(answer)
    # What each mode does, in the operator's words: simulated, demo money, real money.
    assert "PAPER" in answer and "simul" in answer and "courtier" in answer
    assert "DÉMO" in answer and "démonstration" in answer and "fictif" in answer
    assert "RÉEL" in answer and "argent réel" in answer and "9 portes" in answer
    assert "OBSERVATION" in answer and "SIGNAL" in answer
    # The explanation and the buttons travel in the same message: nothing to choose
    # before the consequences have been read.
    assert [button.label for button in buttons(answer)] == [
        "OBSERVATION",
        "SIGNAL",
        "PAPER",
        "DÉMO",
        "RÉEL",
    ]


def test_mode_says_where_the_agent_stands_today(engine: Engine) -> None:
    svc = service(engine)
    send(svc, "/mode DEMO")

    answer = send(svc, "/mode")

    assert answer is not None and "DEMO" in answer
    assert "dernier mode demandé" in answer.lower()


def test_mode_without_a_recorded_change_says_so(engine: Engine) -> None:
    answer = send(service(engine), "/mode")

    assert answer is not None
    assert "aucun" in answer.lower()


def test_the_pause_prompt_states_what_stops_what_continues_and_how_to_come_back(
    engine: Engine,
) -> None:
    answer = send(service(engine), "/pause")

    assert answer is not None
    assert_readable(answer)
    assert "nouveaux ordres" in answer  # what stops
    assert "conservées" in answer  # what continues (RM-015)
    assert "/resume" in answer  # how to come back
    assert "confirmer" in answer
    assert labelled(answer, "Confirmer la suspension").data.endswith(":pause:confirmer")


def test_close_all_and_the_emergency_stop_state_their_own_consequence(engine: Engine) -> None:
    svc = service(engine)

    closing = send(svc, "/close_all")
    stopping = send(svc, "/emergency_stop")

    assert closing is not None and "clôturées" in closing
    assert stopping is not None and "pas" in stopping and "clôturées" in stopping
    labelled(closing, "Confirmer la clôture")
    labelled(stopping, "Confirmer l'arrêt d'urgence")


def test_disable_without_a_symbol_lists_the_markets_it_can_stop(engine: Engine) -> None:
    answer = send(service(engine), "/disable")

    assert answer is not None
    assert_readable(answer)
    assert [button.label for button in buttons(answer)] == ["XAUUSD", "BTCUSD"]
    assert "plus aucun signal" in answer  # the consequence of the choice


def test_the_market_view_offers_its_two_controls_as_buttons(engine: Engine) -> None:
    answer = send(service(engine), "/marche XAUUSD")

    assert answer is not None
    stopping = decode_callback(labelled(answer, "Arrêter ce marché").data)
    starting = decode_callback(labelled(answer, "Reprendre ce marché").data)
    assert stopping is not None and stopping.command == "disable" and stopping.args == ("XAUUSD",)
    assert starting is not None and starting.command == "enable" and starting.args == ("XAUUSD",)


def test_portes_without_a_market_offers_the_markets_as_buttons(engine: Engine) -> None:
    svc = service(engine)

    answer = send(svc, "/portes")

    assert answer is not None
    assert_readable(answer)
    assert "/portes" in answer  # the usage text stays, the buttons come with it
    assert [button.label for button in buttons(answer)] == ["XAUUSD", "BTCUSD"]

    chosen = click(svc, labelled(answer, "XAUUSD").data)
    assert chosen is not None and "aucune stratégie" in chosen.lower()
    assert commands_recorded(engine)[-1] == {"command": "portes", "args": ["XAUUSD"]}


def test_report_offers_its_three_periods_as_buttons(engine: Engine) -> None:
    svc = service(engine)

    daily = send(svc, "/report")

    assert daily is not None
    assert [button.label for button in buttons(daily)] == ["Quotidien", "Hebdomadaire", "Mensuel"]

    weekly = click(svc, labelled(daily, "Hebdomadaire").data)

    assert weekly is not None and "weekly" in weekly
    assert commands_recorded(engine)[-1] == {"command": "report", "args": ["weekly"]}


# --- 3. a click is the typed command, audited, and revalidated ----------------


def test_a_click_runs_the_same_audited_command_as_the_typed_one(engine: Engine) -> None:
    svc = service(engine)
    demo = labelled(send(svc, "/mode"), "DÉMO")

    clicked = click(svc, demo.data)  # the first mode change, by button
    typed = send(svc, "/mode DEMO")  # the same change, typed out

    assert clicked is not None and "C'est fait" in clicked
    assert typed is not None
    # The click, then the typed form: two rows the audit log cannot tell apart.
    assert commands_recorded(engine)[-2:] == [
        {"command": "mode", "args": ["DEMO"]},
        {"command": "mode", "args": ["DEMO"]},
    ]
    actors = [row.actor for row in audit_rows(engine) if row.action == "command"]
    assert actors[-2:] == [f"telegram:{OPERATOR}", f"telegram:{OPERATOR}"]
    assert len(mode_events(engine)) == 2


def test_the_audit_row_is_written_before_the_clicked_command_runs(engine: Engine) -> None:
    seen: list[bool] = []

    async def probe(_: CommandRequest) -> str:
        seen.append(
            any(row.detail.get("command") == "probe" for row in AuditStore(engine).recent())
        )
        return "sonde exécutée"

    router = CommandRouter()
    router.register("probe", "sonde", probe)
    svc = CommandService(AccessGate({OPERATOR}), router, AuditStore(engine), now=Clock())

    answer = click(svc, encode_callback("probe", (), T0))

    assert answer is not None and "sonde exécutée" in answer
    assert seen == [True], "the click must be journalled before it is executed"


def test_a_command_that_cannot_be_journalled_is_not_run_by_a_click(tmp_path: Path) -> None:
    ran: list[str] = []
    broken = create_database_engine(f"sqlite:///{tmp_path / 'empty.db'}")

    async def act(_: CommandRequest) -> str:
        ran.append("ran")
        return "fait"

    router = CommandRouter()
    router.register("act", "agit", act)
    svc = CommandService(AccessGate({OPERATOR}), router, AuditStore(broken), now=Clock())
    try:
        answer = click(svc, encode_callback("act", (), T0))
    finally:
        broken.dispose()

    assert ran == []
    assert answer is not None and "journal" in answer


def test_a_stranger_clicking_a_button_gets_nothing_at_all(engine: Engine) -> None:
    svc = service(engine)
    data = labelled(send(svc, "/mode"), "DÉMO").data

    assert click(svc, data, user=STRANGER) is None
    assert click(svc, data, private=False) is None  # the operator, but not in a private chat

    assert mode_events(engine) == [], "an unusable click changes nothing"
    refusals = [row for row in audit_rows(engine) if row.action == "command_refused"]
    assert [(row.actor, row.detail["verdict"]) for row in refusals] == [
        (f"telegram:{STRANGER}", "unauthorized"),
        (f"telegram:{OPERATOR}", "not_private"),
    ]


def test_a_stale_button_is_refused_and_shows_the_current_choices(engine: Engine) -> None:
    clock = Clock()
    svc = service(engine, clock)
    data = labelled(send(svc, "/mode"), "DÉMO").data

    clock.advance(minutes=30)  # older than a button is allowed to live
    answer = click(svc, data)

    assert answer is not None
    assert "rien n'a été exécuté" in answer.lower()
    assert mode_events(engine) == [], "an outdated button never applies its action"
    assert answer.keyboard is not None, "the operator gets the current choices instead"


def test_a_fresh_button_is_accepted_and_its_life_is_long_enough_to_be_useful() -> None:
    assert timedelta(minutes=5) <= CALLBACK_TTL
    decoded = decode_callback(encode_callback("mode", ("DEMO",), T0))

    assert decoded is not None
    assert is_fresh(decoded, T0)
    assert is_fresh(decoded, T0 + CALLBACK_TTL)
    assert not is_fresh(decoded, T0 + CALLBACK_TTL + timedelta(seconds=1))
    assert not is_fresh(decoded, T0 - timedelta(seconds=1)), "a button from the future is refused"


def test_a_handmade_callback_cannot_reach_the_real_mode(engine: Engine) -> None:
    svc = service(engine)

    answer = click(svc, encode_callback("mode", ("LIVE",), T0))

    assert answer is not None
    assert "RM-000" in answer and "serveur" in answer
    assert mode_events(engine) == []
    assert commands_recorded(engine) == [{"command": "mode", "args": ["LIVE"]}], (
        "the attempt is journalled, only the change is not"
    )


def test_a_handmade_callback_naming_an_unknown_command_does_nothing(engine: Engine) -> None:
    svc = service(engine)

    answer = click(svc, encode_callback("rm_rf", (), T0))

    assert answer is not None and "inconnue" in answer.lower()
    assert mode_events(engine) == []
    assert not HaltStore(engine).status().halted


def test_malformed_callback_data_only_re_shows_the_palette(engine: Engine) -> None:
    svc = service(engine)
    garbage = (
        "",
        "mode DEMO",
        "c1:pasunnombre:mode:DEMO",
        "x1:1:mode:DEMO",
        "c1:1:",
        "c1:1:" + "A" * 200,
        "c1:1:MODE:DEMO",
    )

    for data in garbage:
        answer = click(svc, data)
        # Nothing is executed; the operator simply gets the palette back, as buttons.
        assert answer is not None and answer.keyboard is not None, data
        assert menu_commands(answer), data

    assert mode_events(engine) == []
    assert not HaltStore(engine).status().halted


def test_a_button_never_carries_a_secret_and_always_fits_telegram(engine: Engine) -> None:
    svc = service(engine)
    replies = [
        send(svc, "/mode"),
        send(svc, "/pause"),
        send(svc, "/resume"),
        send(svc, "/close_all"),
        send(svc, "/emergency_stop"),
        send(svc, "/restart"),
        send(svc, "/disable"),
        send(svc, "/disable XAUUSD"),
        send(svc, "/enable XAUUSD"),
        send(svc, "/marche XAUUSD"),
    ]
    seen: list[str] = []
    for reply in replies:
        for button in buttons(reply):
            data = button.data
            seen.append(data)
            assert data.startswith(CALLBACK_PREFIX + ":")
            assert CALLBACK_SHAPE.match(data), f"unexpected payload: {data!r}"
            assert len(data.encode()) <= MAX_CALLBACK_BYTES
            assert "://" not in data and " " not in data
    assert MAX_CALLBACK_BYTES <= 64, "Telegram refuses a longer callback_data"
    assert len(seen) >= 12


def test_the_confirmation_says_the_action_is_done_and_consumes_the_buttons(
    engine: Engine,
) -> None:
    svc = service(engine)

    done = click(svc, labelled(send(svc, "/pause"), "Confirmer la suspension").data)

    assert done is not None
    assert_readable(done)
    assert "fait" in done.lower()
    assert "suspendus" in done.lower(), "the operator is told the observable consequence"
    assert done.keyboard is None, "a consumed button must not be clickable twice"
    assert HaltStore(engine).status().halted


# --- 4. the choices that need a second step (the market ones) -----------------


def test_choosing_a_market_states_the_consequence_then_asks_to_confirm(engine: Engine) -> None:
    svc = service(engine)

    chosen = click(svc, labelled(send(svc, "/disable"), "BTCUSD").data)

    assert chosen is not None
    assert_readable(chosen)
    assert "BTCUSD" in chosen and "plus aucun signal" in chosen
    assert not HaltStore(engine).is_halted("market:BTCUSD"), "the choice alone changes nothing"
    assert commands_recorded(engine)[-1] == {"command": "disable", "args": ["BTCUSD"]}

    done = click(svc, labelled(chosen, "Confirmer la coupure de BTCUSD").data)

    assert done is not None and "fait" in done.lower()
    assert HaltStore(engine).is_halted("market:BTCUSD")
    assert commands_recorded(engine)[-1] == {
        "command": "disable",
        "args": ["BTCUSD", "confirmer"],
    }


def test_a_clicked_market_is_revalidated_against_the_configured_markets(engine: Engine) -> None:
    svc = service(engine)

    answer = click(svc, encode_callback("disable", ("EURUSD", "confirmer"), T0))

    assert answer is not None
    assert "EURUSD" in answer and "inconnu" in answer.lower()
    assert not HaltStore(engine).is_halted("market:EURUSD")


def test_re_enabling_a_market_does_not_re_enable_the_other_one(engine: Engine) -> None:
    svc = service(engine)
    click(svc, labelled(send(svc, "/disable"), "XAUUSD").data)
    click(svc, encode_callback("disable", ("XAUUSD", "confirmer"), T0))
    click(svc, labelled(send(svc, "/enable"), "XAUUSD").data)
    click(svc, encode_callback("enable", ("XAUUSD", "confirmer"), T0))

    halts = HaltStore(engine)
    assert not halts.is_halted("market:XAUUSD")
    assert not halts.is_halted("market:BTCUSD")


# --- 5. the adapter: an inline keyboard, never a permanent one ----------------


def test_a_keyboard_becomes_an_inline_keyboard() -> None:
    from telegram import InlineKeyboardMarkup

    from tradingagent.notify.telegram_app import to_markup

    assert to_markup(None) is None
    keyboard = Keyboard(
        ((Button("OBSERVATION", "c1:1:mode:OBSERVATION"), Button("SIGNAL", "c1:1:mode:SIGNAL")),)
    )
    markup = to_markup(keyboard)

    assert isinstance(markup, InlineKeyboardMarkup)
    assert [button.text for button in markup.inline_keyboard[0]] == ["OBSERVATION", "SIGNAL"]
    assert markup.inline_keyboard[0][0].callback_data == "c1:1:mode:OBSERVATION"
    assert not hasattr(markup, "resize_keyboard"), "a reply keyboard would stick to the screen"


def test_the_bot_listens_for_button_clicks(engine: Engine) -> None:
    from telegram import Update

    from tradingagent.notify.telegram_app import ALLOWED_UPDATES, build_application

    assert Update.MESSAGE in ALLOWED_UPDATES
    assert Update.CALLBACK_QUERY in ALLOWED_UPDATES

    # A real service, because the application now also carries the menu hook (`post_init`):
    # the menu is derived from the service's router, so `build_application` needs one.
    application = build_application("123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawE", service(engine))
    kinds = {type(handler).__name__ for handler in application.handlers[0]}
    assert "CallbackQueryHandler" in kinds
    assert "MessageHandler" in kinds


class FakeChat:
    PRIVATE = "private"
    type = PRIVATE


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeMessage:
    def __init__(self) -> None:
        self.chat = FakeChat()
        self.sent: list[tuple[str, object]] = []

    async def edit_text(
        self, text: str, reply_markup: object = None, parse_mode: str | None = None
    ) -> None:
        self.sent.append((text, reply_markup))

    async def reply_text(
        self, text: str, reply_markup: object = None, parse_mode: str | None = None
    ) -> None:
        self.sent.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answers = 0

    async def answer(self) -> None:
        self.answers += 1


class FakeUpdate:
    def __init__(self, query: FakeQuery, user_id: int) -> None:
        self.callback_query = query
        self.effective_message = query.message
        self.effective_user = FakeUser(user_id)
        self.effective_chat = query.message.chat


def run_callback(svc: CommandService, data: str, user_id: int = OPERATOR) -> FakeUpdate:
    from tradingagent.notify.telegram_app import callback_handler

    update = FakeUpdate(FakeQuery(data, FakeMessage()), user_id)
    asyncio.run(callback_handler(svc)(cast(Any, update), cast(Any, None)))
    return update


def test_a_click_edits_the_message_so_the_old_button_disappears(engine: Engine) -> None:
    svc = service(engine)
    data = labelled(send(svc, "/mode"), "DÉMO").data

    update = run_callback(svc, data)

    assert update.callback_query.answers == 1, "the client spinner is released"
    text, markup = update.callback_query.message.sent[-1]
    assert "C'est fait" in text
    assert markup is None, "the confirmation removes the keyboard"
    assert mode_events(engine)[0].detail["requested"] == "DEMO"


def test_a_stranger_clicking_from_telegram_sends_nothing(engine: Engine) -> None:
    svc = service(engine)
    data = labelled(send(svc, "/mode"), "DÉMO").data

    update = run_callback(svc, data, user_id=STRANGER)

    assert update.callback_query.message.sent == []
    assert update.callback_query.answers == 1, "an empty acknowledgement carries no content"
    assert mode_events(engine) == []
