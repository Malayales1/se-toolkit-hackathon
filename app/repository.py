from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from .db import session_scope
from .models import Task, TaskTimerSession, User


class UserRepo:
    @staticmethod
    def get_or_create(
        telegram_id: int,
        first_name: str | None,
        username: str | None,
        timezone: str = "Europe/Moscow",
    ) -> User:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(
                    telegram_id=telegram_id,
                    first_name=first_name,
                    username=username,
                    timezone=timezone,
                )
                session.add(user)
                session.flush()
                session.refresh(user)
                return user

            user.first_name = first_name
            user.username = username
            user.timezone = timezone
            session.add(user)
            session.flush()
            session.refresh(user)
            return user

    @staticmethod
    def media_settings(telegram_id: int) -> dict[str, bool]:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return {"media_enabled": True, "wowles_enabled": False}
            return {
                "media_enabled": bool(user.media_enabled),
                "wowles_enabled": bool(user.wowles_enabled),
            }

    @staticmethod
    def set_reminders(telegram_id: int, enabled: bool) -> bool:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return False
            user.reminders_enabled = enabled
            session.add(user)
            return True

    @staticmethod
    def set_media_enabled(telegram_id: int, enabled: bool) -> bool:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return False
            user.media_enabled = enabled
            session.add(user)
            return True

    @staticmethod
    def unlock_wowles(telegram_id: int) -> bool:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return False
            user.wowles_enabled = True
            session.add(user)
            return True

    @staticmethod
    def all_users_with_reminders() -> list[User]:
        with session_scope() as session:
            return list(session.scalars(select(User).where(User.reminders_enabled.is_(True))).all())


