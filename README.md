# CPM2 Plates Market

Production-oriented Telegram marketplace for **digital in-game Car Parking Multiplayer 2 plate assets**. It does not issue, sell, or represent real-world registration signs.

## Run locally

1. Copy `.env.example` to `.env` and set `BOT_TOKEN`, `ADMIN_TELEGRAM_IDS`, and `OWNER_TELEGRAM_ID`.
2. Start the stack: `docker compose up --build`.
3. The bot applies Alembic migrations before polling.

Set `WEBHOOK_URL` to a public HTTPS origin to use webhook delivery instead of polling. The bot
then listens on `WEBHOOK_HOST:WEBHOOK_PORT`, serves Telegram on `WEBHOOK_PATH`, validates
`WEBHOOK_SECRET`, and exposes `/healthz` for an orchestrator. Leave `WEBHOOK_URL` empty for
local polling.

For a local Python environment, install `pip install -e '.[dev]'`, start PostgreSQL and Redis, set the same variables, then run `alembic upgrade head` and `python -m app.main`.

## Production notes

- Run exactly one scheduler instance (`SCHEDULER_ENABLED=true`, the default) when horizontally scaling polling workers.
- Use Telegram webhook mode behind HTTPS for Railway or other multi-instance deployments.
- PostgreSQL is the source of truth. Redis is used only for FSM and UI cache, so losing Redis does not alter balances or asset ownership.
- Backups use `pg_dump`; the process image includes the PostgreSQL client.

## Architecture

`app/domain` holds invariants and value objects; `app/application` holds use cases; `app/infrastructure` implements persistence and external integrations; `app/presentation` contains aiogram handlers and rendering only.

The first migration creates the complete schema, including payment intents needed for idempotent Telegram Stars handling and a platform ledger for State revenue.

The data model and layer boundaries are documented in [ER.md](docs/ER.md) and [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

Run the unit checks with `python -m pytest -q`. Repository and service integration checks run
against PostgreSQL because the production schema uses PostgreSQL-only types and indexes:

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://plates:plates@localhost:5432/plates_test"
python -m pytest -q -m integration
```

The integration fixture recreates all tables in the database specified by
`TEST_DATABASE_URL`; use a disposable database only.
