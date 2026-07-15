from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType, ContentType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from app.application.dto import Page
from app.bootstrap import Container
from app.domain.enums import Screen
from app.domain.errors import DomainError, ValidationError
from app.presentation.keyboards import keyboard
from app.presentation.rendering import ScreenRenderer, ScreenView
from app.presentation.states import InputState

router = Router(name="marketplace")


def _renderer(container: Container) -> ScreenRenderer:
    return ScreenRenderer(container)


async def _delete(message: Message, message_id: int) -> None:
    with suppress(Exception):
        await message.bot.delete_message(message.chat.id, message_id)


async def _cleanup_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_id = data.get("prompt_id")
    if prompt_id:
        await _delete(message, int(prompt_id))
    await _delete(message, message.message_id)
    await state.clear()


async def _input_error(message: Message, state: FSMContext, error: Exception) -> None:
    await _delete(message, message.message_id)
    data = await state.get_data()
    prompt_id = data.get("prompt_id")
    if prompt_id:
        with suppress(Exception):
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=int(prompt_id),
                text=f"⚠ {error}\nПопробуйте ещё раз.",
            )


async def _prompt(
    callback: CallbackQuery,
    state: FSMContext,
    target: InputState,
    text: str,
    **data: object,
) -> None:
    if callback.message is None:
        return
    prompt = await callback.message.bot.send_message(callback.from_user.id, text)
    await state.set_state(target)
    await state.set_data({"prompt_id": prompt.message_id, **data})
    await callback.answer()


async def _show(callback: CallbackQuery, view: ScreenView, renderer: ScreenRenderer) -> None:
    if callback.message is not None:
        await renderer.update_existing(
            callback.message.bot, callback.from_user.id, view, callback.message
        )
    await callback.answer()


async def _show_after_input(message: Message, view: ScreenView, renderer: ScreenRenderer) -> None:
    await renderer.update_existing(message.bot, message.from_user.id, view)


async def _view_for_screen(container: Container, telegram_id: int) -> ScreenView:
    renderer = _renderer(container)
    screen = await container.navigation.current(telegram_id)
    if screen == Screen.HOME.value:
        return await renderer.home(telegram_id)
    if screen == Screen.SEARCH.value:
        return await renderer.search()
    if screen == Screen.MARKET.value:
        return await renderer.market()
    if screen == Screen.MARKET_RU.value:
        return await renderer.market("RU")
    if screen == Screen.MARKET_KZ.value:
        return await renderer.market("KZ")
    if screen.startswith("MARKET_FEATURED:"):
        return await renderer.featured_market(screen.split(":", 1)[1])
    if screen == Screen.MY_PLATES.value:
        return await renderer.my_plates(telegram_id)
    if screen == Screen.AUCTIONS.value:
        return await renderer.auctions()
    if screen == Screen.BALANCE.value:
        return await renderer.balance(telegram_id)
    if screen == Screen.PROFILE.value:
        return await renderer.profile(telegram_id)
    if screen == Screen.HELP.value:
        return await renderer.help()
    if screen == Screen.LEGAL.value:
        return await renderer.legal()
    if screen == Screen.ADMIN_HOME.value:
        return await renderer.admin_home(telegram_id)
    if screen == Screen.ADMIN_STATS.value:
        return await renderer.admin_stats(telegram_id)
    if screen.startswith("SEARCH_RESULTS:"):
        _, country, query = screen.split(":", 2)
        _, plates = await container.marketplace.search(country, query, Page())
        try:
            offer_state = (
                await container.marketplace.find_exact_or_offer_state(country, query)
            ).can_buy_from_state
        except ValidationError:
            offer_state = False
        return await renderer.search_results(
            country, query, [plate.id for plate in plates], offer_state
        )
    if screen.startswith("PLATE_VIEW:"):
        return await renderer.plate(telegram_id, int(screen.split(":", 1)[1]))
    if screen.startswith("PLATE_HISTORY:"):
        return await renderer.ownership_history(int(screen.split(":", 1)[1]))
    if screen.startswith("AUCTION_VIEW:"):
        return await renderer.auction(telegram_id, int(screen.split(":", 1)[1]))
    if screen.startswith("AUCTION_HISTORY:"):
        auction_id = int(screen.split(":", 1)[1])
        bids = await container.auctions.bid_history(auction_id, Page(limit=50))
        text = "\n".join(f"• ⭐{bid.amount} · пользователь #{bid.bidder_id}" for bid in bids)
        return await renderer.basic(
            Screen.AUCTION_HISTORY.value,
            "История ставок",
            text or "Ставок ещё нет.",
            [[("⬅ К аукциону", f"auction:view:{auction_id}")]],
        )
    return await renderer.home(telegram_id)


async def _send_invoice(message: Message, invoice: object) -> None:
    from app.application.dto import InvoiceDTO

    assert isinstance(invoice, InvoiceDTO)
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title=invoice.title,
        description=invoice.description,
        payload=invoice.payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=invoice.title, amount=invoice.amount)],
    )


def _profile_keyboard(has_primary: bool) -> InlineKeyboardMarkup:
    action = "Сменить основной номер" if has_primary else "Выбрать основной номер"
    return keyboard([[(action, "profile:choose")]])


