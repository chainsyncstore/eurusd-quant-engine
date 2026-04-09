# Hypothesis Research Engine (Current Runtime)

This repository now runs a **multi-user Telegram trading platform** with modern v2 execution controls, safety checks, and deployment-ready operations.

The project still includes research/evaluation tooling, but the active production workflow is centered on the Telegram bot + v2 execution stack.

## What the system does today

- Supports **paper** and **live** sessions per user.
- **Machine Learning Ensemble:** Blends classical structural features (LightGBM) with purely temporal analysis (Amazon Chronos) into a `FullEnsemble` consensus. 
- **The Event Gate:** Semantic news pipeline (CryptoCompare + Fear/Greed) acts as a dynamic veto against ML predictions during extreme global shocks.
- **Resilient Redis Architecture:** Uses an asynchronous Zero-Trust message bus (Redis Streams) and a robust Write-Ahead Log (WAL) to survive and seamlessly recover from aggressive EC2 out-of-memory container crashes.
- **Advanced Execution Logistics:** Includes background ledger reconciliation to catch ghost/phantom positions against the live exchange, alongside a 120s "Tick Starvation" dead-man's switch to flatten positions during market data blackouts.
- **Dynamic Risk Filters:** Automatic Minimum Notional allocation limits based on real-time equity ratios mapped heavily against inverse-volatility parity weighting.

## Non-technical lifecycle controls (for end users)

Users can configure auto-close safety rules without trading jargon:

- **Time limit**: close open trades automatically after N hours.
  - Example: `/set_horizon 4`
- **Loss limit**: close a trade when loss reaches your chosen percentage.
  - Example: `/set_stoploss 2` (means 2%)
- **View current settings**:
  - `/lifecycle`

## Core Telegram commands

- `/start` - account check
- `/start_demo` - start paper trading
- `/start_live` - start live trading
- `/stop` - stop trading
- `/status` - current engine state
- `/stats` - portfolio and risk stats
- `/reset_demo` - reset paper session
- `/prepare_update` - admin pre-deploy snapshot + safe stop
- `/continue_demo` / `/continue_live` - restore after maintenance

## Local development

### Install

```bash
pip install -e .
pip install -e ".[dev]"
```

### Run tests

```bash
pytest tests/ -v
```

### Run bot locally (env required)

Set these environment variables:

- `TELEGRAM_TOKEN`
- `ADMIN_ID`
- `BOT_MASTER_KEY`

Then start:

```bash
python -m quant.telebot.main
```

## Docker run

```bash
docker-compose up -d --build
docker-compose logs -f
```

## AWS deployment (EC2)

Use `AWS_DEPLOY.md` for full setup details. Typical restart/update flow on server:

```bash
git pull
docker-compose down
docker-compose up -d --build
docker-compose logs -f --tail=200
```

## Important docs

- `AWS_DEPLOY.md` - EC2 setup + deployment
- `DEPLOY.md` - generic VPS deployment
- `quant/telebot/main.py` - bot command handlers
- `quant_v2/execution/service.py` - v2 execution + safety core

## License

Research and internal operations use.
