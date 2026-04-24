# Audit 20260423 — Agent Prompt Pack

Ready-to-paste system prompts for implementing every recommendation from `scratch/audit_20260423/audit_report.md`.

## How to use

1. **Every agent session must see `00_common_preamble.md` first.** Paste its contents as the system prompt (or as the first message if your agent framework doesn't expose a "system" slot), then paste the task file as the user's first turn. If the framework accepts a single "system" field, concatenate `00_common_preamble.md` + the task file with a clear `---` separator.
2. **Run tasks in the recommended order** (below). Some tasks depend on others; the dependency column calls this out.
3. **One agent = one task = one PR.** Do not hand an agent multiple tasks; context conflicts produce messy diffs.
4. **The senior auditor reviews each PR against the task's Definition of Done** before approving merge.
5. **If an agent escalates**, return the escalation to the auditor with the agent's PR draft and blocker description — do not route around the safety.

## Recommended Rollout Order

| Order | Task ID | File | Depends on | Est. effort | Ship risk |
|---|---|---|---|---|---|
| 1 | P4-1 | `P3-3_P4_ops_hygiene.md` (sub-task) | — | 15 min | low (ops, reversible) |
| 2 | P2-1 | `P2-1_loop_cadence.md` | — | 30 min | low |
| 3 | P1   | `P1_model_quality.md` | — | 2–4 h | low |
| 4 | P0-1 | `P0-1_optimizer_min_notional.md` | — | 30 min | **medium — materially changes allocation sizing** |
| 5 | P0-3 | `P0-3_optimizer_flatten_held.md` | P0-1 landed | 2 h | medium |
| 6 | P0-2 | `P0-2_time_stop.md` | — | 3 h | medium (new behaviour) |
| 7 | P3-1 | `P3-1_model_version_persist.md` | — | 1 h | low |
| 8 | P3-3 | `P3-3_P4_ops_hygiene.md` (sub-task) | No other task uses `quant_execution` | 30 min | low |
| 9 | P4-2 | `P3-3_P4_ops_hygiene.md` (sub-task) | — | 1 h | low |
| 10 | P2-2 | `P2-2_feature_cache.md` | P2-1 landed | 3–4 h | medium (refactor) |
| 11 | P3-2 | `P3-2_quiet_hour_digest.md` | — | 2 h | low (opt-in) |

**Fastest impact path**: P4-1 → P2-1 → P1 → P0-1 → P0-3 → P0-2 ships every structural fix in roughly one working day of agent-time.

## Validation After Each Merge

- CI `pytest` green.
- Deploy to production.
- Watch `quant_telegram` logs for 1 hour after each deploy:
  ```bash
  ssh -i ./quant-key.pem ubuntu@13.48.85.88 "docker logs --since 1h quant_telegram 2>&1 | tail -200"
  ```
- Check `docker stats` for resource regressions.
- For P0 changes: verify the `"Optimizer: N symbols → M after filter"` distribution shifts toward `M > 0` more often.
- For P2-1: verify no `429` Binance responses and no spike in `httpx.ReadError`.

## Post-deployment KPIs (revisit in 48 h)

- **Filter pass-through rate**: should rise from 15.8% → ≥ 40%.
- **Optimizer symbol diversity**: ≥ 2 symbols should appear in weights in ≥ 30% of cycles.
- **Mean paper position age at close**: below `BOT_V2_MAX_HOLD_HOURS` (default 12 h) with time-stop active.
- **Scorecard coverage**: ≥ 50% of symbols at `hit_rate ≥ 0.50` after 48 h on the expanded retrain universe.
- **Binance request rate**: ≤ 100 req/min average post-P2-1, ≤ 60 after P2-2.
- **Disk free**: ≥ 20 GB after P4-1.

## Prompt Files

| File | Purpose |
|---|---|
| `00_common_preamble.md` | **Paste first, every session.** Operating rules, verification workflow, escalation triggers, definition of done. |
| `P0-1_optimizer_min_notional.md` | Optimizer min-notional floor fix (2% → 0.5% of equity). |
| `P0-2_time_stop.md` | Time-stop safety net for stuck paper positions. |
| `P0-3_optimizer_flatten_held.md` | Optimizer synthesises flatten targets for held-but-silent symbols. |
| `P1_model_quality.md` | Retrain universe expansion + promotion-gate audit + scorecard-band recommendation. |
| `P2-1_loop_cadence.md` | Signal-loop cadence 3600 s → 900 s. |
| `P2-2_feature_cache.md` | Per-cycle shared feature/prediction cache across users. |
| `P3-1_model_version_persist.md` | Persist `active_model_version` to `user_context` on rotation. |
| `P3-2_quiet_hour_digest.md` | Opt-in Telegram heartbeat on all-HOLD cycles. |
| `P3-3_P4_ops_hygiene.md` | Remove zombie `execution_engine`, disk prune, SQLite WAL checkpoint. |

## Reviewer's Acceptance Checklist (per PR)

Copy and complete before approving merge:

```
- [ ] Branch naming matches spec: fix/<task-id>-<slug>
- [ ] Diff scope matches task's allow-list — no extra files
- [ ] Definition of Done items all checked in PR body
- [ ] pytest tail pasted, all green
- [ ] pyflakes / ruff output clean for touched files
- [ ] New tests actually exercise the new behaviour (read them, don't just trust the count)
- [ ] No new dependencies
- [ ] No state/DB migration side-effects
- [ ] Logging format preserved where ops relies on grep patterns
- [ ] Backwards-compat where task spec required it (kw-only args, default values)
- [ ] Escalation notes (if any) addressed or deferred with follow-up ticket
```
