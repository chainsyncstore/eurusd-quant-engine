# TASK P3-3 + P4 — Ops Hygiene: remove zombie execution container, disk cleanup, WAL checkpoint

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there. This task contains operations on the production host. **You must never auto-run destructive commands.** Print each command for explicit user approval before running.

## Context (why this matters)

The audit found:

- **`quant_execution` container is idle**: 0 log lines in 10 days. Telegram runs its own `InMemoryExecutionService`; the dedicated execution container is a zombie consuming RAM. Either wire it up (non-trivial) or remove it (fast).
- **Disk 91% full**: 27 GB used / 29 GB total, 2.9 GB free. `docker system df` reports 19.86 GB reclaimable (99% of images). Near-term outage risk.
- **SQLite WAL bloat**: `state/quant_bot.db` is 12 KB but `quant_bot.db-wal` is 3.97 MB.

This task has three independent sub-tasks. Execute each as a separate commit.

## Remote Host Details

- Host: `ubuntu@13.48.85.88` (EC2 t3.medium).
- SSH key: `C:\Users\HP\Downloads\hypothesis-research-engine\quant-key.pem`.
- State dir on host: `/home/ubuntu/quant_bot/state/`.
- Docker compose file on host: `/home/ubuntu/quant_bot/docker-compose.yml` (confirm via `ls` first).

## Scope

- **Modify (local repo)**:
  - `docker-compose.yml` at repo root — remove the `execution_engine` service block.
  - `quant_v2/state/` or wherever SQLite connection lifecycle is managed — add a checkpoint helper.
- **Run (on remote host, with explicit user approval per command)**:
  - `docker compose down execution_engine` (stop + remove just that container).
  - `docker image prune -a -f` (after confirming reclaimable size matches audit).
  - WAL checkpoint against `quant_bot.db`.
- **Add tests** for the WAL checkpoint helper.
- **Do not modify**: any other compose service, any other data, `.env` on the host.

---

## Sub-task P3-3 — Remove `execution_engine` compose service

### Preconditions

1. Confirm no pending P0/P1/P2 task depends on `quant_execution`. Grep `quant_execution` and `stream:cmd:exec` across the repo. If any code path enqueues to that stream or polls its output, STOP and escalate — the container may be used in a code path not exercised by the audit-window data.
2. Read the current `docker-compose.yml` service block for `execution_engine`. Note: image name, volume mounts, depends_on relationships, environment.

### Exact Changes (local repo)

Edit `docker-compose.yml`:
- Remove the entire `execution_engine:` service block.
- Remove any `depends_on` reference to `execution_engine` from other services (most likely in `telegram_bot`).
- Leave `redis` in place — other services may still use it. Verify by grep.

### Remote Host Actions (execute with user approval, one at a time)

For each command, print it, ask for approval, then run only after user confirms.

```bash
# Dry-run inspection first (safe)
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'"

# Stop and remove the execution container
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "docker stop quant_execution && docker rm quant_execution"

# After repo change is deployed, verify compose no longer lists it
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "cd /home/ubuntu/quant_bot && docker compose config --services"
```

### Tests

Add `tests/infra/test_docker_compose_services.py`:

```python
import yaml
from pathlib import Path

def test_execution_engine_service_removed():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    data = yaml.safe_load(compose_path.read_text())
    assert "execution_engine" not in data.get("services", {}), (
        "quant_execution service was removed in audit_20260423 P3-3; "
        "re-enable only after wiring Redis bus publishing in telegram bot."
    )
```

This is a tripwire preventing accidental re-introduction.

### Definition of Done (P3-3)

- [ ] `docker-compose.yml` no longer declares `execution_engine`.
- [ ] No other service references it in `depends_on`, `links`, or env.
- [ ] Tripwire test passes.
- [ ] Remote container stopped and removed (after user approval).
- [ ] PR body includes `docker ps -a` output before and after.

---

## Sub-task P4-1 — Disk reclamation via `docker image prune`

### Preconditions

Confirm reclaimable size before pruning:

```bash
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "docker system df"
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "df -h /"
```

**Print the output**. If reclaimable is meaningfully different from the audit's 19.86 GB (i.e. off by >50%), STOP and re-consult the audit — something material changed.

### Action (requires user approval)

```bash
# DANGEROUS: removes all unused images. Print this command and wait for explicit approval.
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "docker image prune -a -f"

# Also prune builder cache (typically safe)
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "docker builder prune -f"

# Verify
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "df -h /"
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "docker system df"
ssh -i ./quant-key.pem ubuntu@13.48.85.88 "docker ps -a"
```

Do NOT run `docker volume prune`. Volumes may contain state/models.

### Deliverable

A short markdown file at `scratch/audit_20260423/ops_notes/P4-1_disk_prune.md` with:
- Before / after `df -h /` output.
- Before / after `docker system df` output.
- Timestamp of the prune.
- Confirmation that all 4 production containers (`quant_telegram`, `quant_retrain`, `quant_redis`, previously `quant_execution` removed) are still up (via `docker ps`).

### Follow-up Recommendation

