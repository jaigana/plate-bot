from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class CountryDefinition:
    code: str
    name: str
    active: bool
    validator: str


COUNTRIES: Final[dict[str, CountryDefinition]] = {
    "RU": CountryDefinition("RU", "Россия", True, "russia"),
    "KZ": CountryDefinition("KZ", "Казахстан", True, "kazakhstan"),
    "UA": CountryDefinition("UA", "Украина", False, "unsupported"),
    "BY": CountryDefinition("BY", "Беларусь", False, "unsupported"),
    "KG": CountryDefinition("KG", "Кыргызстан", False, "unsupported"),
    "UZ": CountryDefinition("UZ", "Узбекистан", False, "unsupported"),
    "TJ": CountryDefinition("TJ", "Таджикистан", False, "unsupported"),
    "TM": CountryDefinition("TM", "Туркменистан", False, "unsupported"),
    "AM": CountryDefinition("AM", "Армения", False, "unsupported"),
    "AZ": CountryDefinition("AZ", "Азербайджан", False, "unsupported"),
    "MD": CountryDefinition("MD", "Молдова", False, "unsupported"),
}
