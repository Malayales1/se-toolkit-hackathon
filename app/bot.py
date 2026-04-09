from __future__ import annotations

import logging
import os
import random
from io import BytesIO
from pathlib import Path
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .calendar_ui import build_month_calendar
from .db import engine, initialize_runtime_schema
from .models import Base
from .repository import TaskRepo, UserRepo

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Moscow"))
UNFINISHED_PLAN_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "unfinished-plan.jpg"
MID_PROGRESS_PLAN_VIDEO = Path(__file__).resolve().parent.parent / "assets" / "mid-plan.mp4"
HIGH_MID_PROGRESS_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "high-mid-plan.jpg"
WOWLES_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "wowles.jpg"
CELEBRATION_MEDIA = [
    ("video", Path(__file__).resolve().parent.parent / "assets" / "celebrate-1.mp4"),
    ("video", Path(__file__).resolve().parent.parent / "assets" / "celebrate-2.mp4"),
    ("video", Path(__file__).resolve().parent.parent / "assets" / "celebrate-3.mp4"),
    ("video", Path(__file__).resolve().parent.parent / "assets" / "celebrate-4.mp4"),
    ("photo", Path(__file__).resolve().parent.parent / "assets" / "celebrate-6.jpg"),
    ("photo", Path(__file__).resolve().parent.parent / "assets" / "celebrate-7.jpg"),
    ("photo", Path(__file__).resolve().parent.parent / "assets" / "celebrate-8.jpg"),
]
RESET_MEDIA = [
    Path(__file__).resolve().parent.parent / "assets" / "reset-1.mp4",
    Path(__file__).resolve().parent.parent / "assets" / "reset-2.mp4",
    Path(__file__).resolve().parent.parent / "assets" / "reset-3.mp4",
    Path(__file__).resolve().parent.parent / "assets" / "reset-4.mp4",
    Path(__file__).resolve().parent.parent / "assets" / "reset-5.mp4",
    Path(__file__).resolve().parent.parent / "assets" / "reset-6.mp4",
]

ASK_TASK_TEXT, PICK_DATE, PICK_PRIORITY = range(3)

BUTTON_ADD = "➕ Add Task"
BUTTON_TODAY = "📅 Today"
BUTTON_TOMORROW = "🌤 Tomorrow"
BUTTON_WEEK = "🗓 Week"
BUTTON_CALENDAR = "📆 Calendar"
BUTTON_STATS = "📊 Productivity"
BUTTON_TIMERS = "⏱ Timers"
BUTTON_REMINDERS_ON = "🔔 Reminders On"
BUTTON_REMINDERS_OFF = "🔕 Reminders Off"
BUTTON_MEDIA_ON = "🎞 Media On"
BUTTON_MEDIA_OFF = "🙈 Media Off"
BUTTON_RESET = "репродуктивация"


def now_local() -> datetime:
    return datetime.now(DEFAULT_TIMEZONE)


def today_local_date() -> date:
    return now_local().date()


def strike(text: str) -> str:
    return "".join(ch + "\u0336" for ch in text)


def priority_label(priority: str) -> str:
    mapping = {
        "high": "🔴 СРОЧНО",
        "medium": "🟣 НАДО",
        "low": "🟢 ХЗ",
    }
    return mapping.get(priority, priority.title())


