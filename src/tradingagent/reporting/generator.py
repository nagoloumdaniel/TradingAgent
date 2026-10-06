"""The single report generator (F-022, C-006, TASK-042).

Every number is computed here, from the database only, with the shared analytics
package; the model, when one is wired, may only comment on the finished text. The
numeric report is complete on its own: a missing model never costs a report.
"""

from decimal import Decimal

from tradingagent.analytics import Performance, Trade, compute_performance, group
from tradingagent.analytics.axes import Axis
from tradingagent.reporting.schedule import Period, Window
from tradingagent.storage.account import AccountStore, ReportData

EUR = "\N{EURO SIGN} "


def _money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{EUR}{value:+.2f}" if value else f"{EUR}0.00"


def _rate(value: Decimal | float | None) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f} %"


def _advice(performance: Performance) -> str:
    """A documented heuristic, not a decision: promotion stays with the operator (RM-016)."""
    if performance.trades == 0:
        return "aucun trade"
    factor = performance.profit_factor
    if factor is None:
        return "surveiller (aucune perte enregistrée, échantillon petit)"
    if factor >= 1.5:
        return "maintenir"
    if factor >= 1.0:
        return "surveiller"
    return "suspendre"


class ReportGenerator:
    def __init__(self, data: ReportData, account: AccountStore) -> None:
        self._data = data
        self._account = account

    def build(self, window: Window) -> str:
        trades = self._data.trades_between(window.start, window.end)
        performance = compute_performance(trades)
        refused = self._data.refused_risk_between(window.start, window.end)
        counts = self._data.signal_counts_between(window.start, window.end)
        anomalies = self._data.anomalies_between(window.start, window.end)
        opening = self._account.latest_before(window.start)
        closing = self._account.latest_before(window.end)

        lines = [
            f"Rapport {window.period.value} — {window.start:%Y-%m-%d} au "
            f"{window.end:%Y-%m-%d} (UTC)",
            "",
            f"Solde d'ouverture : {_money(opening.equity if opening else None)}",
            f"Solde de clôture : {_money(closing.equity if closing else None)}",
            f"Résultat de la période : {_money(performance.net_profit)}",
            f"Positions clôturées : {performance.trades} "
            f"(gagnantes {performance.wins}, perdantes {performance.losses})",
            f"Taux de réussite : {_rate(performance.win_rate if performance.win_rate else None)}",
            f"Drawdown maximal : {_money(performance.max_drawdown)}",
            "",
            "Risque :",
            f"  Signaux produits : {counts.total} (validés {counts.validated}, "
            f"refusés par le risque {counts.refused})",
            f"  Pertes évitées par les refus du risque : "
            f"{refused.count} refus, {_money(refused.avoided_risk_eur)} de risque évité",
            f"  Anomalies système (warning et plus) : {anomalies}",
            f"  Marchés actifs : "
            f"{', '.join(self._data.active_markets_between(window.start, window.end)) or 'aucun'}",
        ]
        if window.period is not Period.DAILY:
            lines += self._breakdown(trades, "Par marché", Axis.MARKET)
            lines += self._breakdown(trades, "Par stratégie", Axis.STRATEGY)
            lines += self._previous_comparison(window, performance)
            lines += self._recommendations(trades)
        return "\n".join(lines)

    def _breakdown(self, trades: list[Trade], title: str, axis: Axis) -> list[str]:
        lines = ["" + title + " :"]
        buckets = group(trades, axis)
        if not buckets:
            lines.append("  aucun trade")
        for key, bucket in sorted(buckets.items()):
            performance = compute_performance(bucket)
            lines.append(
                f"  {key} : {performance.trades} trade(s), "
                f"{_money(performance.net_profit)}, "
                f"réussite {_rate(performance.win_rate if performance.win_rate else None)}"
            )
        return lines

    def _previous_comparison(self, window: Window, performance: Performance) -> list[str]:
        length = window.end - window.start
        previous = self._data.trades_between(window.start - length, window.start)
        previous_net = compute_performance(previous).net_profit
        direction = (
            "en hausse"
            if performance.net_profit > previous_net
            else ("en baisse" if performance.net_profit < previous_net else "stable")
        )
        return [
            "",
            f"Comparaison avec la période précédente : {_money(previous_net)} → "
            f"{_money(performance.net_profit)} ({direction})",
        ]

    def _recommendations(self, trades: list[Trade]) -> list[str]:
        lines = ["", "Stratégies (avis automatique, décision de l'opérateur) :"]
        buckets = group(trades, Axis.STRATEGY)
        if not buckets:
            lines.append("  aucune donnée")
        for key, bucket in sorted(buckets.items()):
            performance = compute_performance(bucket)
            lines.append(f"  {key} : {_advice(performance)}")
        return lines