Add an entry to the PR body recommending (not implementing) a weekly cron:

```
0 3 * * 0 /usr/bin/docker image prune -a -f 2>&1 | logger -t docker-prune
```

This is a recommendation only, not part of this task's scope.

### Definition of Done (P4-1)

- [ ] Pre/post metrics captured in `scratch/audit_20260423/ops_notes/P4-1_disk_prune.md`.
- [ ] Free space increased by at least 15 GB.
- [ ] All expected containers still running.

---

## Sub-task P4-2 — SQLite WAL checkpoint helper

### Exact Changes

Find the module that opens the SQLAlchemy engine for `quant_bot.db`. Likely `quant/db/session.py`, `quant/db/__init__.py`, or similar (grep for `create_engine` and `sqlite`).

Add a helper function (same module):

```python
def checkpoint_wal(engine) -> tuple[int, int]:
    """Run PRAGMA wal_checkpoint(TRUNCATE) to prevent WAL bloat.

    Returns (pages_checkpointed, wal_size_frames_before).
    Safe to call on a live DB: TRUNCATE blocks only briefly.
    Silently no-ops on non-SQLite engines.
    """
    from sqlalchemy import text
    if engine.url.get_backend_name() != "sqlite":
        return (0, 0)
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE);")).fetchone()
        # Result format: (busy, log_frames, checkpointed_frames)
        if result is None:
            return (0, 0)
        return (int(result[2]) if len(result) > 2 else 0,
                int(result[1]) if len(result) > 1 else 0)
```

Wire a call in the bot's shutdown path. Find the `main()` or equivalent in `quant/telebot/main.py`. If there is a clean-shutdown `finally` block or signal handler, call `checkpoint_wal(engine)` there. If no such block exists, add a minimal one around `application.run_polling(...)` at line ~3309:

```python
try:
    application.run_polling(drop_pending_updates=True)
finally:
    try:
        from quant.db.session import checkpoint_wal, engine  # adjust import to actual
        pages, frames = checkpoint_wal(engine)
        logger.info("SQLite WAL checkpoint: pages=%d frames=%d", pages, frames)
    except Exception as exc:
        logger.warning("WAL checkpoint on shutdown failed: %s", exc)
```

Additionally, expose an admin Telegram command? NO — out of scope. Shutdown-only is sufficient.

### Tests

`tests/quant/db/test_wal_checkpoint.py`:

1. `test_checkpoint_wal_returns_zeros_on_empty_db` — create in-memory SQLite engine, call `checkpoint_wal`. Assert returns non-negative tuple.

2. `test_checkpoint_wal_noop_on_non_sqlite` — mock engine with backend `"postgresql"`, assert returns `(0, 0)` without raising.

3. `test_checkpoint_wal_after_writes_reduces_frames` — create file-backed SQLite with WAL mode, do 100 inserts, call `checkpoint_wal`, assert returned frames > 0.

### Definition of Done (P4-2)

- [ ] `checkpoint_wal` helper added to the DB session module.
- [ ] Called on bot shutdown via `finally` block.
- [ ] 3 new tests pass.
- [ ] PR title: `fix(db): checkpoint SQLite WAL on bot shutdown to prevent bloat`.

### Remote Action (requires user approval)

One-time manual checkpoint to clear the current 3.97 MB WAL:

```bash
ssh -i ./quant-key.pem ubuntu@13.48.85.88 \
  "sqlite3 /home/ubuntu/quant_bot/state/quant_bot.db 'PRAGMA wal_checkpoint(TRUNCATE); VACUUM;'"
```

**Pre-requisite**: coordinate with the bot's activity — preferably run during a low-traffic window. The TRUNCATE blocks briefly (< 1 s for a 4 MB WAL). Confirm with user before running.

---

## Combined Definition of Done (All Sub-tasks)

- [ ] Three commits on branch `chore/audit-ops-hygiene`: P3-3, P4-1 (notes only), P4-2.
- [ ] `docker-compose.yml` no longer declares `execution_engine`.
- [ ] `checkpoint_wal` helper + shutdown wiring + tests.
- [ ] Remote actions executed only after explicit user approval, each with before/after evidence.
- [ ] PR body contains:
  - Links to the audit findings for P3-3, P4-1, P4-2.
  - `docker ps` output before and after container removal.
  - `df -h /` output before and after disk prune.
  - SQLite WAL size before and after checkpoint.

## Common Pitfalls — Do NOT do any of these

- ❌ Running `docker volume prune`. Volumes may hold model files and DB state.
- ❌ Running `docker system prune -a --volumes`. Same risk.
- ❌ Deleting `state/quant_bot.db` or `state/quant_bot.db-wal` manually. Use the checkpoint PRAGMA; NEVER `rm` the WAL while the bot is running.
- ❌ Running any destructive command without printing it first and waiting for user approval.
- ❌ Treating this as "infra-only, no tests needed". The tripwire test and WAL helper tests are both in scope.
- ❌ Re-enabling `execution_engine` in a later PR without also wiring telegram to publish to `stream:cmd:exec`. The tripwire test in P3-3 will prevent accidental regressions.
