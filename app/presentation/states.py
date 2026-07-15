from aiogram.fsm.state import State, StatesGroup


class InputState(StatesGroup):
    WAIT_SEARCH_QUERY = State()
    WAIT_TOPUP_AMOUNT = State()
    WAIT_PLATE_INPUT = State()
    WAIT_SALE_PRICE = State()
    WAIT_AUCTION_START_PRICE = State()
    WAIT_AUCTION_DURATION = State()
    WAIT_BID_AMOUNT = State()
    WAIT_ADMIN_CARD_TEXT = State()
    WAIT_ADMIN_CARD_IMAGE = State()
    WAIT_ADMIN_BLACKLIST_SERIES = State()
    WAIT_ADMIN_SETTING = State()
    WAIT_ADMIN_BALANCE = State()
    WAIT_ADMIN_BLOCK_REASON = State()
    WAIT_ADMIN_TRANSFER = State()
    WAIT_ADMIN_BANNER_TEXT = State()
    WAIT_ADMIN_BANNER_IMAGE = State()
