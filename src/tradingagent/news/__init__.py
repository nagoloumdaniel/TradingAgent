"""Economic announcements and the news that moves gold and bitcoin.

The package answers three questions and only three:

* what is announced, and when (the sources, kept in `storage.news`);
* how much it should matter to XAUUSD and BTCUSD (`model.influence_score`, deterministic);
* what a strategy must do about it (`blackout.news_blackout`, a pre-risk gate that can only
  REFUSE, and the AI reading in `analyst`, which is commentary and never a trade order).

Nothing here creates a signal, sizes a position or loosens a stop (C-002). The pure modules
(`model`, `stance`, `fallback`, `blackout`, `config`) read no clock and do no I/O; the clock is
always an argument.
"""
