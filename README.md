# CPM2 Plates Market

Production-oriented Telegram marketplace for **digital in-game Car Parking Multiplayer 2 plate assets**. It does not issue, sell, or represent real-world registration signs.

## Run locally

1. Copy `.env.example` to `.env` and set `BOT_TOKEN` and `ADMIN_TELEGRAM_IDS`.
2. Start the stack: `docker compose up --build`.
3. The bot applies Alembic migrations before polling.

Set `WEBHOOK_URL` to a public HTTPS origin to use webhook delivery instead of polling. The bot
then listens on `WEBHOOK_HOST:WEBHOOK_PORT`, serves Telegram on `WEBHOOK_PATH`, validates
`WEBHOOK_SECRET`, and exposes `/healthz` for an orchestrator. Leave `WEBHOOK_URL` empty for
local polling.

For a local Python environment, install `pip install -e '.[dev]'`, start PostgreSQL and Redis, set the same variables, then run `alembic upgrade head` and `python -m app.main`.

## Production notes

- The primary-number bot needs no scheduler. Set `SCHEDULER_ENABLED=true` only if you keep using
  the legacy marketplace auctions and payment-expiry jobs; run exactly one such instance.
- Use Telegram webhook mode behind HTTPS for Railway or other multi-instance deployments.
- PostgreSQL is the source of truth. Redis is used only for FSM and UI cache, so losing Redis does not alter balances or asset ownership.
- All CPM2 tables live in the `cpm2` PostgreSQL schema by default. This prevents collisions with
  generic tables such as `users` in a shared database; override it only through `DATABASE_SCHEMA`.
- Backups use `pg_dump`; the process image includes the PostgreSQL client.

## Railway

Deploy one service from this repository and attach Railway PostgreSQL and Redis. Set
`BOT_TOKEN`, `DATABASE_URL`, `REDIS_URL`, and `ADMIN_TELEGRAM_IDS`. The simplest Railway
configuration is polling: leave `WEBHOOK_URL` and `WEBHOOK_SECRET` empty. If using a webhook,
set `WEBHOOK_URL` to the public Railway domain; both `https://my-bot.up.railway.app` and the
bare `my-bot.up.railway.app` are accepted. Do not use Railway's private/internal `http://`
address — Telegram cannot call it. Railway provides `PORT` automatically; it is used when
`WEBHOOK_PORT` is not set. The container applies migrations before starting the bot.

Set `REDIS_URL` from the Redis service's generated connection variable (for example, reference
its `REDIS_URL` in Railway). Do not enter `redis://redis:6379/0` manually: Railway Redis uses a
password, so the URL must contain credentials such as `redis://default:<password>@host:port/0`.
If Redis is temporarily unavailable, the bot now uses in-memory FSM storage and continues to
answer `/start`; Redis-backed state will resume after its connection variable is corrected.

For the group phrase **«мой номер»** to reach the bot, disable group privacy in BotFather:
`/setprivacy` → select the bot → `Disable`. Telegram otherwise delivers only commands to bots
in groups. A user opens the bot privately, selects one owned, non-listed number as primary, and
may send a photo there. PostgreSQL stores the selected plate and Telegram's `file_id`; the photo
is re-sent by Telegram, so no S3 or other media storage is used.

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