def main_menu_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_ADD, BUTTON_TODAY],
            [BUTTON_TOMORROW, BUTTON_WEEK],
            [BUTTON_CALENDAR, BUTTON_STATS],
            [BUTTON_TIMERS],
            [BUTTON_REMINDERS_ON, BUTTON_REMINDERS_OFF],
            [BUTTON_MEDIA_ON, BUTTON_MEDIA_OFF],
            [BUTTON_RESET],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def format_duration(total_seconds: int) -> str:
    hours, rem = divmod(max(total_seconds, 0), 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def task_checkbox(task_done: bool) -> str:
    return "💋" if task_done else "-"


def format_task_line(task, *, spent_seconds: int | None = None, running: bool = False) -> str:
    title = escape(task.text)
    if task.completed:
        title = strike(title)
    line = f"{task_checkbox(task.completed)} {title}  <i>{priority_label(task.priority)}</i>"
    if spent_seconds is not None:
        timer_icon = "⏸" if running else "⏱"
        line += f"  {timer_icon} <code>{format_duration(spent_seconds)}</code>"
    return line


def build_task_keyboard(tasks, view: str, anchor: date, *, timer_map: dict[int, dict] | None = None) -> InlineKeyboardMarkup | None:
    if not tasks:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        timer_state = (timer_map or {}).get(task.id, {})
        timer_running = bool(timer_state.get("running", False))
        timer_action = "pause" if timer_running else "start"
        timer_label = "⏸ Pause" if timer_running else "▶️ Start"
        if task.completed:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"↩️ Undo #{task.id}",
                        callback_data=f"task:toggle:{task.id}:{view}:{anchor.isoformat()}",
                    ),
                    InlineKeyboardButton(
                        timer_label,
                        callback_data=f"timer:{timer_action}:{task.id}:{view}:{anchor.isoformat()}",
                    ),
                    InlineKeyboardButton(
                        "🗑 Delete",
                        callback_data=f"task:delete:{task.id}:{view}:{anchor.isoformat()}",
                    ),
                ]
            )
            continue

        rows.append(
            [
                InlineKeyboardButton(
                    f"✅ Done #{task.id}",
                    callback_data=f"task:toggle:{task.id}:{view}:{anchor.isoformat()}",
                ),
                InlineKeyboardButton(
                    "➡️ Tomorrow",
                    callback_data=f"task:postpone:{task.id}:{view}:{anchor.isoformat()}",
                ),
                InlineKeyboardButton(
                    timer_label,
                    callback_data=f"timer:{timer_action}:{task.id}:{view}:{anchor.isoformat()}",
                ),
                InlineKeyboardButton(
                    "🗑 Delete",
                    callback_data=f"task:delete:{task.id}:{view}:{anchor.isoformat()}",
                ),
            ]
        )
    return InlineKeyboardMarkup(rows)


def render_day_view(tasks, target_date: date, *, timer_map: dict[int, dict] | None = None, day_total_seconds: int | None = None) -> str:
    title = f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n"
    if not tasks:
        footer = ""
        if day_total_seconds is not None:
            footer = f"\n\n⏱ Потрачено за день: <b>{format_duration(day_total_seconds)}</b>"
        return title + "\nНет задач на эту дату." + footer
    body = "\n".join(
        f"{idx}. {format_task_line(task, spent_seconds=(timer_map or {}).get(task.id, {}).get('seconds'), running=bool((timer_map or {}).get(task.id, {}).get('running', False)))}"
        for idx, task in enumerate(tasks, start=1)
    )
    footer = ""
    if day_total_seconds is not None:
        footer = f"\n\n⏱ Потрачено за день: <b>{format_duration(day_total_seconds)}</b>"
    return title + "\n" + body + footer


def render_week_view(days: list[tuple[date, list]]) -> str:
    lines = ["🗓 <b>Tasks for the next 7 days</b>"]
    for day, tasks in days:
        lines.append("")
        lines.append(f"<b>{day.strftime('%a, %d.%m')}</b>")
        if not tasks:
            lines.append("• Нет задач")
            continue
        for task in tasks:
            lines.append(f"• {format_task_line(task)}")
    return "\n".join(lines)


