"""Turn what a person says ("in 10 minutes", "tomorrow at 9am", "every weekday at 8") into times.

The model never works out times itself: it passes the person's words along and this code decides,
so "remind me at 6" cannot become the wrong hour because a small model did arithmetic. Times are
the computer's local time. Two kinds of schedule come out:

- once: one moment (a reminder);
- cron: a repeating rule in the usual five-field form `minute hour day-of-month month weekday`
  (weekday 0 or 7 = Sunday), with a floor of one fire every 5 minutes so nothing can flood the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MIN_INTERVAL = timedelta(minutes=5)
WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5,
    "sat": 5, "sunday": 6, "sun": 6,
}  # fmt: skip
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
    "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12,
    "dec": 12,
}  # fmt: skip
FULL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
                "thirty": 30, "forty": 40, "sixty": 60}  # fmt: skip
HELP = (
    "Try 'in 10 minutes', 'tomorrow at 9am', 'at 6pm', 'next friday 3pm' or 'every weekday at 8'."
)


class WhenError(ValueError):
    """The words could not be turned into a time (shown to the model and the user)."""


@dataclass(frozen=True)
class When:
    kind: str  # "once" or "cron"
    label: str  # how to say it back to the user
    at: datetime | None = None
    cron: str | None = None


# ---- cron ------------------------------------------------------------------------------------


def _field(text: str, low: int, high: int) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise WhenError(f"bad step in {text!r}")
            step = int(step_text)
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit()):
                raise WhenError(f"bad range in {text!r}")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = int(part)
            end = high if step > 1 else start
        else:
            raise WhenError(f"bad value {part!r} in {text!r}")
        if start < low or end > high or start > end:
            raise WhenError(f"{text!r} is out of range ({low}-{high})")
        out.update(range(start, end + 1, step))
    return out


class Cron:
    """`minute hour day-of-month month weekday`, with *, lists, ranges and steps."""

    def __init__(self, expr: str) -> None:
        parts = expr.split()
        if len(parts) != 5:
            raise WhenError("a cron rule has five fields: minute hour day month weekday")
        self.expr = " ".join(parts)
        self.minutes = sorted(_field(parts[0], 0, 59))
        self.hours = sorted(_field(parts[1], 0, 23))
        self.days = _field(parts[2], 1, 31)
        self.months = _field(parts[3], 1, 12)
        weekdays = _field(parts[4], 0, 7)
        self.weekdays = {0 if d == 7 else d for d in weekdays}
        self._day_star = parts[2].startswith("*")
        self._weekday_star = parts[4].startswith("*")

    def _day_ok(self, d: date) -> bool:
        if d.month not in self.months:
            return False
        cron_weekday = (d.weekday() + 1) % 7  # Monday=1 ... Sunday=0
        by_day, by_weekday = d.day in self.days, cron_weekday in self.weekdays
        if self._day_star and self._weekday_star:
            return True
        if self._day_star:
            return by_weekday
        if self._weekday_star:
            return by_day
        return by_day or by_weekday  # both given: either one (the classic cron rule)

    def next_after(self, moment: datetime) -> datetime | None:
        """The first fire strictly after `moment`."""
        start = moment.replace(second=0, microsecond=0)
        for offset in range(0, 800):
            day = start.date() + timedelta(days=offset)
            if not self._day_ok(day):
                continue
            for hour in self.hours:
                for minute in self.minutes:
                    candidate = datetime(day.year, day.month, day.day, hour, minute)
                    if candidate > moment:
                        return candidate
        return None

    def last_at_or_before(self, moment: datetime) -> datetime | None:
        """The latest fire time that is not after `moment`."""
        for offset in range(0, 800):
            day = moment.date() - timedelta(days=offset)
            if not self._day_ok(day):
                continue
            for hour in reversed(self.hours):
                for minute in reversed(self.minutes):
                    candidate = datetime(day.year, day.month, day.day, hour, minute)
                    if candidate <= moment:
                        return candidate
        return None

    def check_not_too_often(self, now: datetime) -> None:
        first = self.next_after(now)
        second = self.next_after(first) if first else None
        if first and second and second - first < MIN_INTERVAL:
            raise WhenError("that would fire more than once every 5 minutes; pick a longer gap")


# ---- words ------------------------------------------------------------------------------------


def _clock(hour: int, minute: int) -> str:
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


_TIME = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b|"  # 6pm, 6:30 pm, at 6 pm
    r"\b(?:at\s+)?(\d{1,2}):(\d{2})\b|"  # 18:30
    r"\bat\s+(\d{1,2})\b|"  # at 6
    r"\b(noon|midday|midnight)\b|\b(morning|afternoon|evening|tonight|night)\b"
)
_PART_OF_DAY = {"morning": 9, "afternoon": 15, "evening": 18, "tonight": 20, "night": 21}


def _find_time(text: str) -> tuple[int, int, bool] | None:
    """(hour, minute, certain). `certain` is False for a bare 'at 6' that may be am or pm."""
    for m in _TIME.finditer(text):
        if m.group(1) and m.group(3):
            hour, minute, mark = int(m.group(1)), int(m.group(2) or 0), m.group(3)[0]
            if not (1 <= hour <= 12 and minute < 60):
                raise WhenError(f"{m.group(0).strip()!r} is not a time of day")
            return (hour % 12) + (12 if mark == "p" else 0), minute, True
        if m.group(4):
            hour, minute = int(m.group(4)), int(m.group(5))
            if hour > 23 or minute > 59:
                raise WhenError(f"{m.group(0).strip()!r} is not a time of day")
            return hour, minute, True
        if m.group(6):
            hour = int(m.group(6))
            if hour > 23:
                raise WhenError(f"{m.group(0).strip()!r} is not a time of day")
            return hour, 0, hour > 12 or hour == 0
        if m.group(7):
            return (12 if m.group(7) in ("noon", "midday") else 0), 0, True
        if m.group(8):
            return _PART_OF_DAY[m.group(8)], 0, True
    return None


def _amount(word: str) -> int:
    if word.isdigit():
        return int(word)
    if word in NUMBER_WORDS:
        return NUMBER_WORDS[word]
    raise WhenError(f"I don't know how many {word!r} is")


def _relative(text: str, now: datetime) -> datetime | None:
    m = re.search(
        r"\bin\s+(?:about\s+|around\s+)?(half an|half a|a quarter of an|\w+)\s*"
        r"(second|sec|minute|min|hour|hr|day|week)s?\b",
        text,
    )
    if not m:
        return None
    word, unit = m.group(1), m.group(2)
    if word.startswith("half"):
        amount: float = 0.5
    elif word.startswith("a quarter"):
        amount = 0.25
    else:
        amount = _amount(word)
    seconds = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600,
               "day": 86400, "week": 604800}[unit]  # fmt: skip
    return (now + timedelta(seconds=amount * seconds)).replace(microsecond=0)


def _day_anchor(text: str, now: datetime) -> tuple[date, str] | None:
    today = now.date()
    if re.search(r"\bday after tomorrow\b", text):
        return today + timedelta(days=2), "day after tomorrow"
    if re.search(r"\btomorrow\b", text):
        return today + timedelta(days=1), "tomorrow"
    if re.search(r"\b(today|tonight|this (?:morning|afternoon|evening))\b", text):
        return today, "today"
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3])), ""
        except ValueError as exc:
            raise WhenError("that date does not exist") from exc
    dm = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b(?!:)", text)
    if dm and not re.search(r"\b\d{1,2}:\d{2}\b", dm.group(0)):
        day, month = int(dm[1]), int(dm[2])
        year = int(dm[3]) + (2000 if dm[3] and len(dm[3]) == 2 else 0) if dm[3] else today.year
        try:
            found = date(year, month, day)
        except ValueError as exc:
            raise WhenError("that date does not exist (dates are day/month)") from exc
        if found < today and not dm[3]:
            found = date(year + 1, month, day)
        return found, ""
    names = "|".join(sorted(MONTHS, key=len, reverse=True))
    named = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({names})\b", text) or re.search(
        rf"\b({names})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text
    )
    if named:
        a, b = named.groups()
        day, month = (int(a), MONTHS[b]) if a.isdigit() else (int(b), MONTHS[a])
        try:
            found = date(today.year, month, day)
        except ValueError as exc:
            raise WhenError("that date does not exist") from exc
        if found < today:
            found = date(today.year + 1, month, day)
        return found, ""
    days = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
    weekday = re.search(rf"\b(next\s+)?({days})\b", text)
    if weekday:
        ahead = (WEEKDAYS[weekday.group(2)] - today.weekday()) % 7
        if weekday.group(1) and ahead == 0:
            ahead = 7  # "next monday" said on a Monday is a week away
        return today + timedelta(days=ahead), "weekday"
    return None


def _pick_hour(
    hour: int, minute: int, certain: bool, anchor: date, now: datetime, dated: bool
) -> int:
    """A bare 'at 6' is whichever 6 comes next today, or a sensible guess for another day."""
    if certain or hour > 12 or hour == 0:
        return hour
    if not dated or anchor == now.date():
        for candidate in (hour % 12, hour % 12 + 12):
            if datetime(anchor.year, anchor.month, anchor.day, candidate, minute) > now:
                return candidate
        return hour % 12 + 12
    return hour if 7 <= hour <= 11 else (hour % 12) + 12  # other days: 7-11 morning, else afternoon


def _label_once(moment: datetime, now: datetime) -> str:
    when = _clock(moment.hour, moment.minute)
    if moment.date() == now.date():
        return f"today at {when}"
    if moment.date() == now.date() + timedelta(days=1):
        return f"tomorrow at {when}"
    return f"{moment:%a %d %b} at {when}"


def _recurring(text: str, now: datetime) -> When:
    interval = re.search(r"every\s+(?:(\d+|\w+)\s+)?(minute|min|hour|hr)s?\b", text)
    if interval:
        n = _amount(interval.group(1)) if interval.group(1) else 1
        if interval.group(2) in ("minute", "min"):
            cron, label = f"*/{n} * * * *", f"every {n} minutes" if n != 1 else "every minute"
        else:
            cron, label = (
                (f"0 */{n} * * *", f"every {n} hours") if n != 1 else ("0 * * * *", "every hour")
            )
    else:
        found = _find_time(text)
        hour, minute, _certain = found if found else (9, 0, True)
        if found and not found[2]:
            hour = hour if hour > 12 or hour >= 7 else hour + 12  # "every day at 6": evening
        names = [
            d for d in sorted(WEEKDAYS, key=len, reverse=True) if re.search(rf"\b{d}s?\b", text)
        ]
        dom = "*"
        if re.search(r"\bweekdays?\b", text):
            dow, words = "1-5", "weekday"
        elif re.search(r"\bweekends?\b", text):
            dow, words = "0,6", "weekend"
        elif names:
            chosen = sorted({(WEEKDAYS[n] + 1) % 7 for n in names})
            dow = ",".join(str(d) for d in chosen)
            words = (
                " and ".join(
                    sorted(
                        {
                            n.capitalize()
                            for n in names
                            if len(n) > 3 or n in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
                        }
                    )
                )
                or "week"
            )
            words = words.lower()
        else:
            dow, words = "*", "day"
        month_day = re.search(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", text)
        if month_day and re.search(r"\b(month|monthly)\b", text):
            dom, dow, words = (
                str(int(month_day.group(1))),
                "*",
                f"month on day {int(month_day.group(1))}",
            )
        cron = f"{minute} {hour} {dom} * {dow}"
        label = f"every {words} at {_clock(hour, minute)}"
    Cron(cron).check_not_too_often(now)
    return When("cron", label, cron=cron)


def parse_when(words: str, now: datetime | None = None) -> When:
    """The schedule meant by `words`, or a WhenError that says what to try."""
    now = (now or datetime.now()).replace(microsecond=0)
    text = re.sub(r"\s+", " ", words.lower().strip().strip(".,!?"))
    if not text:
        raise WhenError(f"say when. {HELP}")
    if re.search(r"\b(yesterday|last (?:week|night|month|year)|ago)\b", text):
        raise WhenError(f"{words!r} is in the past, and I can only remind you about the future")
    if re.search(r"\b(every|each|daily|weekly|hourly|monthly)\b", text):
        text = text.replace("daily", "every day").replace("weekly", "every week")
        text = text.replace("hourly", "every hour").replace("monthly", "every month")
        return _recurring(text, now)
    moment = _relative(text, now)
    if moment is None:
        anchor = _day_anchor(text, now)
        found = _find_time(text)
        if anchor is None and found is None:
            raise WhenError(f"I couldn't tell when {words!r} is. {HELP}")
        day = anchor[0] if anchor else now.date()
        if found:
            hour, minute, certain = found
            hour = _pick_hour(hour, minute, certain, day, now, dated=anchor is not None)
        else:
            hour, minute = (20, 0) if re.search(r"\btonight\b", text) else (9, 0)
        moment = datetime(day.year, day.month, day.day, hour, minute)
        if anchor is None and moment <= now:
            moment += timedelta(days=1)  # "at 6pm" said at 7pm means tomorrow
        elif anchor is not None and anchor[1] == "weekday" and moment <= now:
            moment += timedelta(days=7)  # "monday at 9" said on Monday at noon: next Monday
    if moment <= now:
        raise WhenError(f"{_label_once(moment, now)} has already passed. {HELP}")
    if moment - now > timedelta(days=366 * 2):
        raise WhenError("that is more than two years away")
    return When("once", _label_once(moment, now), at=moment)
