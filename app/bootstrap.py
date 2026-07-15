from dataclasses import dataclass
from pathlib import Path

from app.application.services.admin import AdminService
from app.application.services.auction import AuctionService
from app.application.services.maintenance import BackupService
from app.application.services.marketplace import MarketplaceService
from app.application.services.navigation import NavigationService
from app.config.settings import Settings
from app.infrastructure.db.session import Database, UnitOfWork
from app.infrastructure.storage import ImageStorage


@dataclass(slots=True)
class Container:
    settings: Settings
    database: Database
    uow: UnitOfWork
    marketplace: MarketplaceService
    auctions: AuctionService
    admin: AdminService
    navigation: NavigationService
    backups: BackupService
    images: ImageStorage

    async def close(self) -> None:
        await self.database.close()


def build_container(settings: Settings) -> Container:
    database = Database(settings.async_database_url, schema=settings.database_schema)
    uow = UnitOfWork(database.session_factory)
    auctions = AuctionService(uow)
    return Container(
        settings=settings,
        database=database,
        uow=uow,
        marketplace=MarketplaceService(uow),
        auctions=auctions,
        admin=AdminService(uow, auctions),
        navigation=NavigationService(uow),
        backups=BackupService(uow, settings.async_database_url, Path("backups")),
        images=ImageStorage(settings),
    )
