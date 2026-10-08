### Jeux geles H1/H4 -- source courte (11 999 bougies M15)

| Marché | UT | Bougies écrites | Fenêtre UTC | Empreinte SHA-256 | Bougies écartées (incomplet / vide) | Trous dans la série | Bougies M15 non pliées | dont bords d'export | dont bords de fermeture élargie | dont non expliqués | dont marché fermé | Fichier |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSD | H1 | 2998 | 2026-06-05 00:00 -> 2026-10-08 02:00 | `4f95e09e92ed42d6...` | 3 / 1 | 2 | 9 | 0 | 0 | 9 | 0 | `BTCUSD-H1-from-M15-2026-10-08.jsonl` |
| XAUUSD | H1 | 2980 | 2026-04-07 18:00 -> 2026-10-08 02:00 | `94c8cc28c24fd8d5...` | 27 / 1394 | 132 | 5605 | 1 | 8 | 0 | 5596 | `XAUUSD-H1-from-M15-2026-10-08.jsonl` |
| BTCUSD | H4 | 747 | 2026-06-05 00:00 -> 2026-10-08 00:00 | `4bedec02b7e6dc45...` | 4 / 0 | 2 | 17 | 8 | 0 | 9 | 0 | `BTCUSD-H4-from-M15-2026-10-08.jsonl` |
| XAUUSD | H4 | 651 | 2026-04-08 00:00 -> 2026-10-07 20:00 | `4631ba0ecb9f9ed9...` | 161 / 289 | 130 | 5617 | 13 | 8 | 0 | 5596 | `XAUUSD-H4-from-M15-2026-10-08.jsonl` |

### Jeux geles H1/H4 -- source courte (11 999 bougies M15)

| Marché | UT | Bougies | Manifeste du marché | Historique déclaré | Bougies par fold | Folds jouables | Validation | Verdict |
|---|---|---|---|---|---|---|---|---|
| BTCUSD | H1 | 2998 | trend_breakout@1.0.0 | 600 | 1450 | 8 | split 1798/599/601, validation too short for history 600 | utilisable |
| BTCUSD | H1 | 2998 | trend_breakout@1.0.1 | 600 | 1450 | 8 | split 1798/599/601, validation too short for history 600 | utilisable |
| XAUUSD | H1 | 2980 | witness@1.1.0 | 300 | 900 | 11 | split 1788/596/596, validation serves history 300 | utilisable |
| XAUUSD | H1 | 2980 | witness@1.1.1 | 300 | 900 | 11 | split 1788/596/596, validation serves history 300 | utilisable |
| BTCUSD | H4 | 747 | trend_breakout@1.0.0 | 600 | 1450 | 0 | split 448/149/150, validation too short for history 600 | AUCUN FOLD |
| BTCUSD | H4 | 747 | trend_breakout@1.0.1 | 600 | 1450 | 0 | split 448/149/150, validation too short for history 600 | AUCUN FOLD |
| XAUUSD | H4 | 651 | witness@1.1.0 | 300 | 900 | 0 | split 390/130/131, validation too short for history 300 | AUCUN FOLD |
| XAUUSD | H4 | 651 | witness@1.1.1 | 300 | 900 | 0 | split 390/130/131, validation too short for history 300 | AUCUN FOLD |