class TaskRepo:
    @staticmethod
    def add_task(telegram_id: int, text: str, due_date: date, priority: str) -> Task:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                raise ValueError("User not found")

            last_sort_order = session.scalar(
                select(Task.sort_order)
                .where(Task.user_id == user.id, Task.due_date == due_date)
                .order_by(Task.sort_order.desc())
                .limit(1)
            )
            task = Task(
                user_id=user.id,
                text=text.strip(),
                due_date=due_date,
                priority=priority,
                sort_order=(last_sort_order or 0) + 1,
            )
            session.add(task)
            session.flush()
            session.refresh(task)
            return task

    @staticmethod
    def list_tasks_for_day(telegram_id: int, due_date: date) -> list[Task]:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return []
            return list(
                session.scalars(
                    select(Task)
                    .where(Task.user_id == user.id, Task.due_date == due_date)
                    .order_by(Task.completed.asc(), Task.sort_order.asc(), Task.id.asc())
                ).all()
            )

    @staticmethod
    def list_open_tasks_for_day(telegram_id: int, due_date: date) -> list[Task]:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return []
            return list(
                session.scalars(
                    select(Task)
                    .where(
                        Task.user_id == user.id,
                        Task.due_date == due_date,
                        Task.completed.is_(False),
                    )
                    .order_by(Task.priority.asc(), Task.sort_order.asc(), Task.id.asc())
                ).all()
            )

    @staticmethod
    def toggle_task(telegram_id: int, task_id: int) -> Task | None:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return None
            task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
            if task is None:
                return None
            TaskRepo._pause_active_session_for_user(session, user.id, task.id)
            task.completed = not task.completed
            task.completed_at = datetime.utcnow() if task.completed else None
            session.add(task)
            session.flush()
            session.refresh(task)
            return task

    @staticmethod
    def postpone_task_to_tomorrow(telegram_id: int, task_id: int) -> Task | None:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return None
            task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
            if task is None:
                return None
            task.due_date = task.due_date + timedelta(days=1)
            session.add(task)
            session.flush()
            session.refresh(task)
            return task

    @staticmethod
    def delete_task(telegram_id: int, task_id: int) -> bool:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return False
            task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
            if task is None:
                return False
            TaskRepo._pause_active_session_for_user(session, user.id, task.id)
            session.delete(task)
            session.flush()
            return True

    @staticmethod
    def productivity_stats(telegram_id: int, target_day: date) -> dict[str, int]:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return {
                    "all_total": 0,
                    "all_completed": 0,
                    "day_total": 0,
                    "day_completed": 0,
                    "all_time_seconds": 0,
                    "day_time_seconds": 0,
                }

            all_total = session.scalar(
                select(func.count(Task.id)).where(Task.user_id == user.id)
            ) or 0
            all_completed = session.scalar(
                select(func.count(Task.id)).where(Task.user_id == user.id, Task.completed.is_(True))
            ) or 0
            day_total = session.scalar(
                select(func.count(Task.id)).where(Task.user_id == user.id, Task.due_date == target_day)
            ) or 0
            day_completed = session.scalar(
                select(func.count(Task.id)).where(
                    Task.user_id == user.id,
                    Task.due_date == target_day,
                    Task.completed.is_(True),
                )
            ) or 0
            all_time_seconds = TaskRepo._total_logged_seconds_for_user(session, user.id)
            day_time_seconds = TaskRepo._total_logged_seconds_for_day(session, user.id, target_day)
            return {
                "all_total": int(all_total),
                "all_completed": int(all_completed),
                "day_total": int(day_total),
                "day_completed": int(day_completed),
                "all_time_seconds": int(all_time_seconds),
                "day_time_seconds": int(day_time_seconds),
            }

    @staticmethod
    def timer_snapshot_for_task(telegram_id: int, task_id: int) -> dict[str, int | bool] | None:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return None
            task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
            if task is None:
                return None
            total_seconds = TaskRepo._total_logged_seconds_for_task(session, user.id, task.id)
            active_session = session.scalar(
                select(TaskTimerSession)
                .where(
                    TaskTimerSession.user_id == user.id,
                    TaskTimerSession.task_id == task.id,
                    TaskTimerSession.ended_at.is_(None),
                )
                .order_by(TaskTimerSession.started_at.desc())
                .limit(1)
            )
            return {
                "task_id": task.id,
                "running": active_session is not None,
                "seconds": int(total_seconds),
            }

    @staticmethod
    def start_timer(telegram_id: int, task_id: int) -> Task | None:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return None
            task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
            if task is None:
                return None
            TaskRepo._pause_active_session_for_user(session, user.id)
            active_session = session.scalar(
                select(TaskTimerSession)
                .where(
                    TaskTimerSession.user_id == user.id,
                    TaskTimerSession.task_id == task.id,
                    TaskTimerSession.ended_at.is_(None),
                )
                .limit(1)
            )
            if active_session is None:
                session.add(TaskTimerSession(user_id=user.id, task_id=task.id))
            session.flush()
            session.refresh(task)
            return task

    @staticmethod
    def pause_timer(telegram_id: int, task_id: int) -> Task | None:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return None
            task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
            if task is None:
                return None
            TaskRepo._pause_active_session_for_user(session, user.id, task.id)
            session.flush()
            session.refresh(task)
            return task

    @staticmethod
    def list_tasks_for_timer_dashboard(telegram_id: int, target_day: date) -> list[dict]:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return []
            tasks = list(
                session.scalars(
                    select(Task)
                    .where(Task.user_id == user.id, Task.due_date == target_day)
                    .order_by(Task.completed.asc(), Task.sort_order.asc(), Task.id.asc())
                ).all()
            )
            result = []
            for task in tasks:
                active_session = session.scalar(
                    select(TaskTimerSession)
                    .where(
                        TaskTimerSession.user_id == user.id,
                        TaskTimerSession.task_id == task.id,
                        TaskTimerSession.ended_at.is_(None),
                    )
                    .limit(1)
                )
                result.append(
                    {
                        "task": task,
                        "running": active_session is not None,
                        "seconds": int(TaskRepo._total_logged_seconds_for_task(session, user.id, task.id)),
                    }
                )
            return result

    @staticmethod
    def clear_all_tasks(telegram_id: int) -> int:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return 0
            tasks = list(
                session.scalars(
                    select(Task).where(Task.user_id == user.id)
                ).all()
            )
            deleted_count = len(tasks)
            for task in tasks:
                session.delete(task)
            session.flush()
            return deleted_count

    @staticmethod
    def _pause_active_session_for_user(session, user_id: int, only_task_id: int | None = None) -> None:
        active_sessions = list(
            session.scalars(
                select(TaskTimerSession).where(
                    TaskTimerSession.user_id == user_id,
                    TaskTimerSession.ended_at.is_(None),
                )
            ).all()
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for timer_session in active_sessions:
            if only_task_id is not None and timer_session.task_id != only_task_id:
                pass
            elapsed = max(0, int((now - timer_session.started_at).total_seconds()))
            timer_session.ended_at = now
            timer_session.duration_seconds = elapsed
            session.add(timer_session)

    @staticmethod
    def _total_logged_seconds_for_task(session, user_id: int, task_id: int) -> int:
        closed_total = session.scalar(
            select(func.coalesce(func.sum(TaskTimerSession.duration_seconds), 0)).where(
                TaskTimerSession.user_id == user_id,
                TaskTimerSession.task_id == task_id,
            )
        ) or 0
        active_session = session.scalar(
            select(TaskTimerSession).where(
                TaskTimerSession.user_id == user_id,
                TaskTimerSession.task_id == task_id,
                TaskTimerSession.ended_at.is_(None),
            )
        )
        if active_session is None:
            return int(closed_total)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return int(closed_total) + max(0, int((now - active_session.started_at).total_seconds()))

    @staticmethod
    def _total_logged_seconds_for_user(session, user_id: int) -> int:
        closed_total = session.scalar(
            select(func.coalesce(func.sum(TaskTimerSession.duration_seconds), 0)).where(
                TaskTimerSession.user_id == user_id,
            )
        ) or 0
        active_sessions = list(
            session.scalars(
                select(TaskTimerSession).where(
                    TaskTimerSession.user_id == user_id,
                    TaskTimerSession.ended_at.is_(None),
                )
            ).all()
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        active_total = sum(max(0, int((now - item.started_at).total_seconds())) for item in active_sessions)
        return int(closed_total) + active_total

    @staticmethod
    def _total_logged_seconds_for_day(session, user_id: int, target_day: date) -> int:
        day_start = datetime.combine(target_day, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        sessions = list(
            session.scalars(
                select(TaskTimerSession).where(
                    TaskTimerSession.user_id == user_id,
                    TaskTimerSession.started_at < day_end,
                )
            ).all()
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        total = 0
        for item in sessions:
            session_end = item.ended_at or now
            overlap_start = max(item.started_at, day_start)
            overlap_end = min(session_end, day_end)
            if overlap_end > overlap_start:
                total += int((overlap_end - overlap_start).total_seconds())
        return total