async def _profile_text(container: Container, telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user, plate = await container.marketplace.profile(telegram_id)
    if plate is None:
        number = "не выбран"
    else:
        number = f"<code>{escape(plate.plate_number)}</code> · {plate.country_code}"
    cover = "установлена" if user.cover_photo_file_id else "не установлена"
    return (
        "<b>Ваш номер</b>\n\n"
        f"Основной номер: {number}\n"
        f"Обложка: {cover}\n\n"
        "Выберите один из доступных номеров. Чтобы сменить обложку, просто отправьте фото сюда.",
        _profile_keyboard(plate is not None),
    )


async def _send_profile(message: Message, container: Container) -> None:
    text, markup = await _profile_text(container, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.message(CommandStart())
@router.message(Command("profile"))
async def start(message: Message, container: Container) -> None:
    if message.from_user is None:
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("Напишите «мой номер», и я покажу ваш основной игровой номер.")
        return
    await _send_profile(message, container)


@router.callback_query(F.data == "profile:show")
async def profile_show(callback: CallbackQuery, container: Container) -> None:
    if callback.message is None:
        return
    text, markup = await _profile_text(container, callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "profile:choose")
async def profile_choose(callback: CallbackQuery, container: Container) -> None:
    if callback.message is None:
        return
    plates = await container.marketplace.available_primary_plates(callback.from_user.id)
    if not plates:
        await callback.message.edit_text(
            "<b>Нет доступного номера</b>\n\n"
            "Основным можно назначить только номер, который принадлежит вам и не выставлен на продажу.",
            reply_markup=keyboard([[("Назад", "profile:show")]]),
        )
    else:
        rows = [[(f"🚘 {plate.plate_number} · {plate.country_code}", f"profile:primary:{plate.id}")]
                for plate in plates]
        rows.append([("Назад", "profile:show")])
        await callback.message.edit_text(
            "<b>Выберите основной номер</b>\n\nВ групповом чате будет показан только он.",
            reply_markup=keyboard(rows),
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^profile:primary:\d+$"))
async def profile_set_primary(callback: CallbackQuery, container: Container) -> None:
    if callback.message is None:
        return
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.marketplace.set_primary_plate(callback.from_user.id, plate_id)
        text, markup = await _profile_text(container, callback.from_user.id)
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer("Основной номер сохранён.")
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


def _owner_plate_text(telegram_id: int, name: str, plate_number: str, country_code: str) -> str:
    return (
        "🚘 <b>Номер владельца</b>\n\n"
        f"<code>{escape(plate_number)}</code>\n"
        f"🌍 {escape(country_code)}\n"
        f"👤 <a href=\"tg://user?id={telegram_id}\">{escape(name)}</a>"
    )


@router.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    F.text.func(lambda text: isinstance(text, str) and text.strip().casefold() == "мой номер"),
)
async def show_primary_number_in_group(message: Message, container: Container) -> None:
    if message.from_user is None:
        return
    user, plate = await container.marketplace.profile(message.from_user.id)
    if plate is None:
        await message.reply(
            "Основной номер ещё не выбран. Откройте бота в личном чате и нажмите «Выбрать основной номер»."
        )
        return
    text = _owner_plate_text(user.telegram_id, user.telegram_name, plate.plate_number, plate.country_code)
    if user.cover_photo_file_id:
        await message.reply_photo(user.cover_photo_file_id, caption=text, parse_mode=ParseMode.HTML)
    else:
        await message.reply(text)


@router.callback_query(F.data == "nav:home")
async def navigation_home(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.home(callback.from_user.id)
    await _show(
        callback, await _renderer(container).home(callback.from_user.id), _renderer(container)
    )


@router.callback_query(F.data == "nav:back")
async def navigation_back(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.back(callback.from_user.id)
    renderer = _renderer(container)
    await _show(callback, await _view_for_screen(container, callback.from_user.id), renderer)


@router.callback_query(F.data == "search:open")
async def open_search(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.SEARCH.value)
    renderer = _renderer(container)
    await _show(callback, await renderer.search(), renderer)


@router.callback_query(F.data.startswith("search:country:"))
async def choose_search_country(callback: CallbackQuery, state: FSMContext) -> None:
    country = (callback.data or "").rsplit(":", 1)[-1]
    await _prompt(
        callback,
        state,
        InputState.WAIT_SEARCH_QUERY,
        "Введите номер или часть номера (до 15 символов).",
        country=country,
    )


@router.message(InputState.WAIT_SEARCH_QUERY, F.text)
async def search_input(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    country, raw = str(data["country"]), message.text or ""
    try:
        query, plates = await container.marketplace.search(country, raw, Page())
        offer_state = False
        try:
            exact = await container.marketplace.find_exact_or_offer_state(country, raw)
            offer_state = exact.can_buy_from_state
        except ValidationError:
            pass
        await container.navigation.push(message.from_user.id, f"SEARCH_RESULTS:{country}:{query}")
        view = await _renderer(container).search_results(
            country, query, [plate.id for plate in plates], offer_state
        )
        await _cleanup_input(message, state)
        await _show_after_input(message, view, _renderer(container))
    except DomainError as error:
        await _input_error(message, state, error)


@router.callback_query(F.data.startswith("state:buy:"))
async def buy_from_state(callback: CallbackQuery, container: Container) -> None:
    _, _, country, number = (callback.data or "").split(":", 3)
    try:
        invoice = await container.marketplace.start_state_emission(
            callback.from_user.id, country, number
        )
        if callback.message is not None:
            await _send_invoice(callback.message, invoice)
        await callback.answer("Счёт Telegram Stars создан.")
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("state:purchase:"))
async def purchase_state_inventory(callback: CallbackQuery, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        invoice = await container.marketplace.start_state_purchase(callback.from_user.id, plate_id)
        if callback.message is not None:
            await _send_invoice(callback.message, invoice)
        await callback.answer("Счёт Telegram Stars создан.")
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "market:open")
async def market_open(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.MARKET.value)
    renderer = _renderer(container)
    await _show(callback, await renderer.market(), renderer)


@router.callback_query(F.data.startswith("market:country:"))
async def market_country(callback: CallbackQuery, container: Container) -> None:
    country = (callback.data or "").rsplit(":", 1)[-1]
    screen = Screen.MARKET_RU.value if country == "RU" else Screen.MARKET_KZ.value
    await container.navigation.push(callback.from_user.id, screen)
    renderer = _renderer(container)
    await _show(callback, await renderer.market(country), renderer)


@router.callback_query(F.data.in_({"market:new", "market:rare", "market:cheap"}))
async def market_featured(callback: CallbackQuery, container: Container) -> None:
    sort = (callback.data or "market:new").split(":", 1)[1]
    await container.navigation.push(callback.from_user.id, f"MARKET_FEATURED:{sort}")
    renderer = _renderer(container)
    await _show(callback, await renderer.featured_market(sort), renderer)


@router.callback_query(F.data == "plates:mine")
async def my_plates(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.MY_PLATES.value)
    renderer = _renderer(container)
    await _show(callback, await renderer.my_plates(callback.from_user.id), renderer)


@router.callback_query(F.data.startswith("plate:view:"))
async def plate_view(callback: CallbackQuery, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    await container.navigation.push(callback.from_user.id, f"PLATE_VIEW:{plate_id}")
    renderer = _renderer(container)
    await _show(callback, await renderer.plate(callback.from_user.id, plate_id), renderer)


@router.callback_query(F.data.startswith("plate:history:"))
async def plate_history(callback: CallbackQuery, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    await container.navigation.push(callback.from_user.id, f"PLATE_HISTORY:{plate_id}")
    renderer = _renderer(container)
    await _show(callback, await renderer.ownership_history(plate_id), renderer)


@router.callback_query(F.data.startswith("sale:create:"))
async def sale_create_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _prompt(
        callback,
        state,
        InputState.WAIT_SALE_PRICE,
        "Введите цену продажи: целое число от ⭐1 до ⭐99999.",
        plate_id=plate_id,
    )


@router.message(InputState.WAIT_SALE_PRICE, F.text)
async def sale_price_input(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        price = int((message.text or "").strip())
        plate_id = int(data["plate_id"])
        user = await container.marketplace.get_user(message.from_user.id)
        policy = None
        del user, policy
        await state.update_data(price=price)
        await _delete(message, message.message_id)
        if data.get("prompt_id"):
            await _delete(message, int(data["prompt_id"]))
        view = await _renderer(container).basic(
            Screen.CREATE_SALE.value,
            "Подтверждение продажи",
            f"Выставить номер на продажу за ⭐{price}?",
            [
                [
                    ("✅ Подтвердить", f"sale:confirm:{plate_id}"),
                    ("✖ Отмена", f"plate:view:{plate_id}"),
                ]
            ],
        )
        await _show_after_input(message, view, _renderer(container))
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.callback_query(F.data.startswith("sale:confirm:"))
async def sale_confirm(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    data = await state.get_data()
    try:
        await container.marketplace.create_sale(callback.from_user.id, plate_id, int(data["price"]))
        await state.clear()
        await container.navigation.replace(callback.from_user.id, f"PLATE_VIEW:{plate_id}")
        renderer = _renderer(container)
        await _show(callback, await renderer.plate(callback.from_user.id, plate_id), renderer)
    except (KeyError, ValueError, DomainError) as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("sale:buy:"))
async def sale_buy(callback: CallbackQuery, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        invoice = await container.marketplace.start_sale_purchase(callback.from_user.id, plate_id)
        if callback.message is not None:
            await _send_invoice(callback.message, invoice)
        await callback.answer("Счёт Telegram Stars создан.")
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "auction:list")
async def auction_list(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.AUCTIONS.value)
    renderer = _renderer(container)
    await _show(callback, await renderer.auctions(), renderer)


@router.callback_query(F.data.startswith("auction:view:"))
async def auction_view(callback: CallbackQuery, container: Container) -> None:
    auction_id = int((callback.data or "").rsplit(":", 1)[-1])
    await container.navigation.push(callback.from_user.id, f"AUCTION_VIEW:{auction_id}")
    renderer = _renderer(container)
    await _show(callback, await renderer.auction(callback.from_user.id, auction_id), renderer)


@router.callback_query(F.data.startswith("auction:create:"))
async def auction_create_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _prompt(
        callback,
        state,
        InputState.WAIT_AUCTION_START_PRICE,
        "Введите стартовую цену аукциона.",
        plate_id=plate_id,
    )


@router.message(InputState.WAIT_AUCTION_START_PRICE, F.text)
async def auction_price_input(message: Message, state: FSMContext) -> None:
    try:
        amount = int((message.text or "").strip())
        if amount < 1:
            raise ValidationError("Цена должна быть не меньше ⭐1.")
        data = await state.get_data()
        await _delete(message, message.message_id)
        if data.get("prompt_id"):
            await _delete(message, int(data["prompt_id"]))
        prompt = await message.bot.send_message(
            message.chat.id, "Введите длительность аукциона в часах (1–24)."
        )
        await state.set_state(InputState.WAIT_AUCTION_DURATION)
        await state.update_data(start_price=amount, prompt_id=prompt.message_id)
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.message(InputState.WAIT_AUCTION_DURATION, F.text)
async def auction_duration_input(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        duration = int((message.text or "").strip())
        if not 1 <= duration <= 24:
            raise ValidationError("Длительность должна быть от 1 до 24 часов.")
        plate_id, price = int(data["plate_id"]), int(data["start_price"])
        await state.update_data(duration=duration)
        await _delete(message, message.message_id)
        if data.get("prompt_id"):
            await _delete(message, int(data["prompt_id"]))
        view = await _renderer(container).basic(
            Screen.CREATE_AUCTION.value,
            "Подтверждение аукциона",
            f"Старт: ⭐{price}\nДлительность: {duration} ч.",
            [
                [
                    ("✅ Создать", f"auction:confirm:{plate_id}"),
                    ("✖ Отмена", f"plate:view:{plate_id}"),
                ]
            ],
        )
        await _show_after_input(message, view, _renderer(container))
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.callback_query(F.data.startswith("auction:confirm:"))
async def auction_confirm(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        data = await state.get_data()
        auction = await container.auctions.create(
            callback.from_user.id, plate_id, int(data["start_price"]), int(data["duration"])
        )
        await state.clear()
        await container.navigation.replace(callback.from_user.id, f"AUCTION_VIEW:{auction.id}")
        renderer = _renderer(container)
        await _show(callback, await renderer.auction(callback.from_user.id, auction.id), renderer)
    except (KeyError, ValueError, DomainError) as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("auction:bid:"))
async def bid_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    auction_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _prompt(
        callback,
        state,
        InputState.WAIT_BID_AMOUNT,
        "Введите сумму ставки в ⭐.",
        auction_id=auction_id,
    )


@router.message(InputState.WAIT_BID_AMOUNT, F.text)
async def bid_input(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        auction_id, amount = int(data["auction_id"]), int((message.text or "").strip())
        await container.auctions.place_bid(message.from_user.id, auction_id, amount)
        await _cleanup_input(message, state)
        await container.navigation.replace(message.from_user.id, f"AUCTION_VIEW:{auction_id}")
        await _show_after_input(
            message,
            await _renderer(container).auction(message.from_user.id, auction_id),
            _renderer(container),
        )
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.callback_query(F.data.startswith("auction:cancel:"))
async def auction_cancel(callback: CallbackQuery, container: Container) -> None:
    auction_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.auctions.cancel(callback.from_user.id, auction_id)
        await container.navigation.back(callback.from_user.id)
        renderer = _renderer(container)
        await _show(callback, await _view_for_screen(container, callback.from_user.id), renderer)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("auction:history:"))
async def auction_history(callback: CallbackQuery, container: Container) -> None:
    auction_id = int((callback.data or "").rsplit(":", 1)[-1])
    await container.navigation.push(callback.from_user.id, f"AUCTION_HISTORY:{auction_id}")
    bids = await container.auctions.bid_history(auction_id, Page(limit=50))
    text = (
        "\n".join(f"• ⭐{bid.amount} · пользователь #{bid.bidder_id}" for bid in bids)
        or "Ставок ещё нет."
    )
    renderer = _renderer(container)
    view = await renderer.basic(
        Screen.AUCTION_HISTORY.value,
        "История ставок",
        text,
        [[("⬅ К аукциону", f"auction:view:{auction_id}")]],
    )
    await _show(callback, view, renderer)


@router.callback_query(F.data == "balance:view")
async def balance_view(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.BALANCE.value)
    renderer = _renderer(container)
    await _show(callback, await renderer.balance(callback.from_user.id), renderer)


@router.callback_query(F.data == "balance:topup")
async def topup_prompt(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.BALANCE_TOPUP.value)
    await _prompt(
        callback, state, InputState.WAIT_TOPUP_AMOUNT, "Введите сумму пополнения от ⭐1 до ⭐99999."
    )


@router.message(InputState.WAIT_TOPUP_AMOUNT, F.text)
async def topup_input(message: Message, state: FSMContext, container: Container) -> None:
    try:
        invoice = await container.marketplace.start_top_up(
            message.from_user.id, int((message.text or "").strip())
        )
        await _cleanup_input(message, state)
        await _send_invoice(message, invoice)
        await container.navigation.replace(message.from_user.id, Screen.BALANCE.value)
        await _show_after_input(
            message, await _renderer(container).balance(message.from_user.id), _renderer(container)
        )
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.callback_query(F.data == "balance:history")
async def balance_history(callback: CallbackQuery, container: Container) -> None:
    user = await container.marketplace.get_user(callback.from_user.id)
    async with container.uow.transaction() as session:
        from sqlalchemy import select

        from app.infrastructure.db.models import Transaction

        rows = list(
            await session.scalars(
                select(Transaction)
                .where(Transaction.user_id == user.id)
                .order_by(Transaction.id.desc())
                .limit(20)
            )
        )
    text = "\n".join(f"• {item.type}: {item.amount:+d} ⭐" for item in rows) or "Операций пока нет."
    renderer = _renderer(container)
    await _show(
        callback,
        await renderer.basic(
            Screen.BALANCE.value, "История операций", text, [[("⬅ Баланс", "balance:view")]]
        ),
        renderer,
    )


@router.callback_query(F.data == "profile:view")
async def profile_view(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.PROFILE.value)
    renderer = _renderer(container)
    await _show(callback, await renderer.profile(callback.from_user.id), renderer)


@router.callback_query(F.data == "help:view")
async def help_view(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.HELP.value)
    renderer = _renderer(container)
    await _show(callback, await renderer.help(), renderer)


@router.callback_query(F.data == "legal:view")
async def legal_view(callback: CallbackQuery, container: Container) -> None:
    await container.navigation.push(callback.from_user.id, Screen.LEGAL.value)
    renderer = _renderer(container)
    await _show(callback, await renderer.legal(), renderer)


@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_HOME.value)
        renderer = _renderer(container)
        await _show(callback, await renderer.admin_home(callback.from_user.id), renderer)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_STATS.value)
        renderer = _renderer(container)
        await _show(callback, await renderer.admin_stats(callback.from_user.id), renderer)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_USERS.value)
        users = await container.admin.users(callback.from_user.id, Page())
        rows = [
            [(f"👤 {user.telegram_name} · #{user.id}", f"admin:user:{user.id}")] for user in users
        ]
        rows.append([("⬅ Админ-панель", "admin:home")])
        renderer = _renderer(container)
        await _show(
            callback,
            await renderer.basic(
                Screen.ADMIN_USERS.value, "Пользователи", f"Показано: {len(users)}.", rows
            ),
            renderer,
        )
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("admin:user:"))
async def admin_user(callback: CallbackQuery, container: Container) -> None:
    user_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.navigation.push(callback.from_user.id, f"ADMIN_USER_VIEW:{user_id}")
        user = await container.admin.user(callback.from_user.id, user_id)
        block_button = "✅ Разблокировать" if user.is_blocked else "⛔ Блокировать"
        block_callback = f"admin:unblock:{user.id}" if user.is_blocked else f"admin:block:{user.id}"
        rows = [
            [("💰 Изменить баланс", f"admin:balance:{user.id}"), (block_button, block_callback)],
            [("⬅ Пользователи", "admin:users")],
        ]
        renderer = _renderer(container)
        view = await renderer.basic(
            Screen.ADMIN_USER_VIEW.value,
            "Пользователь",
            f"{user.telegram_name}\nID: {user.id}\nБаланс: ⭐{user.balance_available}\nСтатус: {user.status}",
            rows,
        )
        await _show(callback, view, renderer)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("admin:balance:"))
async def admin_balance_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    target_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _prompt(
        callback,
        state,
        InputState.WAIT_ADMIN_BALANCE,
        "Введите изменение и причину: <code>+100 | бонус</code>.",
        target_id=target_id,
    )


@router.message(InputState.WAIT_ADMIN_BALANCE, F.text)
async def admin_balance_input(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        value, reason = (message.text or "").split("|", 1)
        await container.admin.adjust_balance(
            message.from_user.id, int(data["target_id"]), int(value.strip()), reason.strip()
        )
        await _cleanup_input(message, state)
        await _show_after_input(
            message,
            await _renderer(container).admin_home(message.from_user.id),
            _renderer(container),
        )
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.callback_query(F.data.startswith("admin:block:"))
async def admin_block_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    target_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _prompt(
        callback,
        state,
        InputState.WAIT_ADMIN_BLOCK_REASON,
        "Введите причину блокировки.",
        target_id=target_id,
    )


@router.message(InputState.WAIT_ADMIN_BLOCK_REASON, F.text)
async def admin_block_input(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        await container.admin.set_blocked(
            message.from_user.id, int(data["target_id"]), True, message.text or ""
        )
        await _cleanup_input(message, state)
        await _show_after_input(
            message,
            await _renderer(container).admin_home(message.from_user.id),
            _renderer(container),
        )
    except DomainError as error:
        await _input_error(message, state, error)


@router.callback_query(F.data.startswith("admin:unblock:"))
async def admin_unblock(callback: CallbackQuery, container: Container) -> None:
    target_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.admin.set_blocked(callback.from_user.id, target_id, False, "")
        await callback.answer("Пользователь разблокирован.")
        renderer = _renderer(container)
        await _show(callback, await renderer.admin_home(callback.from_user.id), renderer)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:blacklists")
async def admin_blacklists(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_BLACKLISTS.value)
        series = await container.admin.blacklist(callback.from_user.id)
        rows = [[(f"✖ {item.series}", f"admin:blacklist:remove:{item.id}")] for item in series]
        rows.append([("➕ Добавить серию", "admin:blacklist:add")])
        rows.append([("⬅ Админ-панель", "admin:home")])
        renderer = _renderer(container)
        await _show(
            callback,
            await renderer.basic(
                Screen.ADMIN_BLACKLISTS.value, "Чёрные списки КЗ", "Запрещённые серии.", rows
            ),
            renderer,
        )
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:blacklist:add")
async def admin_blacklist_add_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await _prompt(
        callback,
        state,
        InputState.WAIT_ADMIN_BLACKLIST_SERIES,
        "Введите серию КЗ для добавления в чёрный список (2–3 латинские буквы).",
    )


@router.callback_query(F.data.startswith("admin:blacklist:remove:"))
async def admin_blacklist_remove(callback: CallbackQuery, container: Container) -> None:
    record_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        records = await container.admin.blacklist(callback.from_user.id)
        record = next((item for item in records if item.id == record_id), None)
        if record is None:
            raise ValidationError("Серия уже удалена.")
        await container.admin.blacklist_series(callback.from_user.id, "KZ", record.series, False)
        await admin_blacklists(callback, container)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.message(InputState.WAIT_ADMIN_BLACKLIST_SERIES, F.text)
async def admin_blacklist_input(message: Message, state: FSMContext, container: Container) -> None:
    try:
        await container.admin.blacklist_series(message.from_user.id, "KZ", message.text or "", True)
        await _cleanup_input(message, state)
        await _show_after_input(
            message,
            await _renderer(container).admin_home(message.from_user.id),
            _renderer(container),
        )
    except DomainError as error:
        await _input_error(message, state, error)


@router.callback_query(F.data == "admin:settings")
async def admin_settings(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_SETTINGS.value)
        values = await container.admin.settings_values(callback.from_user.id)
        text = "\n".join(f"<code>{key}</code> = {value}" for key, value in values.items())
        renderer = _renderer(container)
        view = await renderer.basic(
            Screen.ADMIN_SETTINGS.value,
            "Настройки платформы",
            text,
            [[("✏ Изменить", "admin:setting:edit")], [("⬅ Админ-панель", "admin:home")]],
        )
        await _show(callback, view, renderer)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:setting:edit")
async def admin_setting_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await _prompt(
        callback,
        state,
        InputState.WAIT_ADMIN_SETTING,
        "Введите настройку: <code>commission_percent=10</code>.",
    )


@router.message(InputState.WAIT_ADMIN_SETTING, F.text)
async def admin_setting_input(message: Message, state: FSMContext, container: Container) -> None:
    try:
        key, value = (message.text or "").split("=", 1)
        await container.admin.set_setting(message.from_user.id, key.strip(), int(value.strip()))
        await _cleanup_input(message, state)
        await _show_after_input(
            message,
            await _renderer(container).admin_home(message.from_user.id),
            _renderer(container),
        )
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.callback_query(F.data == "admin:cards")
async def admin_cards(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_CARDS.value)
        cards = await container.admin.cards(callback.from_user.id, Page(limit=50))
        rows = [[(f"✏ {card.card_id}", "admin:card:edit")] for card in cards]
        rows.append([("📣 Баннеры", "admin:banners")])
        rows.append([("⬅ Админ-панель", "admin:home")])
        renderer = _renderer(container)
        view = await renderer.basic(
            Screen.ADMIN_CARDS.value,
            "Карточки интерфейса",
            "Выберите карточку, затем укажите: <code>ID | Название | Описание | ID баннера</code>.",
            rows,
        )
        await _show(callback, view, renderer)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:card:edit")
async def admin_card_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await _prompt(
        callback,
        state,
        InputState.WAIT_ADMIN_CARD_TEXT,
        "Изменить карточку: <code>HOME | Название | Описание | ID баннера</code>. ID баннера необязателен.",
    )


@router.message(InputState.WAIT_ADMIN_CARD_TEXT, F.text)
async def admin_card_input(message: Message, state: FSMContext, container: Container) -> None:
    try:
        parts = [part.strip() for part in (message.text or "").split("|")]
        if len(parts) not in {3, 4}:
            raise ValidationError("Нужны ID, название, описание и необязательный ID баннера.")
        card_id, title, description = parts[:3]
        banner_id = int(parts[3]) if len(parts) == 4 and parts[3] else None
        await container.admin.edit_card(
            message.from_user.id, card_id, title, description, None, banner_id, True
        )
        await _delete(message, message.message_id)
        data = await state.get_data()
        if data.get("prompt_id"):
            await _delete(message, int(data["prompt_id"]))
        prompt = await message.bot.send_message(
            message.chat.id,
            "Отправьте изображение карточки или /skip, чтобы сохранить текущее изображение. /clear удалит его.",
        )
        await state.set_state(InputState.WAIT_ADMIN_CARD_IMAGE)
        await state.set_data({"prompt_id": prompt.message_id, "card_id": card_id})
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.message(InputState.WAIT_ADMIN_CARD_IMAGE, F.photo)
async def admin_card_image(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        photo = (message.photo or [])[-1]
        await container.admin.set_card_image(
            message.from_user.id, str(data["card_id"]), photo.file_id
        )
        await _cleanup_input(message, state)
        renderer = _renderer(container)
        await _show_after_input(message, await renderer.admin_home(message.from_user.id), renderer)
    except DomainError as error:
        await _input_error(message, state, error)


@router.message(InputState.WAIT_ADMIN_CARD_IMAGE, F.text.in_({"/skip", "/clear"}))
async def admin_card_image_skip(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        if (message.text or "") == "/clear":
            await container.admin.set_card_image(message.from_user.id, str(data["card_id"]), None)
        await _cleanup_input(message, state)
        renderer = _renderer(container)
        await _show_after_input(message, await renderer.admin_home(message.from_user.id), renderer)
    except DomainError as error:
        await _input_error(message, state, error)


def _parse_banner_time(value: str) -> datetime | None:
    if not value or value == "-":
        return None
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return parsed.replace(tzinfo=UTC)


@router.callback_query(F.data == "admin:banners")
async def admin_banners(callback: CallbackQuery, container: Container) -> None:
    try:
        banners = await container.admin.banners(callback.from_user.id, Page(limit=50))
        rows = [
            [(f"📣 #{banner.id} · {banner.title[:24]}", "admin:banner:new")] for banner in banners
        ]
        rows.append([("➕ Новый баннер", "admin:banner:new")])
        rows.append([("⬅ Карточки", "admin:cards")])
        renderer = _renderer(container)
        await _show(
            callback,
            await renderer.basic(
                Screen.ADMIN_CARDS.value,
                "Баннеры",
                "Баннер прикрепляется к карточке по его ID.",
                rows,
            ),
            renderer,
        )
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:banner:new")
async def admin_banner_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await _prompt(
        callback,
        state,
        InputState.WAIT_ADMIN_BANNER_TEXT,
        (
            "Новый баннер: <code>Название | Описание | Приоритет | "
            "2026-07-15 12:00 | 2026-07-20 12:00</code>. Время можно заменить на <code>-</code>."
        ),
    )


@router.message(InputState.WAIT_ADMIN_BANNER_TEXT, F.text)
async def admin_banner_input(message: Message, state: FSMContext, container: Container) -> None:
    try:
        parts = [part.strip() for part in (message.text or "").split("|")]
        if len(parts) != 5:
            raise ValidationError(
                "Нужно указать название, описание, приоритет, начало и окончание."
            )
        banner = await container.admin.create_banner(
            message.from_user.id,
            parts[0],
            parts[1],
            int(parts[2]),
            _parse_banner_time(parts[3]),
            _parse_banner_time(parts[4]),
        )
        await _delete(message, message.message_id)
        data = await state.get_data()
        if data.get("prompt_id"):
            await _delete(message, int(data["prompt_id"]))
        prompt = await message.bot.send_message(
            message.chat.id,
            "Отправьте изображение баннера или /skip, чтобы оставить баннер без изображения.",
        )
        await state.set_state(InputState.WAIT_ADMIN_BANNER_IMAGE)
        await state.set_data({"prompt_id": prompt.message_id, "banner_id": banner.id})
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.message(InputState.WAIT_ADMIN_BANNER_IMAGE, F.photo)
async def admin_banner_image(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        photo = (message.photo or [])[-1]
        await container.admin.set_banner_image(
            message.from_user.id, int(data["banner_id"]), photo.file_id
        )
        await _cleanup_input(message, state)
        renderer = _renderer(container)
        await _show_after_input(message, await renderer.admin_home(message.from_user.id), renderer)
    except DomainError as error:
        await _input_error(message, state, error)


@router.message(InputState.WAIT_ADMIN_BANNER_IMAGE, F.text == "/skip")
async def admin_banner_image_skip(
    message: Message, state: FSMContext, container: Container
) -> None:
    await _cleanup_input(message, state)
    renderer = _renderer(container)
    await _show_after_input(message, await renderer.admin_home(message.from_user.id), renderer)


@router.callback_query(F.data == "admin:plates")
async def admin_plates(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_PLATES.value)
        plates = await container.admin.plates(callback.from_user.id, Page())
        rows = [
            [(f"🔖 {plate.plate_number} · {plate.status}", f"admin:plate:{plate.id}")]
            for plate in plates
        ]
        rows.append([("⬅ Админ-панель", "admin:home")])
        renderer = _renderer(container)
        await _show(
            callback,
            await renderer.basic(
                Screen.ADMIN_PLATES.value,
                "Номера",
                f"Показано: {len(plates)}.",
                rows,
            ),
            renderer,
        )
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("admin:plate:"))
async def admin_plate(callback: CallbackQuery, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.navigation.push(callback.from_user.id, f"ADMIN_PLATE_VIEW:{plate_id}")
        plate = await container.admin.plate(callback.from_user.id, plate_id)
        rows = [
            [
                ("↩ Государству", f"admin:return:{plate.id}"),
                ("🎁 Передать", f"admin:transfer:{plate.id}"),
            ],
            [
                ("🚫 Снять с продажи", f"admin:remove-sale:{plate.id}"),
                ("📜 История", f"plate:history:{plate.id}"),
            ],
            [("⬅ Номера", "admin:plates")],
        ]
        renderer = _renderer(container)
        view = await renderer.basic(
            Screen.ADMIN_PLATE_VIEW.value,
            "Управление номером",
            (
                f"<b>{plate.plate_number}</b>\nСтатус: <code>{plate.status}</code>\n"
                f"Владелец ID: {plate.owner_id or 'Государство'}"
            ),
            rows,
        )
        await _show(callback, view, renderer)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("admin:return:"))
async def admin_return_to_state(callback: CallbackQuery, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.admin.force_return_to_state(
            callback.from_user.id, plate_id, "Manual admin action"
        )
        await admin_plate(callback, container)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("admin:remove-sale:"))
async def admin_remove_sale(callback: CallbackQuery, container: Container) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.admin.remove_sale(callback.from_user.id, plate_id, "Manual admin action")
        await admin_plate(callback, container)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("admin:transfer:"))
async def admin_transfer_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    plate_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _prompt(
        callback,
        state,
        InputState.WAIT_ADMIN_TRANSFER,
        "Введите ID пользователя и причину: <code>123 | причина передачи</code>.",
        plate_id=plate_id,
    )


@router.message(InputState.WAIT_ADMIN_TRANSFER, F.text)
async def admin_transfer_input(message: Message, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    try:
        target_id, reason = (message.text or "").split("|", 1)
        await container.admin.force_transfer(
            message.from_user.id,
            int(data["plate_id"]),
            int(target_id.strip()),
            reason.strip(),
        )
        await _cleanup_input(message, state)
        renderer = _renderer(container)
        await _show_after_input(message, await renderer.admin_home(message.from_user.id), renderer)
    except (ValueError, DomainError) as error:
        await _input_error(message, state, error)


@router.callback_query(F.data == "admin:auctions")
async def admin_auctions(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_AUCTIONS.value)
        auctions = await container.admin.auctions(callback.from_user.id, Page())
        rows = [
            [(f"🔨 #{auction.id} · {auction.status}", f"admin:auction:{auction.id}")]
            for auction in auctions
        ]
        rows.append([("⬅ Админ-панель", "admin:home")])
        renderer = _renderer(container)
        await _show(
            callback,
            await renderer.basic(
                Screen.ADMIN_AUCTIONS.value,
                "Аукционы",
                f"Показано: {len(auctions)}.",
                rows,
            ),
            renderer,
        )
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:auction:\d+$"))
async def admin_auction(callback: CallbackQuery, container: Container) -> None:
    auction_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.navigation.push(callback.from_user.id, f"ADMIN_AUCTION_VIEW:{auction_id}")
        auction = await container.admin.auction(callback.from_user.id, auction_id)
        rows = [
            [
                ("🔨 Завершить", f"admin:auction:finish:{auction.id}"),
                ("🚫 Отменить", f"admin:auction:cancel:{auction.id}"),
            ],
            [("📜 Ставки", f"auction:history:{auction.id}"), ("⬅ Аукционы", "admin:auctions")],
        ]
        renderer = _renderer(container)
        await _show(
            callback,
            await renderer.basic(
                Screen.ADMIN_AUCTION_VIEW.value,
                "Управление аукционом",
                f"ID: {auction.id}\nСтатус: {auction.status}\nТекущая цена: ⭐{auction.current_price}",
                rows,
            ),
            renderer,
        )
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("admin:auction:finish:"))
async def admin_finish_auction(callback: CallbackQuery, container: Container) -> None:
    auction_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.admin.force_finish_auction(callback.from_user.id, auction_id)
        await admin_auction(callback, container)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("admin:auction:cancel:"))
async def admin_cancel_auction(callback: CallbackQuery, container: Container) -> None:
    auction_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await container.admin.force_cancel_auction(
            callback.from_user.id, auction_id, "Manual admin action"
        )
        await admin_auction(callback, container)
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:finance")
async def admin_finance(callback: CallbackQuery, container: Container) -> None:
    try:
        await container.navigation.push(callback.from_user.id, Screen.ADMIN_FINANCE.value)
        total, entries = await container.admin.finance(callback.from_user.id, Page())
        lines = [f"• {entry.type}: ⭐{entry.amount}" for entry in entries] or ["Записей пока нет."]
        renderer = _renderer(container)
        await _show(
            callback,
            await renderer.basic(
                Screen.ADMIN_FINANCE.value,
                "Финансы Государства",
                f"Доход: ⭐{total}\n\n" + "\n".join(lines),
                [[("⬅ Админ-панель", "admin:home")]],
            ),
            renderer,
        )
    except DomainError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data == "admin:backups")
async def admin_backup(callback: CallbackQuery, container: Container) -> None:
    try:
        user = await container.marketplace.get_user(callback.from_user.id)
        if not user.is_admin:
            raise ValidationError("Недостаточно прав администратора.")
        item = await container.backups.create(user.id)
        if callback.message is not None:
            await callback.message.bot.send_document(
                callback.from_user.id, item.storage_path, caption=item.file_name
            )
        await callback.answer("Резервная копия создана.")
    except Exception as error:
        await callback.answer(str(error), show_alert=True)


@router.message(F.chat.type == ChatType.PRIVATE, F.photo)
async def save_profile_cover(message: Message, container: Container) -> None:
    """Telegram's photo file ID is durable enough to use as the user's cover."""
    if message.from_user is None or not message.photo:
        return
    await container.marketplace.set_cover_photo(message.from_user.id, message.photo[-1].file_id)
    await message.answer("Обложка сохранена. Она будет показана вместе с основным номером в группе.")


@router.pre_checkout_query()
async def precheckout(query: PreCheckoutQuery, container: Container) -> None:
    try:
        await container.marketplace.validate_precheckout(
            query.from_user.id, query.invoice_payload, query.total_amount
        )
        await query.answer(ok=True)
    except DomainError as error:
        await query.answer(ok=False, error_message=str(error))


@router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: Message, container: Container) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    payment = message.successful_payment
    try:
        await container.marketplace.complete_telegram_payment(
            message.from_user.id,
            payment.invoice_payload,
            payment.total_amount,
            payment.telegram_payment_charge_id,
        )
        await _show_after_input(
            message, await _view_for_screen(container, message.from_user.id), _renderer(container)
        )
    except DomainError:
        # Payment reconciliation is retained transactionally; support can inspect the intent/audit trail.
        return


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела. Откройте раздел заново.", show_alert=True)
