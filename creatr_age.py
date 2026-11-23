"""Every account holder must be 18 or over. A date of birth and a confirmation are both required."""

from __future__ import annotations

import os
from datetime import date

RULE = "All users must be 18 or over."


def minimum_age() -> int:
    raw = os.getenv("MIN_USER_AGE", "").strip() or os.getenv("MIN_CREATR_AGE", "").strip()
    if not raw:
        return 18
    try:
        return max(0, int(raw))
    except ValueError:
        return 18


def age_on(born: date, on: date) -> int:
    years = on.year - born.year
    if (on.month, on.day) < (born.month, born.day):
        years -= 1
    return years


def parse_born(raw: str) -> tuple[date | None, str]:
    text = (raw or "").strip()
    if not text:
        return None, ""
    try:
        return date.fromisoformat(text), ""
    except ValueError:
        return None, f"Enter a real date of birth. {RULE}"


def check_signup(*, born: date | None, attested: bool, on: date | None = None) -> str:
    """Return an error message, or an empty string when the person may join."""
    on = on or date.today()
    minimum = minimum_age()
    if born is None or not attested:
        return f"All users must be {minimum} or over. Enter your date of birth and confirm you are {minimum} or over."
    if age_on(born, on) < minimum:
        return f"All users must be {minimum} or over."
    return ""