def render_reminder_view(tasks, target_date: date, *, timer_map: dict[int, dict] | None = None, day_total_seconds: int | None = None) -> str:
    lines = [
        f"⏰ <b>Напоминание на {target_date.strftime('%d.%m.%Y')}</b>",
        "",
        "Вот задачи на сегодня. Если что-то не успеваете, можно сразу перенести на завтра:",
        "",
    ]
    for task in tasks:
        timer_state = (timer_map or {}).get(task.id, {})
        lines.append(
            f"• {format_task_line(task, spent_seconds=timer_state.get('seconds'), running=bool(timer_state.get('running', False)))}"
        )
    if day_total_seconds is not None:
        lines.extend(["", f"⏱ Сегодня уже потрачено: <b>{format_duration(day_total_seconds)}</b>"])
    return "\n".join(lines)


def build_timer_map_for_tasks(telegram_id: int, tasks) -> tuple[dict[int, dict], int]:
    timer_map: dict[int, dict] = {}
    for task in tasks:
        snapshot = TaskRepo.timer_snapshot_for_task(telegram_id, task.id)
        if snapshot is None:
            continue
        timer_map[task.id] = snapshot
    stats = TaskRepo.productivity_stats(telegram_id, today_local_date())
    return timer_map, stats["day_time_seconds"]


def build_stats_chart(stats: dict[str, int]) -> BytesIO:
    all_completed = stats["all_completed"]
    all_total = max(stats["all_total"], 1)
    day_completed = stats["day_completed"]
    day_total = max(stats["day_total"], 1)

    fig, axes = plt.subplots(1, 2, figsize=(8, 4), dpi=180)
    fig.patch.set_facecolor("white")
    chart_specs = [
        (
            axes[0],
            all_completed,
            all_total - all_completed,
            "All Time",
            f"{all_completed}/{stats['all_total']}",
        ),
        (
            axes[1],
            day_completed,
            day_total - day_completed,
            "Today",
            f"{day_completed}/{stats['day_total']}",
        ),
    ]
    colors = ["#2D8CFF", "#E6EEF8"]
    for axis, completed, pending, title, ratio in chart_specs:
        values = [max(completed, 0), max(pending, 0)]
        axis.pie(
            values,
            startangle=90,
            counterclock=False,
            colors=colors,
            wedgeprops={"width": 0.32, "edgecolor": "white"},
        )
        axis.text(0, 0.05, ratio, ha="center", va="center", fontsize=14, fontweight="bold")
        axis.text(0, -0.18, title, ha="center", va="center", fontsize=10)
        axis.set(aspect="equal")
    fig.suptitle("Productivity", fontsize=16, fontweight="bold")
    buffer = BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    buffer.name = "productivity.png"
    return buffer


async def ensure_user(update: Update) -> None:
    user = update.effective_user
    if user is None:
        return
    UserRepo.get_or_create(
        telegram_id=user.id,
        first_name=user.first_name,
        username=user.username,
        timezone=str(DEFAULT_TIMEZONE),
    )


async def reply_or_edit(
    update: Update,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if update.callback_query is not None:
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
        return
    if update.message is not None:
        await update.message.reply_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )


