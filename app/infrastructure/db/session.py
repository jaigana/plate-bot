from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class Database:
    def __init__(self, url: str, *, schema: str = "cpm2") -> None:
        self.engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=20,
            max_overflow=40,
            connect_args={"server_settings": {"search_path": schema}},
        )
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False
        )

    async def close(self) -> None:
        await self.engine.dispose()


class UnitOfWork:
    """One database transaction per application command."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            async with session.begin():
                yield session
