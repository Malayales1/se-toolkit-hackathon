from __future__ import annotations

import calendar
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _shift_month(target_month: date, delta: int) -> date:
    month_index = (target_month.year * 12 + target_month.month - 1) + delta
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def build_month_calendar(
    *,
    target_month: date,
    callback_prefix: str,
    selected_date: date | None,
    min_date: date | None,
) -> InlineKeyboardMarkup:
    cal = calendar.Calendar(firstweekday=0)
    rows: list[list[InlineKeyboardButton]] = []

    prev_month = _shift_month(target_month, -1)
    next_month = _shift_month(target_month, 1)

    rows.append(
        [
            InlineKeyboardButton("◀️", callback_data=f"{callback_prefix}:prev:{prev_month.strftime('%Y-%m')}"),
            InlineKeyboardButton(target_month.strftime("%B %Y"), callback_data=f"{callback_prefix}:noop:month"),
            InlineKeyboardButton("▶️", callback_data=f"{callback_prefix}:next:{next_month.strftime('%Y-%m')}"),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(label, callback_data=f"{callback_prefix}:noop:{label}")
            for label in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        ]
    )

    for week in cal.monthdatescalendar(target_month.year, target_month.month):
        buttons: list[InlineKeyboardButton] = []
        for day in week:
            if day.month != target_month.month:
                buttons.append(InlineKeyboardButton(" ", callback_data=f"{callback_prefix}:noop:pad"))
                continue

            is_disabled = min_date is not None and day < min_date
            label = f"·{day.day}" if is_disabled else str(day.day)
            if selected_date == day:
                label = f"[{day.day}]"

            callback = f"{callback_prefix}:noop:disabled" if is_disabled else f"{callback_prefix}:day:{day.isoformat()}"
            buttons.append(InlineKeyboardButton(label, callback_data=callback))
        rows.append(buttons)

    return InlineKeyboardMarkup(rows)
