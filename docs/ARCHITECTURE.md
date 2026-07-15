# Runtime and folder structure

```text
app/
├── domain/          invariants, policies, country definitions and validators
├── application/     DTOs and transactional use-case services
├── infrastructure/  SQLAlchemy models, repositories, storage and Unit of Work
├── presentation/    aiogram handlers, FSM, keyboards and single-message rendering
├── tasks/           APScheduler jobs
├── config/          typed environment configuration
└── utils/           structured logging
migrations/          Alembic revisions
tests/               unit, repository and PostgreSQL integration tests
docs/                data model and architecture documentation
```

The presentation layer does not change marketplace state directly. It forwards a validated
Telegram update to an application service and renders the returned state into the user’s one
main chat message. Service messages, invoices, and notifications are the only permitted new
messages.

Each marketplace command uses `UnitOfWork.transaction()`. Repositories lock mutable rows with
`SELECT FOR UPDATE`; database uniqueness constraints provide a second line of defense for plate
issuance, active listings, payment payloads, and State emission reservations.
