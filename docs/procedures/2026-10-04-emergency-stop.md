# Procédure de test : arrêt d'urgence (TASK-036)

Exécutée le 2026-10-04 avec la commande serveur `uv run tradingagent`. Chaque étape lance un processus distinct : relire l'état dans un nouveau processus équivaut à un redémarrage de l'agent.

La base utilisée était une base SQLite jetable, migrée avec le schéma réel. **La procédure est à rejouer sur Supabase** dès que `DATABASE_URL` sera renseignée.

| # | Commande | Attendu | Obtenu |
|---|---|---|---|
| 1 | `status` | aucun arrêt | `TRADING: no halt active` |
| 2 | `halt --reason "procedure TASK-036" --actor daniel` | arrêt global, avec motif, auteur et heure | `HALTED`, puis `global: procedure TASK-036 (server, daniel, …)` |
| 3 | `status` dans un nouveau processus | l'arrêt survit au redémarrage | `HALTED`, même motif |
| 4 | `resume --reason "procedure terminee"` | reprise | `TRADING: no halt active` |
| 5 | `halt --reason "test fermeture" --close-positions` | fermeture annoncée seulement parce qu'elle est demandée | `HALTED (closing open positions)` |
| 6 | `status` sur une base sans la table | refus par défaut | **1ʳᵉ exécution : défaut trouvé.** L'état était bien « arrêté », mais l'affichage des quarantaines plantait. Corrigé et couvert par un test. **2ᵉ exécution :** `HALTED`, `halt state unreadable, refusing`, `QUARANTINES UNREADABLE` |
| 7 | `resume --reason "fin"` | reprise | `TRADING: no halt active` |

## Critères d'acceptation

- **L'état d'arrêt survit au redémarrage :** vérifié à l'étape 3, et par les tests `test_the_halt_survives_a_restart` et `test_a_quarantine_survives_a_restart`.
- **Aucune clôture de position sans activation explicite :** vérifié aux étapes 2 et 5. Un arrêt automatique ne demande jamais de fermeture (test `test_reaching_the_weekly_loss_halts_the_agent`).
- **Un état illisible ou corrompu conduit au refus :** vérifié à l'étape 6, et par les tests `test_an_unreadable_state_halts_trading` et `test_an_unreadable_quarantine_keeps_the_pair_stopped`.

## Rejouée sur Supabase (production), le 2026-10-04 à 19 h 24 UTC

Étapes 1 à 4 rejouées sur la base de production : `status`, `halt`, `status` dans un nouveau processus, puis `resume`. Le résultat est identique à celui de la base jetable : l'arrêt survit au redémarrage et la reprise le lève. Les deux commandes restent dans `halt_commands`, qui conserve tout l'historique.