async def edit_task_message(
    query,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    message = query.message
    if message is not None and (
        message.photo or message.video or message.animation or message.document
    ):
        await query.edit_message_caption(
            caption=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
        return
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )


def add_calendar_markup(target_month: date) -> InlineKeyboardMarkup:
    return build_month_calendar(
        target_month=target_month,
        callback_prefix="addcal",
        selected_date=None,
        min_date=today_local_date(),
    )


def browse_calendar_markup(target_month: date, selected_date: date | None = None) -> InlineKeyboardMarkup:
    return build_month_calendar(
        target_month=target_month,
        callback_prefix="viewcal",
        selected_date=selected_date,
        min_date=None,
    )


def reminder_media_payload(tasks, completion_ratio: float, *, media_enabled: bool, wowles_enabled: bool) -> tuple[str, Path | None, str | None]:
    if not media_enabled:
        return ("text", None, None)

    all_tasks_done = bool(tasks) and all(task.completed for task in tasks)
    important_tasks = [task for task in tasks if task.priority in {"high", "medium"}]
    all_important_done = bool(important_tasks) and all(task.completed for task in important_tasks)

    if all_tasks_done or all_important_done:
        available_media = [(kind, path) for kind, path in CELEBRATION_MEDIA if path.exists()]
        if wowles_enabled and WOWLES_IMAGE.exists():
            available_media.append(("photo", WOWLES_IMAGE))
        if available_media:
            media_kind, media_path = random.choice(available_media)
            return (
                media_kind,
                media_path,
                "<blockquote>Ты все сделал/а? Понятно. Ты устал/а? Понятно.</blockquote>",
            )

    if completion_ratio < 0.25 and UNFINISHED_PLAN_IMAGE.exists():
        return (
            "photo",
            UNFINISHED_PLAN_IMAGE,
            "<blockquote>пупупу, канава IS CALLING ZAYA</blockquote>",
        )
    if 0.25 <= completion_ratio < 0.5 and MID_PROGRESS_PLAN_VIDEO.exists():
        return (
            "video",
            MID_PROGRESS_PLAN_VIDEO,
            "<blockquote>ухуху, старайся больше, ты помереть в нищете хочешь? ЛОЛ, ОРУ</blockquote>",
        )
    return ("text", None, None)


def priority_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔴 СРОЧНО", callback_data="priority:high"),
                InlineKeyboardButton("🟣 НАДО", callback_data="priority:medium"),
                InlineKeyboardButton("🟢 ХЗ", callback_data="priority:low"),
            ]
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    text = (
        "Привет! Я planner bot.\n\n"
        "Основное управление теперь через кнопки под полем ввода.\n"
        "Можно добавлять задачи, смотреть план, переносить незавершённые дела и следить за продуктивностью."
    )
    await update.message.reply_text(text, reply_markup=main_menu_markup())


async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await ensure_user(update)
    context.user_data["new_task"] = {}
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target is not None:
        await target.reply_text("Напиши текст задачи одним сообщением.", reply_markup=main_menu_markup())
    return ASK_TASK_TEXT


async def add_another_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    return await add_task_start(update, context)


async def add_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Пустую задачу сохранить нельзя. Напиши нормальный текст.")
        return ASK_TASK_TEXT

    context.user_data["new_task"] = {"text": text}
    month = today_local_date().replace(day=1)
    await update.message.reply_text(
        "Теперь выбери дату задачи:",
        reply_markup=add_calendar_markup(month),
    )
    return PICK_DATE


async def add_task_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, action, raw_value = (query.data or "").split(":", 2)

    if action in {"prev", "next"}:
        month = date.fromisoformat(f"{raw_value}-01")
        await query.edit_message_reply_markup(reply_markup=add_calendar_markup(month))
        return PICK_DATE

    if action == "noop":
        return PICK_DATE

    target_date = date.fromisoformat(raw_value)
    context.user_data.setdefault("new_task", {})["due_date"] = target_date.isoformat()
    await query.edit_message_text(
        text=f"Дата выбрана: <b>{target_date.strftime('%d.%m.%Y')}</b>\nТеперь выбери приоритет:",
        parse_mode=ParseMode.HTML,
        reply_markup=priority_markup(),
    )
    return PICK_PRIORITY


async def add_task_priority(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, priority = (query.data or "priority:medium").split(":", 1)

    draft = context.user_data.get("new_task", {})
    task_text = str(draft.get("text", "")).strip()
    due_date = date.fromisoformat(str(draft.get("due_date", today_local_date().isoformat())))

    task = TaskRepo.add_task(
        telegram_id=update.effective_user.id,
        text=task_text,
        due_date=due_date,
        priority=priority,
    )
    context.user_data.pop("new_task", None)
    await query.edit_message_text(
        text=(
            "Задача сохранена:\n"
            f"• <b>{escape(task.text)}</b>\n"
            f"• Дата: <b>{due_date.strftime('%d.%m.%Y')}</b>\n"
            f"• Приоритет: <b>{priority_label(task.priority)}</b>"
        ),
        parse_mode=ParseMode.HTML,
    )
    if query.message is not None:
        await query.message.reply_text(
            "У вас ещё есть задачи?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("➕ Да, ещё одну", callback_data="addmore:yes"),
                        InlineKeyboardButton("📋 Нет, к списку", callback_data=f"addmore:no:{due_date.isoformat()}"),
                    ]
                ]
            ),
        )
    return ConversationHandler.END


