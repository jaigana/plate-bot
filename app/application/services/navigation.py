from app.domain.enums import Screen
from app.domain.errors import NotFoundError, ValidationError
from app.infrastructure.db.session import UnitOfWork
from app.infrastructure.repositories.marketplace import UserRepository


class NavigationService:
    """Persists the single-message screen stack; rendering remains presentation-only."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._users = UserRepository()

    @staticmethod
    def _validate(screen: str) -> str:
        if not screen or len(screen) > 64:
            raise ValidationError("Некорректный экран.")
        return screen

    async def current(self, telegram_id: int) -> str:
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            return user.screen_stack[-1] if user.screen_stack else Screen.HOME.value

    async def push(self, telegram_id: int, screen: str) -> str:
        target = self._validate(screen)
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            stack = list(user.screen_stack or [Screen.HOME.value])
            if stack[-1] != target:
                stack.append(target)
            user.screen_stack = stack[-32:]
            user.last_screen = target
            return target

    async def replace(self, telegram_id: int, screen: str) -> str:
        target = self._validate(screen)
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            stack = list(user.screen_stack or [Screen.HOME.value])
            stack[-1] = target
            user.screen_stack = stack
            user.last_screen = target
            return target

    async def back(self, telegram_id: int) -> str:
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            stack = list(user.screen_stack or [Screen.HOME.value])
            if len(stack) > 1:
                stack.pop()
            user.screen_stack = stack
            user.last_screen = stack[-1]
            return stack[-1]

    async def home(self, telegram_id: int) -> str:
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            user.screen_stack = [Screen.HOME.value]
            user.last_screen = Screen.HOME.value
            return Screen.HOME.value
