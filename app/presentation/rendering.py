from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto, Message
from sqlalchemy import func, select

from app.application.dto import Page
from app.bootstrap import Container
from app.domain.enums import AuctionStatus, PlateStatus, Screen
from app.domain.errors import NotFoundError
from app.infrastructure.db.models import Auction, Banner, BotCard, OwnershipHistory, Sale, User
from app.presentation.keyboards import back_home_rows, keyboard


@dataclass(frozen=True, slots=True)
class ScreenView:
    text: str
    markup: InlineKeyboardMarkup
    image_file_id: str | None = None


class ScreenRenderer:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def _card(self, screen: str, title: str, description: str) -> tuple[str, str, str | None]:
        async with self._container.uow.transaction() as session:
            card = await session.get(BotCard, screen)
            if (
                card is None
                or not card.enabled
                or not card.title.strip()
                or not card.description.strip()
            ):
                return title, description, None
            image = card.image_file_id
            rendered_description = card.description
            if card.banner_id is not None:
                banner = await session.get(Banner, card.banner_id)
                now = datetime.now(UTC)
                if (
                    banner is not None
                    and banner.enabled
                    and (banner.start_at is None or banner.start_at <= now)
                    and (banner.end_at is None or now <= banner.end_at)
                ):
                    rendered_description = (
                        f"{rendered_description}\n\n<b>{banner.title}</b>\n{banner.description}"
                    )
                    image = image or banner.image_file_id
            return card.title, rendered_description, image

    async def basic(
        self, screen: str, title: str, description: str, rows: list[list[tuple[str, str]]]
    ) -> ScreenView:
        title, description, image = await self._card(screen, title, description)
        return ScreenView(f"<b>{title}</b>\n\n{description}", keyboard(rows), image)

    async def home(self, telegram_id: int) -> ScreenView:
        user = await self._container.marketplace.get_user(telegram_id)
        rows = [
            [("🔎 Найти номер", "search:open"), ("🛒 Маркет", "market:open")],
            [("🚘 Мои номера", "plates:mine"), ("🔨 Аукционы", "auction:list")],
            [("⭐ Баланс", "balance:view"), ("👤 Профиль", "profile:view")],
            [("❔ Помощь", "help:view"), ("⚖ Правила", "legal:view")],
        ]
        if user.is_admin:
            rows.append([("🛠 Админ-панель", "admin:home")])
        return await self.basic(
            Screen.HOME.value,
            "CPM2 Plates Market",
            "Маркетплейс <i>цифровых игровых</i> номеров Car Parking Multiplayer 2.\n\nВыберите действие.",
            rows,
        )

    async def search(self) -> ScreenView:
        return await self.basic(
            Screen.SEARCH.value,
            "Поиск номера",
            "Выберите страну и отправьте номер или часть номера.",
            [
                [("🇷🇺 Россия", "search:country:RU"), ("🇰🇿 Казахстан", "search:country:KZ")],
                *back_home_rows(),
            ],
        )

    async def search_results(
        self, country: str, query: str, plate_ids: list[int], offer_state: bool
    ) -> ScreenView:
        rows = [(f"🔖 Открыть номер #{item}", f"plate:view:{item}") for item in plate_ids]
        markup_rows = [[item] for item in rows]
        if offer_state:
            markup_rows.append([("⭐ Купить у Государства", f"state:buy:{country}:{query}")])
        markup_rows.extend(back_home_rows())
        return await self.basic(
            Screen.SEARCH_RESULTS.value,
            "Результаты поиска",
            f"Запрос: <code>{query}</code>\nНайдено предложений: {len(plate_ids)}.",
            markup_rows,
        )

    async def market(self, country: str | None = None, sort: str = "new") -> ScreenView:
        code = country.upper() if country else None
        if code is None:
            return await self.basic(
                Screen.MARKET.value,
                "Маркет",
                "Выберите страну игровых номеров.",
                [
                    [("🇷🇺 Россия", "market:country:RU"), ("🇰🇿 Казахстан", "market:country:KZ")],
                    [("🆕 Новые", "market:new"), ("💎 Редкие", "market:rare")],
                    *back_home_rows(),
                ],
            )
        async with self._container.uow.transaction() as session:
            from app.infrastructure.repositories.marketplace import PlateRepository

            plates = await PlateRepository().market(session, code, sort, 0, 10)
        rows = [[(f"🔖 {plate.plate_number}", f"plate:view:{plate.id}")] for plate in plates]
        rows.extend(back_home_rows())
        return await self.basic(
            Screen.MARKET_RU.value if code == "RU" else Screen.MARKET_KZ.value,
            f"Маркет: {code}",
            "Активные фиксированные продажи и аукционы.",
            rows,
        )

    async def featured_market(self, sort: str) -> ScreenView:
        labels = {
            "new": "Новые предложения",
            "cheap": "Доступные предложения",
            "rare": "Редкие номера",
        }
        async with self._container.uow.transaction() as session:
            from app.infrastructure.repositories.marketplace import PlateRepository

            plates = await PlateRepository().market(session, None, sort, 0, 10)
        rows = [[(f"🔖 {plate.plate_number}", f"plate:view:{plate.id}")] for plate in plates]
        rows.extend(back_home_rows())
        return await self.basic(
            Screen.MARKET.value,
            labels[sort],
            "Активные фиксированные продажи и аукционы.",
            rows,
        )

    async def my_plates(self, telegram_id: int) -> ScreenView:
        plates = await self._container.marketplace.my_plates(telegram_id, Page())
        rows = [[(f"🔖 {plate.plate_number}", f"plate:view:{plate.id}")] for plate in plates]
        rows.extend(back_home_rows())
        return await self.basic(
            Screen.MY_PLATES.value,
            "Мои номера",
            f"В коллекции: {len(plates)}.",
            rows,
        )

    async def plate(self, telegram_id: int, plate_id: int) -> ScreenView:
        plate = await self._container.marketplace.get_plate(plate_id)
        user = await self._container.marketplace.get_user(telegram_id)
        async with self._container.uow.transaction() as session:
            owner = await session.get(User, plate.owner_id) if plate.owner_id else None
            auction = await session.scalar(
                select(Auction).where(
                    Auction.plate_id == plate.id,
                    Auction.status.in_([AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value]),
                )
            )
            sale = await session.scalar(
                select(Sale).where(
                    Sale.plate_id == plate.id, Sale.status.in_(["ACTIVE", "RESERVED"])
                )
            )
            owner_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(OwnershipHistory)
                    .where(OwnershipHistory.plate_id == plate.id)
                )
                or 0
            )
        price = sale.price if sale else (auction.current_price if auction else None)
        details = [
            f"<b>{plate.plate_number}</b>",
            f"Страна: {plate.country_code}",
            f"Статус: <code>{plate.status}</code>",
            f"Владелец: {owner.telegram_name if owner else 'Государство'}",
            f"История владельцев: {owner_count}",
        ]
        if price is not None:
            details.append(f"Цена: ⭐{price}")
        rows: list[list[tuple[str, str]]] = []
        if plate.owner_id == user.id and plate.status == PlateStatus.OWNED.value:
            rows.extend(
                [
                    [
                        ("💸 Продать", f"sale:create:{plate.id}"),
                        ("🔨 На аукцион", f"auction:create:{plate.id}"),
                    ],
                    [("📜 История", f"plate:history:{plate.id}")],
                ]
            )
        elif plate.status == PlateStatus.FIXED_SALE.value:
            rows.append([("⭐ Купить", f"sale:buy:{plate.id}")])
        elif plate.status == PlateStatus.STATE_SALE.value:
            rows.append([("⭐ Купить у Государства", f"state:purchase:{plate.id}")])
        elif plate.status == PlateStatus.AUCTION.value and auction is not None:
            rows.append([("🔨 Открыть аукцион", f"auction:view:{auction.id}")])
        rows.extend(back_home_rows())
        return await self.basic(
            Screen.PLATE_VIEW.value, "Карточка номера", "\n".join(details), rows
        )

    async def auctions(self) -> ScreenView:
        auctions = await self._container.auctions.list_active(Page())
        rows = [
            [
                (
                    f"🔨 Аукцион #{auction.id} · ⭐{auction.current_price}",
                    f"auction:view:{auction.id}",
                )
            ]
            for auction in auctions
        ]
        rows.extend(back_home_rows())
        return await self.basic(
            Screen.AUCTIONS.value, "Аукционы", f"Активно: {len(auctions)}.", rows
        )

    async def auction(self, telegram_id: int, auction_id: int) -> ScreenView:
        auction = await self._container.auctions.get(auction_id)
        plate = await self._container.marketplace.get_plate(auction.plate_id)
        user = await self._container.marketplace.get_user(telegram_id)
        text = (
            f"<b>{plate.plate_number}</b>\n\nСтарт: ⭐{auction.start_price}\nТекущая: ⭐{auction.current_price}\n"
            f"Ставок: {len(await self._container.auctions.bid_history(auction.id, Page(limit=50)))}\n"
            f"До: <code>{auction.ends_at:%Y-%m-%d %H:%M UTC}</code>"
        )
        rows: list[list[tuple[str, str]]] = []
        if user.id == auction.seller_id:
            rows.append([("Отменить", f"auction:cancel:{auction.id}")])
        else:
            rows.append([("⭐ Сделать ставку", f"auction:bid:{auction.id}")])
        rows.append([("📜 История ставок", f"auction:history:{auction.id}")])
        rows.extend(back_home_rows())
        return await self.basic(Screen.AUCTION_VIEW.value, "Карточка аукциона", text, rows)

    async def ownership_history(self, plate_id: int) -> ScreenView:
        history = await self._container.marketplace.ownership_history(plate_id)
        lines = [
            f"• {item.operation_type}: ⭐{item.price if item.price is not None else '—'} · {item.created_at:%Y-%m-%d}"
            for item in history
        ] or ["История пока пуста."]
        return await self.basic(
            Screen.PLATE_VIEW.value, "История владения", "\n".join(lines), back_home_rows()
        )

    async def balance(self, telegram_id: int) -> ScreenView:
        user = await self._container.marketplace.get_user(telegram_id)
        return await self.basic(
            Screen.BALANCE.value,
            "Баланс",
            f"Доступно: ⭐{user.balance_available}\nЗаморожено: ⭐{user.balance_frozen}",
            [
                [
                    ("➕ Пополнить Telegram Stars", "balance:topup"),
                    ("📜 История", "balance:history"),
                ],
                *back_home_rows(),
            ],
        )

    async def profile(self, telegram_id: int) -> ScreenView:
        user = await self._container.marketplace.get_user(telegram_id)
        return await self.basic(
            Screen.PROFILE.value,
            "Профиль",
            f"{user.telegram_name}\nID: <code>{user.telegram_id}</code>\nСтатус: <code>{user.status}</code>",
            back_home_rows(),
        )

    async def admin_home(self, telegram_id: int) -> ScreenView:
        await self._container.admin.statistics(telegram_id)
        return await self.basic(
            Screen.ADMIN_HOME.value,
            "Административная панель",
            "Управление платформой и экономикой.",
            [
                [("📊 Статистика", "admin:stats"), ("👥 Пользователи", "admin:users")],
                [("🔖 Номера", "admin:plates"), ("🔨 Аукционы", "admin:auctions")],
                [("💰 Финансы", "admin:finance"), ("⛔ Чёрные списки", "admin:blacklists")],
                [("🃏 Карточки", "admin:cards"), ("⚙ Настройки", "admin:settings")],
                [("💾 Резервные копии", "admin:backups")],
                *back_home_rows(),
            ],
        )

    async def admin_stats(self, telegram_id: int) -> ScreenView:
        stats = await self._container.admin.statistics(telegram_id)
        return await self.basic(
            Screen.ADMIN_STATS.value,
            "Статистика",
            f"Пользователи: {stats.users}\nНомера: {stats.plates}\nПродажи: {stats.active_sales}\n"
            f"Аукционы: {stats.active_auctions}\nДоход государства: ⭐{stats.state_revenue}",
            back_home_rows(),
        )

    async def help(self) -> ScreenView:
        return await self.basic(
            Screen.HELP.value,
            "Помощь",
            "Найдите номер, пополните баланс Stars и участвуйте в аукционах.\n\nВсе активы — только игровые цифровые номера CPM2.",
            back_home_rows(),
        )

    async def legal(self) -> ScreenView:
        return await self.basic(
            Screen.LEGAL.value,
            "Правила",
            "Номера существуют только внутри Car Parking Multiplayer 2 и не являются реальными регистрационными знаками. Платёжные операции окончательны после успешной оплаты Telegram Stars.",
            back_home_rows(),
        )

    async def update_existing(
        self,
        bot: Bot,
        telegram_id: int,
        view: ScreenView,
        message: Message | None = None,
    ) -> None:
        """Edit the one app message, preserving its current image when a card has no replacement."""
        try:
            user = await self._container.marketplace.get_user(telegram_id)
            if message is not None:
                image = view.image_file_id
                if image is None and message.photo:
                    image = message.photo[-1].file_id
                image = image or user.app_image_file_id
                if image is None:
                    await message.edit_text(view.text, reply_markup=view.markup)
                else:
                    await message.edit_media(
                        InputMediaPhoto(media=image, caption=view.text, parse_mode=ParseMode.HTML),
                        reply_markup=view.markup,
                    )
                await self._container.marketplace.set_main_message(
                    telegram_id,
                    message.chat.id,
                    message.message_id,
                    image,
                )
                return
            if user.app_chat_id is None or user.app_message_id is None:
                raise NotFoundError("Основное сообщение ещё не создано.")
            image = view.image_file_id or user.app_image_file_id
            if image is None:
                await bot.edit_message_text(
                    chat_id=user.app_chat_id,
                    message_id=user.app_message_id,
                    text=view.text,
                    reply_markup=view.markup,
                )
            else:
                await bot.edit_message_media(
                    chat_id=user.app_chat_id,
                    message_id=user.app_message_id,
                    media=InputMediaPhoto(
                        media=image, caption=view.text, parse_mode=ParseMode.HTML
                    ),
                    reply_markup=view.markup,
                )
            await self._container.marketplace.set_main_message(
                telegram_id,
                user.app_chat_id,
                user.app_message_id,
                image,
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
