import os

import app.infrastructure.db.models  # noqa: F401 - register all SQLAlchemy tables
import pytest
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import Database, UnitOfWork


def _async_url(url: str) -> str:
    return url.replace("postgres://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture
async def postgres_uow() -> UnitOfWork:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database = Database(_async_url(url))
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield UnitOfWork(database.session_factory)
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.close()
