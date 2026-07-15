from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.application.services.navigation import NavigationService
from app.domain.enums import Screen


class FakeUnitOfWork:
    @asynccontextmanager
    async def transaction(self):
        yield object()


async def test_navigation_keeps_a_bounded_history_and_returns_to_previous_screen() -> None:
    user = SimpleNamespace(screen_stack=[Screen.HOME.value], last_screen=Screen.HOME.value)
    navigation = NavigationService(FakeUnitOfWork())  # type: ignore[arg-type]
    navigation._users.get_by_telegram_id = AsyncMock(return_value=user)  # type: ignore[method-assign]

    await navigation.push(1, Screen.MARKET.value)
    await navigation.push(1, "PLATE_VIEW:42")

    assert user.screen_stack == [Screen.HOME.value, Screen.MARKET.value, "PLATE_VIEW:42"]
    assert await navigation.back(1) == Screen.MARKET.value
    assert user.last_screen == Screen.MARKET.value
