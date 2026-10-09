"""Le journal console ne doit jamais perdre une ligne à cause d'un accent.

**Le défaut réparé ici.** L'agent tourne en service : sa sortie standard est redirigée vers un
fichier, donc il bascule en journalisation JSON — purement ASCII, jamais en échec. Mais le
`ConsoleNotifier` **imprime quand même sur `sys.stdout`** pour montrer les notifications dans la
console. Or `use_utf8_console()` n'était appelé que dans la branche interactive, après le
`return` du mode machine : la sortie standard restait dans la page de codes héritée de Windows
(cp1252), et chaque ligne accentuée levait `UnicodeEncodeError`.

La conséquence n'était pas cosmétique. Le 2026-10-09 à 03:45, un ordre a été refusé par le
courtier et le journal n'a **rien** conservé de l'incident : six lignes
`UnicodeEncodeError: 'charmap' codec can't encode characters` à la place. Un incident sans
trace est un incident qu'on ne peut pas diagnostiquer.
"""

import sys
from unittest import mock

from tradingagent.console import use_utf8_console


class Recorder:
    """Un flux minimal qui retient ce qu'on lui demande, comme le ferait un vrai."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs: str) -> None:
        self.calls.append(kwargs)
        if "encoding" in kwargs:
            self.encoding = kwargs["encoding"]

    def write(self, text: str) -> int:
        return len(text)


def test_both_streams_are_moved_to_utf8() -> None:
    """La sortie standard **et** la sortie d'erreur : le notifier écrit sur la première."""
    out, err = Recorder("cp1252"), Recorder("cp1252")

    with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
        use_utf8_console()

    assert out.encoding == "utf-8"
    assert err.encoding == "utf-8"


def test_a_character_the_legacy_code_page_cannot_encode_survives() -> None:
    """C'est le cas réel : un accent français dans une notification Telegram.

    Le test écrit dans un flux reconfiguré et vérifie que rien n'est perdu. Avec cp1252, cette
    même écriture lève — c'est exactement ce que le journal montrait.
    """
    out = Recorder("cp1252")
    err = Recorder("cp1252")

    with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
        use_utf8_console()

    line = "Ordre refusé par le courtier · BTCUSD ACHAT"
    assert out.encoding == "utf-8"
    line.encode(out.encoding)  # ne lève pas : c'est toute la garantie


def test_a_stream_without_reconfigure_is_left_alone() -> None:
    """Certains flux n'exposent pas `reconfigure` : on ne doit pas échouer pour autant."""

    class Bare:
        encoding = "cp1252"

    with mock.patch.object(sys, "stdout", Bare()), mock.patch.object(sys, "stderr", Bare()):
        use_utf8_console()  # ne lève pas


def test_errors_are_replaced_rather_than_raising() -> None:
    """`errors="replace"` : un caractère inconvertible ne doit jamais perdre une ligne."""
    out = Recorder("cp1252")
    err = Recorder("cp1252")

    with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
        use_utf8_console()

    assert out.calls
    assert out.calls[0]["errors"] == "replace"
