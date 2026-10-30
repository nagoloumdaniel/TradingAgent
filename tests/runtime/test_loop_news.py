"""The loop refreshes the calendar in the background and never waits for it."""

import asyncio
from datetime import datetime

from sqlalchemy import Engine
from tests.runtime.test_loop import build_loop

from tradingagent.news.blackout import CLEAR, BlackoutDecision


class SlowNews:
    def __init__(self, *, fails: bool = False) -> None:
        self.ticks: list[datetime] = []
        self.release = asyncio.Event()
        self.fails = fails

    async def tick(self, now: datetime) -> object:
        self.ticks.append(now)
        await self.release.wait()
        return None

    def check(self, symbol: str, now: datetime) -> BlackoutDecision:
        return CLEAR

    def upcoming_summary(self, now: datetime, hours: int = 24, limit: int = 8) -> str:
        return ""


def test_a_slow_refresh_never_delays_the_cycle_nor_runs_twice(engine: Engine) -> None:
    loop, *_ = build_loop(engine)
    news = SlowNews()
    loop._news = news  # the composition root passes it; the harness builds the loop without one

    async def scenario() -> None:
        await loop.run_once()
        await loop.run_once()  # the first refresh is still waiting: no second one starts
        await asyncio.sleep(0)
        assert len(news.ticks) == 1
        news.release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await loop.run_once()
        await asyncio.sleep(0)
        assert len(news.ticks) == 2

    asyncio.run(scenario())


def test_a_loop_without_news_just_works(engine: Engine) -> None:
    loop, *_ = build_loop(engine)
    asyncio.run(loop.run_once())
