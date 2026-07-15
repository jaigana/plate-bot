import pytest
from app.domain.errors import ValidationError
from app.domain.validators import (
    normalize_plate_query,
    validate_kazakhstan_plate,
    validate_russian_plate,
)


def test_russian_plate_is_normalized_without_transliteration() -> None:
    assert validate_russian_plate("а777 аа 77") == "А777АА77"


def test_kazakhstan_plate_is_normalized() -> None:
    assert validate_kazakhstan_plate("777aaa01") == "777AAA01"


@pytest.mark.parametrize(
    "raw",
    [
        "А٧٧٧АА77",  # Arabic-Indic digits
        "А777AА77",  # Latin A mixed with Cyrillic alphabet
        "777ßA01",  # ß uppercases to two ASCII characters
        "777ΑΑΑ01",  # Greek alpha looks like Latin A
    ],
)
def test_confusable_or_non_ascii_input_is_rejected(raw: str) -> None:
    validator = validate_kazakhstan_plate if raw.startswith("777") else validate_russian_plate
    with pytest.raises(ValidationError):
        validator(raw)


def test_partial_query_uses_the_same_security_boundary() -> None:
    assert normalize_plate_query("RU", "а 777") == "А777"
    with pytest.raises(ValidationError):
        normalize_plate_query("RU", "A777")
