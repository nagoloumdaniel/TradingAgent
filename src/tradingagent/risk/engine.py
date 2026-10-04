"""The last word before any order (F-011, TASK-035): authorize, reduce or refuse.

An account that does not match the mode is not a refusal but a fatal error (RM-017): the
execution component must stop. Everything else is a decision with every reason attached.
"""

from dataclasses import dataclass
from typing import Any

from tradingagent.core.mode import TradingMode
from tradingagent.core.states import RiskOutcome
from tradingagent.risk.checks import CHECKS, CheckResult, RiskContext, check_stop_loss
from tradingagent.risk.model import AccountState
from tradingagent.risk.sizing import Sizing, SizingError, size_position


class AccountModeMismatchError(Exception):
    """RM-017: the terminal's account contradicts the configured account or mode."""


def verify_account(account: AccountState, expected_login: int, mode: TradingMode) -> None:
    if account.login != expected_login:
        raise AccountModeMismatchError(
            f"terminal account {account.login} is not the configured {expected_login}"
        )
    if mode is TradingMode.LIVE and account.is_demo:
        raise AccountModeMismatchError("LIVE mode on a demo account")
    if mode is not TradingMode.LIVE and not account.is_demo:
        raise AccountModeMismatchError(f"real account detected in {mode} mode")


@dataclass(frozen=True)
class RiskDecision:
    outcome: RiskOutcome
    reason: str
    checks: tuple[CheckResult, ...]
    sizing: Sizing | None

    @property
    def refusals(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def checks_record(self) -> dict[str, Any]:
        return {
            check.name: {"passed": check.passed, "reason": check.reason} for check in self.checks
        }


def decide(ctx: RiskContext, expected_login: int) -> RiskDecision:
    """Run every check, size the position, and decide. Raises only on RM-017."""
    verify_account(ctx.account, expected_login, ctx.limits.mode)
    results = [check(ctx) for check in CHECKS]
    sizing, sizing_check = _size(ctx)
    results.append(sizing_check)
    checks = tuple(results)

    failed = [check for check in checks if not check.passed]
    if failed:
        reason = "; ".join(f"{check.name}: {check.reason}" for check in failed)
        return RiskDecision(RiskOutcome.REFUSED, reason, checks, None)
    if sizing is None:  # unreachable: a passed sizing check always carries its sizing
        return RiskDecision(RiskOutcome.REFUSED, "sizing: no size computed", checks, None)
    if sizing.limited_by != "risk":
        return RiskDecision(
            RiskOutcome.REDUCED,
            f"{sizing.volume} lot, limited by {sizing.limited_by} below the risk budget "
            f"({sizing.risk_volume:.5f} lot)",
            checks,
            sizing,
        )
    return RiskDecision(
        RiskOutcome.AUTHORIZED,
        f"{sizing.volume} lot, {sizing.risk_eur:.2f} EUR at risk",
        checks,
        sizing,
    )


def _size(ctx: RiskContext) -> tuple[Sizing | None, CheckResult]:
    distance = ctx.stop_distance
    if distance is None or not check_stop_loss(ctx).passed:
        return None, CheckResult("sizing", False, "not computed without a valid stop")
    limits = ctx.limits
    try:
        sizing = size_position(
            capital=limits.capital(ctx.account.equity),
            risk_per_trade=limits.risk_per_trade,
            stop_distance=distance,
            spec=ctx.spec,
            quote=ctx.quote,
            free_margin=ctx.account.free_margin,
            margin_usage=limits.margin_usage,
            max_volume=limits.max_volume,
        )
    except SizingError as error:
        return None, CheckResult("sizing", False, str(error))
    return sizing, CheckResult(
        "sizing", True, f"{sizing.volume} lot, limited by {sizing.limited_by}"
    )
