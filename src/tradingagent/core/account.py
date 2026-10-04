from tradingagent.core.mode import TradingMode


class AccountModeMismatchError(Exception):
    """RM-017: the terminal's account contradicts the configured account or mode. Fatal:
    whoever sees it must stop trading, never treat it as an ordinary refusal."""


def verify_account_mode(login: int, is_demo: bool, expected_login: int, mode: TradingMode) -> None:
    """The single RM-017 rule, shared by the market connection and the risk engine."""
    if login != expected_login:
        raise AccountModeMismatchError(
            f"terminal is logged into account {login}, configured account is {expected_login}"
        )
    if mode is TradingMode.LIVE and is_demo:
        raise AccountModeMismatchError(
            "LIVE mode requires a real account, the terminal reports a demo"
        )
    if mode is not TradingMode.LIVE and not is_demo:
        raise AccountModeMismatchError(
            f"{mode} mode requires a demo account, the terminal reports a real one"
        )
