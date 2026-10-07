"""Spoken course codes in a transcript, rewritten as catalog codes.

Deepgram hears "computer science two forty three" and writes exactly that: it
keeps spoken numbers as words, and its `numerals` option makes things worse
("2 43"). This turns such phrases into "COSC 243" so the student sees - and the
advisor receives - a code the engine understands.

One rule matters more than the parsing: a rewrite happens only when the result
is a course in the catalog. "Computer science nine ninety nine" is left exactly
as heard. The system never invents a course code, here or anywhere else.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

#: How people say the catalog's subjects out loud. The subject code itself
#: ("cosc", "math") is always accepted too, so only spoken names go here.
SUBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "BIOL": ("biology",),
    "CLCO": ("cloud computing",),
    "COSC": ("computer science",),
    "ECON": ("economics", "econ"),
    "EEGR": ("electrical engineering",),
    "ENGL": ("english",),
    "HIST": ("history",),
    "MATH": ("math", "mathematics"),
    "PHIL": ("philosophy",),
    "PHYS": ("physics",),
    "UNIV": ("university",),
}

_DIGITS = {
    "zero": 0,
    "oh": 0,
    "o": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}
_TEENS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_CODE = re.compile(r"^([A-Z]+)(\d{3})$")
#: One word or digit group after a space or hyphen ("forty-three").
_NEXT_TOKEN = re.compile(r"(?:\s+|-)(\d+|[A-Za-z]+)")
#: A course number is three digits and at most "two hundred and forty three".
_MAX_TOKENS = 6


def normalize_course_mentions(text: str, catalog_codes: Iterable[str]) -> str:
    """Rewrite "<subject> <spoken number>" as "SUBJ NNN" wherever that code exists."""
    codes = {code.upper() for code in catalog_codes}
    phrases: dict[str, str] = {}
    for code in codes:
        match = _CODE.match(code)
        if not match:
            continue
        subject = match[1]
        phrases[subject.lower()] = subject
        for alias in SUBJECT_ALIASES.get(subject, ()):
            phrases[alias] = subject
    if not phrases:
        return text

    alternation = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    subject_pattern = re.compile(rf"\b({alternation})\b", re.IGNORECASE)

    pieces: list[str] = []
    done = 0
    for match in subject_pattern.finditer(text):
        if match.start() < done:
            continue  # inside a number already consumed by the previous rewrite
        subject = phrases[match[1].lower()]
        tokens = _tokens_after(text, match.end())
        parsed = _parse_course_number([word for word, _ in tokens])
        if parsed is None:
            continue
        number, used = parsed
        if f"{subject}{number}" not in codes:
            continue
        pieces += [text[done : match.start()], f"{subject} {number}"]
        done = tokens[used - 1][1]
    pieces.append(text[done:])
    return "".join(pieces)


def _tokens_after(text: str, position: int) -> list[tuple[str, int]]:
    """Up to _MAX_TOKENS (word, end offset) pairs directly following `position`."""
    tokens: list[tuple[str, int]] = []
    while len(tokens) < _MAX_TOKENS:
        match = _NEXT_TOKEN.match(text, position)
        if not match:
            break
        tokens.append((match[1], match.end()))
        position = match.end()
    return tokens


def _parse_course_number(words: Sequence[str]) -> tuple[str, int] | None:
    """A three-digit course number from the start of `words`, and how many it used."""
    if not words:
        return None
    if words[0].isdigit():
        return _from_digit_groups(words)
    return _from_number_words([w.lower() for w in words])


def _from_digit_groups(words: Sequence[str]) -> tuple[str, int] | None:
    """ "243", "2 43", "1 0 1" -> joined, if the groups make exactly three digits."""
    joined = ""
    for used, word in enumerate(words, start=1):
        if not word.isdigit():
            break
        joined += word
        if len(joined) == 3:
            return joined, used
        if len(joined) > 3:
            break
    return None


def _from_number_words(w: Sequence[str]) -> tuple[str, int] | None:
    hundreds = _DIGITS.get(w[0])
    if not hundreds:  # "zero"/"oh" cannot lead, and a non-number word ends it
        return None
    rest = list(w[1:])

    if rest[:1] == ["hundred"]:
        after = rest[1:]
        skipped = 2
        if after[:1] == ["and"]:
            after, skipped = after[1:], 3
        below, used = _below_hundred(after)
        return f"{hundreds * 100 + below:03d}", skipped + used

    if len(rest) >= 2 and rest[0] in _DIGITS and rest[1] in _DIGITS:
        return f"{hundreds}{_DIGITS[rest[0]]}{_DIGITS[rest[1]]}", 3

    below, used = _below_hundred(rest)
    if used == 0:
        return None
    return f"{hundreds * 100 + below:03d}", 1 + used


def _below_hundred(w: Sequence[str]) -> tuple[int, int]:
    """ "forty three" -> (43, 2); "fifteen" -> (15, 1); "one" -> (1, 1); else (0, 0)."""
    if not w:
        return 0, 0
    if w[0] in _TEENS:
        return _TEENS[w[0]], 1
    if w[0] in _TENS:
        units = _DIGITS.get(w[1], 0) if len(w) > 1 else 0
        return _TENS[w[0]] + units, 2 if units else 1
    if _DIGITS.get(w[0]):
        return _DIGITS[w[0]], 1
    return 0, 0
