import re
import unicodedata
from collections.abc import Iterable

from app.domain.errors import ValidationError

RUSSIAN_LETTERS = frozenset("АВЕКМНОРСТУХ")
LATIN_LETTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
ASCII_DIGITS = frozenset("0123456789")
# Officially assigned Russian region codes, including historic/reissued codes still visible in-game.
RUSSIAN_REGION_CODES = frozenset(
    {
        *range(1, 100),
        102,
        116,
        121,
        123,
        124,
        125,
        126,
        134,
        136,
        138,
        142,
        147,
        150,
        152,
        154,
        156,
        159,
        161,
        163,
        164,
        169,
        173,
        174,
        177,
        178,
        180,
        181,
        184,
        186,
        190,
        193,
        196,
        197,
        198,
        199,
        702,
        716,
        717,
        750,
        761,
        763,
        777,
        790,
        797,
        799,
    }
)
_RU_PATTERN = re.compile(r"^([АВЕКМНОРСТУХ])([0-9]{3})([АВЕКМНОРСТУХ]{2})([0-9]{2,3})$")
_KZ_INDIVIDUAL_PATTERN = re.compile(r"^([0-9]{3})([A-Z]{3})([0-9]{2})$")
_KZ_LEGAL_PATTERN = re.compile(r"^([0-9]{3})([A-Z]{2})([0-9]{2})$")


def normalize_plate_input(raw: str) -> str:
    """Normalise presentation-only separators without transliterating any Unicode characters."""
    if not isinstance(raw, str) or not raw:
        raise ValidationError("Введите номер.")
    if len(raw) > 64:
        raise ValidationError("Номер слишком длинный.")
    # NFKC would turn mathematical and full-width lookalikes into valid-looking text.
    # Reject them instead; only standard separators may be discarded.
    if any(unicodedata.category(char) in {"Cf", "Cc"} for char in raw):
        raise ValidationError("Номер содержит недопустимые символы Unicode.")
    normalized: list[str] = []
    for char in raw:
        if char in " -\t\n\r":
            continue
        upper = char.upper()
        # Unicode case folding can expand a single confusable character (for example ß)
        # into a valid-looking ASCII sequence. Such input must never be transliterated.
        if len(upper) != 1:
            raise ValidationError("Номер содержит недопустимый символ Unicode.")
        normalized.append(upper)
    value = "".join(normalized)
    if not value:
        raise ValidationError("Введите номер.")
    return value


def _assert_exact_alphabet(value: str, allowed_letters: frozenset[str]) -> None:
    for char in value:
        if char.isalpha() and char not in allowed_letters:
            raise ValidationError("Недопустимый алфавит или визуально похожий символ в номере.")
        if char not in ASCII_DIGITS and char not in allowed_letters:
            raise ValidationError("Номер содержит недопустимый символ.")


def normalize_plate_query(country_code: str, raw: str) -> str:
    """Normalise a partial search query with the same anti-spoofing rules as issuance."""
    value = normalize_plate_input(raw)
    code = country_code.upper()
    if code == "RU":
        _assert_exact_alphabet(value, RUSSIAN_LETTERS)
    elif code == "KZ":
        _assert_exact_alphabet(value, LATIN_LETTERS)
    else:
        raise ValidationError("Для выбранной страны валидатор пока не активирован.")
    return value


def validate_russian_plate(raw: str) -> str:
    value = normalize_plate_input(raw)
    _assert_exact_alphabet(value, RUSSIAN_LETTERS)
    match = _RU_PATTERN.fullmatch(value)
    if match is None:
        raise ValidationError("Формат РФ: А777АА77 или Х001АМ197.")
    digits, region = match.group(2), int(match.group(4))
    if digits == "000":
        raise ValidationError("Три нуля в номере не допускаются.")
    if region not in RUSSIAN_REGION_CODES:
        raise ValidationError("Укажите существующий код региона РФ.")
    return value


def validate_kazakhstan_plate(raw: str, blacklisted_series: Iterable[str] = ()) -> str:
    value = normalize_plate_input(raw)
    _assert_exact_alphabet(value, LATIN_LETTERS)
    match = _KZ_INDIVIDUAL_PATTERN.fullmatch(value) or _KZ_LEGAL_PATTERN.fullmatch(value)
    if match is None:
        raise ValidationError("Формат КЗ: 777AAA01 или 001AA01.")
    region = int(match.group(3))
    if not 1 <= region <= 20:
        raise ValidationError("Код региона Казахстана должен быть от 01 до 20.")
    series = match.group(2)
    forbidden = {item.upper() for item in blacklisted_series}
    if series in forbidden:
        raise ValidationError("Эта серия запрещена правилами площадки.")
    return value


def validate_plate(country_code: str, raw: str, blacklisted_series: Iterable[str] = ()) -> str:
    code = country_code.upper()
    if code == "RU":
        return validate_russian_plate(raw)
    if code == "KZ":
        return validate_kazakhstan_plate(raw, blacklisted_series)
    raise ValidationError("Для выбранной страны валидатор пока не активирован.")