async def add_more_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, _, raw_date = (query.data or "").split(":", 2)
    await query.edit_message_reply_markup(reply_markup=None)
    await send_day_plan(update, date.fromisoformat(raw_date))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_task", None)
    if update.message:
        await update.message.reply_text("Ок, отменено.")
    elif update.callback_query:
        await update.callback_query.answer("Отменено")
    return ConversationHandler.END


async def send_day_plan(update: Update, target_date: date) -> None:
    tasks = TaskRepo.list_tasks_for_day(update.effective_user.id, target_date)
    timer_map, day_total_seconds = build_timer_map_for_tasks(update.effective_user.id, tasks)
    text = render_day_view(tasks, target_date, timer_map=timer_map, day_total_seconds=day_total_seconds)
    reply_markup = build_task_keyboard(tasks, "day", target_date, timer_map=timer_map)
    stats = TaskRepo.productivity_stats(update.effective_user.id, target_date)
    completion_ratio = (
        stats["day_completed"] / stats["day_total"]
        if stats["day_total"] > 0
        else 1.0
    )
    settings = UserRepo.media_settings(update.effective_user.id)
    media_kind, media_path, media_prefix = reminder_media_payload(
        tasks,
        completion_ratio,
        media_enabled=settings["media_enabled"],
        wowles_enabled=settings["wowles_enabled"],
    )

    if target_date == today_local_date() and update.message is not None and media_path is not None:
        caption = f"{media_prefix}\n\n{text}" if media_prefix else text
        with media_path.open("rb") as media_file:
            if media_kind == "photo":
                await update.message.reply_photo(
                    photo=media_file,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                )
            elif media_kind == "video":
                await update.message.reply_video(
                    video=media_file,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True,
                )
        return

    await reply_or_edit(
        update,
        text,
        reply_markup=reply_markup,
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    await send_day_plan(update, today_local_date())


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    await send_day_plan(update, today_local_date() + timedelta(days=1))


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    start_day = today_local_date()
    days = [
        (start_day + timedelta(days=offset), TaskRepo.list_tasks_for_day(update.effective_user.id, start_day + timedelta(days=offset)))
        for offset in range(7)
    ]
    await update.message.reply_text(
        render_week_view(days),
        parse_mode=ParseMode.HTML,
    )


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    month = today_local_date().replace(day=1)
    await update.message.reply_text(
        "Выбери дату, чтобы посмотреть задачи:",
        reply_markup=browse_calendar_markup(month),
    )


async def calendar_browser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, raw_value = (query.data or "").split(":", 2)

    if action in {"prev", "next"}:
        month = date.fromisoformat(f"{raw_value}-01")
        await query.edit_message_reply_markup(reply_markup=browse_calendar_markup(month))
        return
    if action == "noop":
        return

    selected = date.fromisoformat(raw_value)
    tasks = TaskRepo.list_tasks_for_day(update.effective_user.id, selected)
    keyboard_rows = []
    base_markup = build_task_keyboard(tasks, "day", selected)
    if base_markup is not None:
        keyboard_rows.extend(base_markup.inline_keyboard)
    keyboard_rows.append(
        [InlineKeyboardButton("📆 Open calendar", callback_data=f"viewcal:open:{selected.replace(day=1).isoformat()}")]
    )
    await query.edit_message_text(
        text=render_day_view(tasks, selected),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
        parse_mode=ParseMode.HTML,
    )


async def calendar_reopen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, _, raw_value = (query.data or "").split(":", 2)
    month = date.fromisoformat(raw_value)
    await query.edit_message_text(
        text="Выбери дату, чтобы посмотреть задачи:",
        reply_markup=browse_calendar_markup(month),
    )


async def reminders_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    UserRepo.set_reminders(update.effective_user.id, True)
    await update.message.reply_text("Почасовые напоминания включены.", reply_markup=main_menu_markup())


async def reminders_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    UserRepo.set_reminders(update.effective_user.id, False)
    await update.message.reply_text("Почасовые напоминания выключены.", reply_markup=main_menu_markup())


async def media_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    UserRepo.set_media_enabled(update.effective_user.id, True)
    await update.message.reply_text("Фотки и видео снова включены.", reply_markup=main_menu_markup())


async def media_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    UserRepo.set_media_enabled(update.effective_user.id, False)
    await update.message.reply_text("Фотки и видео отключены. Будет только текст.", reply_markup=main_menu_markup())


async def wowles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    UserRepo.unlock_wowles(update.effective_user.id)
    settings = UserRepo.media_settings(update.effective_user.id)
    if not settings["media_enabled"]:
        await update.message.reply_text(
            "wowles unlocked. Сейчас media off, так что без фотки и без цитаты.",
            reply_markup=main_menu_markup(),
        )
        return
    if WOWLES_IMAGE.exists():
        with WOWLES_IMAGE.open("rb") as image_file:
            await update.message.reply_photo(
                photo=image_file,
                caption="<blockquote>wowles unlocked</blockquote>\n\nТеперь именно эта фотка участвует в праздничном рандоме после /wowles.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_markup(),
            )
        return
    await update.message.reply_text(
        "wowles unlocked. Теперь именно эта фотка участвует в праздничном рандоме после /wowles.",
        reply_markup=main_menu_markup(),
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    stats = TaskRepo.productivity_stats(update.effective_user.id, today_local_date())
    buffer = build_stats_chart(stats)
    caption = (
        "📊 <b>Productivity summary</b>\n"
        f"За всё время: <b>{stats['all_completed']}/{stats['all_total']}</b>\n"
        f"За сегодня: <b>{stats['day_completed']}/{stats['day_total']}</b>\n"
        f"⏱ За всё время: <b>{format_duration(stats['all_time_seconds'])}</b>\n"
        f"⏱ За сегодня: <b>{format_duration(stats['day_time_seconds'])}</b>"
    )
    await update.message.reply_photo(photo=buffer, caption=caption, parse_mode=ParseMode.HTML)


async def timers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    target_day = today_local_date()
    tasks = TaskRepo.list_tasks_for_day(update.effective_user.id, target_day)
    timer_map, day_total_seconds = build_timer_map_for_tasks(update.effective_user.id, tasks)
    await update.message.reply_text(
        render_day_view(tasks, target_day, timer_map=timer_map, day_total_seconds=day_total_seconds),
        reply_markup=build_task_keyboard(tasks, "day", target_day, timer_map=timer_map),
        parse_mode=ParseMode.HTML,
    )


async def menu_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await add_task_start(update, context)


async def menu_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await today(update, context)


async def menu_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await tomorrow(update, context)


async def menu_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await week(update, context)


async def menu_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await calendar_command(update, context)


async def menu_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await stats_command(update, context)


async def menu_timers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await timers_command(update, context)


async def menu_reminders_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reminders_on(update, context)


async def menu_reminders_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reminders_off(update, context)


async def reset_all_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    deleted_count = TaskRepo.clear_all_tasks(update.effective_user.id)
    available_media = [path for path in RESET_MEDIA if path.exists()]
    caption = f"<blockquote>мда, мда мда мда, МДАААА</blockquote>\n\nУдалено задач: {deleted_count}."
    if update.message is not None and available_media:
        reset_media = random.choice(available_media)
        with reset_media.open("rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                supports_streaming=True,
                reply_markup=main_menu_markup(),
            )
        return
    if update.message is not None:
        await update.message.reply_text(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_markup(),
        )


async def on_task_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, action, raw_task_id, view, raw_anchor = (query.data or "").split(":", 4)
    if action == "toggle":
        task = TaskRepo.toggle_task(update.effective_user.id, int(raw_task_id))
        if task is None:
            await query.answer("Задача не найдена", show_alert=True)
            return
        await query.answer("Статус обновлён")
    elif action == "postpone":
        task = TaskRepo.postpone_task_to_tomorrow(update.effective_user.id, int(raw_task_id))
        if task is None:
            await query.answer("Задача не найдена", show_alert=True)
            return
        await query.answer("Перенесено на завтра")
    elif action == "delete":
        deleted = TaskRepo.delete_task(update.effective_user.id, int(raw_task_id))
        if not deleted:
            await query.answer("Задача не найдена", show_alert=True)
            return
        await query.answer("Задача удалена")
    else:
        return

    anchor = date.fromisoformat(raw_anchor)
    tasks = TaskRepo.list_tasks_for_day(update.effective_user.id, anchor)
    timer_map, day_total_seconds = build_timer_map_for_tasks(update.effective_user.id, tasks)
    text = (
        render_day_view(tasks, anchor, timer_map=timer_map, day_total_seconds=day_total_seconds)
        if view == "day"
        else render_reminder_view(tasks, anchor, timer_map=timer_map, day_total_seconds=day_total_seconds)
    )
    await edit_task_message(
        query,
        text,
        reply_markup=build_task_keyboard(tasks, view, anchor, timer_map=timer_map),
    )


async def on_timer_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, raw_task_id, view, raw_anchor = (query.data or "").split(":", 4)
    task_id = int(raw_task_id)

    if action == "start":
        task = TaskRepo.start_timer(update.effective_user.id, task_id)
        if task is None:
            await query.answer("Задача не найдена", show_alert=True)
            return
        await query.answer("Таймер запущен")
    elif action == "pause":
        task = TaskRepo.pause_timer(update.effective_user.id, task_id)
        if task is None:
            await query.answer("Задача не найдена", show_alert=True)
            return
        await query.answer("Таймер поставлен на паузу")
    else:
        return

    anchor = date.fromisoformat(raw_anchor)
    tasks = TaskRepo.list_tasks_for_day(update.effective_user.id, anchor)
    timer_map, day_total_seconds = build_timer_map_for_tasks(update.effective_user.id, tasks)
    text = (
        render_day_view(tasks, anchor, timer_map=timer_map, day_total_seconds=day_total_seconds)
        if view == "day"
        else render_reminder_view(tasks, anchor, timer_map=timer_map, day_total_seconds=day_total_seconds)
    )
    await edit_task_message(
        query,
        text,
        reply_markup=build_task_keyboard(tasks, view, anchor, timer_map=timer_map),
    )


async def hourly_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    current_day = today_local_date()
    for user in UserRepo.all_users_with_reminders():
        all_tasks = TaskRepo.list_tasks_for_day(user.telegram_id, current_day)
        open_tasks = [task for task in all_tasks if not task.completed]
        if not open_tasks:
            continue
        try:
            timer_map, day_total_seconds = build_timer_map_for_tasks(user.telegram_id, open_tasks)
            stats = TaskRepo.productivity_stats(user.telegram_id, current_day)
            completion_ratio = (
                stats["day_completed"] / stats["day_total"]
                if stats["day_total"] > 0
                else 1.0
            )
            media_kind, media_path, media_prefix = reminder_media_payload(
                all_tasks,
                completion_ratio,
                media_enabled=bool(user.media_enabled),
                wowles_enabled=bool(user.wowles_enabled),
            )
            reminder_text = render_reminder_view(
                open_tasks,
                current_day,
                timer_map=timer_map,
                day_total_seconds=day_total_seconds,
            )
            reminder_markup = build_task_keyboard(
                open_tasks,
                "reminder",
                current_day,
                timer_map=timer_map,
            )
            if media_path is not None:
                reminder_text = f"{media_prefix}\n\n{reminder_text}" if media_prefix else reminder_text
                with media_path.open("rb") as media_file:
                    if media_kind == "photo":
                        await context.bot.send_photo(
                            chat_id=user.telegram_id,
                            photo=media_file,
                            caption=reminder_text,
                            reply_markup=reminder_markup,
                            parse_mode=ParseMode.HTML,
                        )
                    elif media_kind == "video":
                        await context.bot.send_video(
                            chat_id=user.telegram_id,
                            video=media_file,
                            caption=reminder_text,
                            reply_markup=reminder_markup,
                            parse_mode=ParseMode.HTML,
                            supports_streaming=True,
                        )
            else:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=reminder_text,
                    reply_markup=reminder_markup,
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            logger.exception("Failed to send reminder to %s", user.telegram_id)


def seconds_to_next_hour() -> int:
    current = now_local()
    next_hour = (current + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return max(1, int((next_hour - current).total_seconds()))


def build_application() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    application = ApplicationBuilder().token(token).build()

    add_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_task_start),
            MessageHandler(filters.Regex(f"^{BUTTON_ADD}$"), menu_add),
            CallbackQueryHandler(add_another_task, pattern=r"^addmore:yes$"),
        ],
        states={
            ASK_TASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_text)],
            PICK_DATE: [CallbackQueryHandler(add_task_calendar, pattern=r"^addcal:(prev|next|day|noop):")],
            PICK_PRIORITY: [CallbackQueryHandler(add_task_priority, pattern=r"^priority:(high|medium|low)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("tomorrow", tomorrow))
    application.add_handler(CommandHandler("week", week))
    application.add_handler(CommandHandler("calendar", calendar_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("timers", timers_command))
    application.add_handler(CommandHandler("reminders_on", reminders_on))
    application.add_handler(CommandHandler("reminders_off", reminders_off))
    application.add_handler(CommandHandler("wowles", wowles))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_TODAY}$"), menu_today))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_TOMORROW}$"), menu_tomorrow))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_WEEK}$"), menu_week))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_CALENDAR}$"), menu_calendar))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_STATS}$"), menu_stats))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_TIMERS}$"), menu_timers))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REMINDERS_ON}$"), menu_reminders_on))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REMINDERS_OFF}$"), menu_reminders_off))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_MEDIA_ON}$"), media_on))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_MEDIA_OFF}$"), media_off))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_RESET}$"), reset_all_prompt))
    application.add_handler(add_conversation)
    application.add_handler(CallbackQueryHandler(add_more_no, pattern=r"^addmore:no:\d{4}-\d{2}-\d{2}$"))
    application.add_handler(CallbackQueryHandler(calendar_browser, pattern=r"^viewcal:(prev|next|day|noop):"))
    application.add_handler(CallbackQueryHandler(calendar_reopen, pattern=r"^viewcal:open:"))
    application.add_handler(CallbackQueryHandler(on_task_button, pattern=r"^task:(toggle|postpone|delete):\d+:(day|reminder):\d{4}-\d{2}-\d{2}$"))
    application.add_handler(CallbackQueryHandler(on_timer_button, pattern=r"^timer:(start|pause):\d+:(day|reminder):\d{4}-\d{2}-\d{2}$"))

    if application.job_queue is None:
        raise RuntimeError("python-telegram-bot job queue is unavailable")
    application.job_queue.run_repeating(
        hourly_reminder,
        interval=3600,
        first=seconds_to_next_hour(),
        name="hourly-reminders",
    )
    return application


def main() -> None:
    Base.metadata.create_all(bind=engine)
    initialize_runtime_schema()
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)
