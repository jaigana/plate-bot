from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def button(text: str, callback_data: str) -> InlineKeyboardButton:
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Telegram callback_data must not exceed 64 bytes")
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def keyboard(rows: Sequence[Sequence[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[button(text, data) for text, data in row] for row in rows]
    )


def back_home_rows() -> list[list[tuple[str, str]]]:
    return [[("⬅ Назад", "nav:back"), ("⌂ Главная", "nav:home")]]
