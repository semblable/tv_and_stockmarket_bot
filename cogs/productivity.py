import logging
import re
from datetime import datetime, timedelta, timezone, time as dtime
import json
from typing import List, Optional
from functools import partial

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def _scope_guild_id_from_ctx(ctx: commands.Context) -> int:
    return ctx.guild.id if ctx.guild else 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sqlite_utc_timestamp(dt: datetime) -> str:
    # SQLite CURRENT_TIMESTAMP uses "YYYY-MM-DD HH:MM:SS" in UTC.
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_due_display(dt_utc: datetime, tz_name: Optional[str]) -> tuple[str, str]:
    """
    Formats a UTC datetime for user-facing display in the requested timezone.
    Returns (local_str, tz_label).
    """
    if not isinstance(dt_utc, datetime):
        # Defensive fallback; shouldn't happen.
        dt_utc = _utc_now()
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    dt_utc = dt_utc.astimezone(timezone.utc)

    tz = _tzinfo_from_name(tz_name)
    local_dt = dt_utc.astimezone(tz)

    name = (tz_name or "").strip()
    if name.upper() in ("UTC", "ETC/UTC", "Z"):
        tz_label = "UTC"
    elif name == "Europe/Warsaw":
        tz_label = "CET/CEST"
    elif name:
        tz_label = name
    else:
        # Default for habits: CET/CEST
        tz_label = "CET/CEST"

    return (local_dt.strftime("%Y-%m-%d %H:%M:%S"), tz_label)


def _parse_sqlite_utc_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_hhmm_utc(s: str) -> Optional[dtime]:
    if not isinstance(s, str):
        return None
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return dtime(hour=hh, minute=mm, tzinfo=timezone.utc)


def _cet_tzinfo():
    """
    Returns tzinfo for CET/CEST (Europe/Warsaw) if available.
    Falls back to a fixed UTC+1 tz if zoneinfo data isn't present.
    """
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Europe/Warsaw")
        except Exception:
            pass
    return timezone(timedelta(hours=1), name="CET")


def _tzinfo_from_name(tz_name: Optional[str]):
    """
    Best-effort tz resolver.
    - 'UTC' => timezone.utc
    - 'Europe/Warsaw' => CET/CEST (preferred)
    - fallback => CET fixed offset if zoneinfo isn't available
    """
    name = (tz_name or "").strip()
    if not name:
        return _cet_tzinfo()
    if name.upper() in ("UTC", "ETC/UTC", "Z"):
        return timezone.utc
    if name.upper() == "CET":
        return timezone(timedelta(hours=1), name="CET")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return _cet_tzinfo()


def _parse_hhmm_local(s: str) -> Optional[dtime]:
    """
    Parse HH:MM without assuming UTC; tzinfo is assigned by the caller.
    """
    if not isinstance(s, str):
        return None
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return dtime(hour=hh, minute=mm)


def _next_due_datetime_cet_to_utc(now_utc: datetime, days_of_week: List[int], due_hhmm_local: dtime, tz) -> datetime:
    """
    Compute next due datetime, interpreting due_hhmm_local in the given tz (CET/CEST),
    returning a UTC datetime suitable for SQLite comparisons.
    days_of_week: 0=Mon..6=Sun (local weekday).
    """
    now_utc = now_utc.astimezone(timezone.utc)
    now_local = now_utc.astimezone(tz)
    days = sorted(set([d for d in days_of_week if 0 <= d <= 6])) or [0, 1, 2, 3, 4]

    today_dow = now_local.weekday()
    today_due_local = datetime.combine(now_local.date(), due_hhmm_local).replace(tzinfo=tz)
    if today_dow in days and now_local < today_due_local:
        return today_due_local.astimezone(timezone.utc)

    for add_days in range(1, 8):
        d = (today_dow + add_days) % 7
        if d in days:
            target_date = (now_local + timedelta(days=add_days)).date()
            target_local = datetime.combine(target_date, due_hhmm_local).replace(tzinfo=tz)
            return target_local.astimezone(timezone.utc)

    return (now_local + timedelta(days=1)).astimezone(timezone.utc)


DAY_ALIASES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _parse_days_spec(spec: str) -> List[int]:
    """
    Accepts:
      - "mon-fri", "tue-thu"
      - "weekdays", "weekends", "daily"
      - "mon,wed,fri"
      - numbers "1-7" for Mon-Sun (e.g. "1-5", "1,3,5", "7")
      - (also accepts "0-6" for Mon-Sun, but "1-7" is recommended)
    Returns list of ints 0=Mon..6=Sun (deduped, sorted).
    """
    if not isinstance(spec, str) or not spec.strip():
        return [0, 1, 2, 3, 4]
    s = spec.strip().lower()
    s = s.replace("–", "-").replace("—", "-")
    if s in ("daily", "everyday", "all"):
        return [0, 1, 2, 3, 4, 5, 6]
    if s in ("weekdays", "mon-fri", "m-f"):
        return [0, 1, 2, 3, 4]
    if s in ("weekends", "sat-sun"):
        return [5, 6]

    parts = re.split(r"[,\s]+", s)
    days: List[int] = []
    for p in parts:
        if not p:
            continue

        # handle ranges: mon-fri
        if "-" in p:
            a, b = p.split("-", 1)
            a = a.strip()
            b = b.strip()
            start = DAY_ALIASES.get(a)
            end = DAY_ALIASES.get(b)
            if start is None or end is None:
                # maybe numeric range
                try:
                    na = int(a)
                    nb = int(b)
                except ValueError:
                    continue
                start = na
                end = nb
                # normalize numeric: allow 1..7 or 0..6
                if 1 <= start <= 7:
                    start = (start - 1) % 7
                if 1 <= end <= 7:
                    end = (end - 1) % 7
                if not (0 <= start <= 6 and 0 <= end <= 6):
                    continue

            # inclusive wrap-around range
            cur = start
            for _ in range(7):
                days.append(cur)
                if cur == end:
                    break
                cur = (cur + 1) % 7
            continue

        if p in DAY_ALIASES:
            days.append(DAY_ALIASES[p])
            continue

        # numeric day
        try:
            n = int(p)
        except ValueError:
            continue
        if 0 <= n <= 6:
            days.append(n)
        elif 1 <= n <= 7:
            days.append((n - 1) % 7)

    days = sorted(set([d for d in days if 0 <= d <= 6]))
    return days if days else [0, 1, 2, 3, 4]


def _next_due_datetime_utc(now: datetime, days_of_week: List[int], due_hhmm_utc: dtime) -> datetime:
    """
    Compute next due datetime in UTC for a weekly schedule.
    days_of_week: 0=Mon..6=Sun
    due_hhmm_utc: time with tzinfo=UTC
    """
    now = now.astimezone(timezone.utc)
    days = sorted(set([d for d in days_of_week if 0 <= d <= 6])) or [0, 1, 2, 3, 4]

    # Candidate for today
    today_dow = now.weekday()  # Mon=0..Sun=6
    today_due = datetime.combine(now.date(), due_hhmm_utc, tzinfo=timezone.utc)
    if today_dow in days and now < today_due:
        return today_due

    # Find next scheduled day
    for add_days in range(1, 8):
        d = (today_dow + add_days) % 7
        if d in days:
            target_date = (now + timedelta(days=add_days)).date()
            return datetime.combine(target_date, due_hhmm_utc, tzinfo=timezone.utc)

    # Fallback (shouldn't happen)
    return now + timedelta(days=1)


_HABIT_REMIND_PROFILES = {"catchup", "nag_gentle", "nag_normal", "nag_aggressive", "nag_daily"}
_HABIT_REMIND_PROFILE_ALIASES = {
    # Canonical
    "catchup": "catchup",
    "nag_gentle": "nag_gentle",
    "nag_normal": "nag_normal",
    "nag_aggressive": "nag_aggressive",
    "nag_daily": "nag_daily",
    # Friendly
    "catch-up": "catchup",
    "nag": "nag_normal",
    "nudge": "nag_normal",
    # Back-compat (old naming)
    "digest": "catchup",
    "summary": "catchup",
    "normal": "nag_normal",
    "gentle": "nag_gentle",
    "aggressive": "nag_aggressive",
    "quiet": "nag_daily",
}


def _normalize_habit_remind_profile(profile: Optional[str]) -> str:
    p = str(profile or "").strip().lower()
    if not p:
        return "catchup"
    p = _HABIT_REMIND_PROFILE_ALIASES.get(p, p)
    return p if p in _HABIT_REMIND_PROFILES else "catchup"


def _escalation_interval_minutes(level: int, profile: str = "catchup") -> int:
    """
    Increasing frequency as level grows.
    level starts at 0.
    """
    p = _normalize_habit_remind_profile(profile)
    schedules = {
        # No nagging: handled by next-day catch-up loop.
        "catchup": [1440, 1440, 1440, 1440, 1440],
        # Nagging (opt-in)
        "nag_gentle": [720, 360, 360, 360, 360],
        "nag_normal": [240, 120, 60, 30, 15],
        "nag_aggressive": [60, 30, 15, 10, 5],
        "nag_daily": [1440, 1440, 1440, 1440, 1440],
    }
    schedule = schedules.get(p) or schedules["catchup"]
    idx = max(0, min(len(schedule) - 1, int(level)))
    # Global anti-spam: never remind more frequently than every 30 minutes.
    return max(30, int(schedule[idx]))


class ProductivityCog(commands.Cog, name="Productivity"):
    def __init__(self, bot: commands.Bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager

    async def cog_load(self):
        self.reminder_loop.start()
        self.habit_catchup_loop.start()
        self.monthly_report_loop.start()
        logger.info("ProductivityCog loaded and reminder loop started.")

    async def cog_unload(self):
        self.reminder_loop.cancel()
        self.habit_catchup_loop.cancel()
        self.monthly_report_loop.cancel()
        logger.info("ProductivityCog unloaded and reminder loop cancelled.")

    def _parse_days_arg(self, raw, *, max_days: int = 3650) -> Optional[int]:
        """
        Returns:
        - int days (1..max_days) when provided
        - None when 'all' is requested
        """
        if raw is None:
            return None
        if isinstance(raw, int):
            return max(1, min(max_days, int(raw)))
        s = str(raw).strip().lower()
        if s in {"all", "a", "*"}:
            return None
        try:
            n = int(s)
        except ValueError:
            return -1  # sentinel for invalid
        return max(1, min(max_days, n))

    def _month_key(self, dt_utc: datetime) -> str:
        return dt_utc.strftime("%Y-%m")

    def _prev_month_range_utc(self, now_utc: datetime) -> tuple[datetime, datetime]:
        """
        Returns (start_utc, end_utc) for the previous calendar month in UTC.
        """
        now_utc = now_utc.astimezone(timezone.utc)
        first_this = datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc)
        end_prev = first_this - timedelta(seconds=1)
        start_prev = datetime(end_prev.year, end_prev.month, 1, tzinfo=timezone.utc)
        return start_prev, end_prev

    def _prev_month_range_in_tz(self, now_utc: datetime, tz) -> tuple[datetime, datetime]:
        """
        Returns (start_local, end_local) for the previous calendar month in the given tz.
        """
        now_local = now_utc.astimezone(tz)
        first_this_local = datetime(now_local.year, now_local.month, 1, tzinfo=tz)
        end_prev_local = first_this_local - timedelta(seconds=1)
        start_prev_local = datetime(end_prev_local.year, end_prev_local.month, 1, tzinfo=tz)
        return start_prev_local, end_prev_local

    async def _send_monthly_report_for_user(self, user_id: int, *, now_utc: datetime) -> bool:
        """
        Generates and DMs a monthly report to the user. Returns True if sent.
        """
        if not self.db_manager:
            return False
        # Respect DND (best-effort). We'll retry next loop.
        if await self._is_user_in_dnd(int(user_id)):
            return False

        # Opt-out flag
        enabled = await self.bot.loop.run_in_executor(
            None, self.db_manager.get_user_preference, int(user_id), "monthly_report_enabled", True
        )
        if enabled is False:
            return False

        # Prevent duplicates: store last sent for current month key (UTC)
        current_month_key = self._month_key(now_utc)
        last_sent = await self.bot.loop.run_in_executor(
            None, self.db_manager.get_user_preference, int(user_id), "monthly_report_last_sent_ym", None
        )
        if isinstance(last_sent, str) and last_sent.strip() == current_month_key:
            return False

        # We report the *previous month*
        start_prev_utc, end_prev_utc = self._prev_month_range_utc(now_utc)
        prev_label = start_prev_utc.strftime("%Y-%m")

        # --- To-dos (UTC month) ---
        todo_days = (end_prev_utc.date() - start_prev_utc.date()).days + 1
        todo_stats = None
        if hasattr(self.db_manager, "get_todo_stats_any_scope"):
            todo_stats = await self.bot.loop.run_in_executor(
                None, lambda: self.db_manager.get_todo_stats_any_scope(int(user_id), days=int(todo_days), now_utc=end_prev_utc.strftime("%Y-%m-%d %H:%M:%S"))
            )

        # --- Habits (per-habit tz month) ---
        habits = []
        if hasattr(self.db_manager, "list_habits_any_scope"):
            habits = await self.bot.loop.run_in_executor(None, self.db_manager.list_habits_any_scope, int(user_id), 200)

        habit_summaries = []
        for h in habits or []:
            try:
                hid = int(h.get("id"))
            except Exception:
                continue
            try:
                guild_id = int(h.get("guild_id") or 0)
            except Exception:
                guild_id = 0
            tz = _tzinfo_from_name(h.get("tz_name"))
            start_local, end_local = self._prev_month_range_in_tz(now_utc, tz)
            habit_days = (end_local.date() - start_local.date()).days + 1
            # Pick an end-of-month moment in the habit tz to align get_habit_stats local window.
            end_local_dt = datetime.combine(end_local.date(), dtime(23, 59, 59)).replace(tzinfo=tz)
            end_local_as_utc = end_local_dt.astimezone(timezone.utc)
            stats = await self.bot.loop.run_in_executor(
                None,
                lambda: self.db_manager.get_habit_stats(
                    guild_id,
                    int(user_id),
                    hid,
                    days=int(habit_days),
                    now_utc=end_local_as_utc.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            if not isinstance(stats, dict):
                continue
            habit_summaries.append(stats)

        # Build embed
        embed = discord.Embed(title=f"📬 Monthly report — {prev_label}", color=discord.Color.dark_teal())

        if isinstance(todo_stats, dict):
            open_count = int(todo_stats.get("open_count") or 0)
            done_in = int(todo_stats.get("done_in_range") or 0)
            created_in = int(todo_stats.get("created_in_range") or 0)
            avg_hours = todo_stats.get("avg_hours_to_done")
            avg_str = "n/a" if avg_hours is None else f"{float(avg_hours):.1f}h"
            cur = int(todo_stats.get("current_done_streak_days") or 0)
            best = int(todo_stats.get("best_done_streak_days") or 0)
            embed.add_field(
                name="✅ To‑dos (UTC month)",
                value=(
                    f"- Created: **{created_in}**\n"
                    f"- Completed: **{done_in}**\n"
                    f"- Avg time-to-done: **{avg_str}**\n"
                    f"- Done streak (days w/ ≥1 done): **{best}** best, **{cur}** current\n"
                    f"- Still open now: **{open_count}**"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="✅ To‑dos", value="No to-do stats available.", inline=False)

        if habit_summaries:
            # Sort by scheduled_days desc, show top 8 (keeps DM readable)
            habit_summaries.sort(key=lambda s: int(s.get("scheduled_days") or 0), reverse=True)
            lines = []
            for s in habit_summaries[:8]:
                hid = int(s.get("id") or 0)
                name = str(s.get("name") or "Habit")
                scheduled = int(s.get("scheduled_days") or 0)
                completed = int(s.get("completed_days") or 0)
                rate = float(s.get("completion_rate") or 0.0) * 100.0
                best = int(s.get("best_streak") or 0)
                lines.append(f"- **#{hid}** {name}: **{completed}/{scheduled}** (**{rate:.0f}%**), best streak **{best}**")
            embed.add_field(name="📌 Habits (prev month in each habit’s TZ)", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="📌 Habits", value="No habit stats available.", inline=False)

        # Attach charts (keep it to 1-2 images max)
        files: List[discord.File] = []
        try:
            from utils.chart_utils import get_todo_daily_created_done_chart_image
            if isinstance(todo_stats, dict):
                labels = todo_stats.get("daily_labels") or []
                created = todo_stats.get("daily_created") or []
                done = todo_stats.get("daily_done") or []
                title = f"To‑dos — created vs done ({prev_label})"
                img = await self.bot.loop.run_in_executor(None, partial(get_todo_daily_created_done_chart_image, title, labels, created, done))
                if img:
                    files.append(discord.File(fp=img, filename=f"monthly_todos_{prev_label}.png"))
        except Exception:
            pass

        sent = await self._dm_user(int(user_id), embed=embed)
        if not sent:
            return False
        # If we have charts, send them as a follow-up DM message.
        if files:
            await self._dm_user(int(user_id), content="Charts:", embed=None)
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
            try:
                await user.send(files=files)
            except Exception:
                pass

        await self.bot.loop.run_in_executor(
            None, self.db_manager.set_user_preference, int(user_id), "monthly_report_last_sent_ym", current_month_key
        )
        return True

    @tasks.loop(minutes=60)
    async def monthly_report_loop(self):
        """
        Sends monthly reports on/after the start of a new month (best-effort).
        """
        if not self.db_manager:
            return
        now = _utc_now()
        # Only attempt near the start of the month (first 2 days) to reduce load.
        if now.day not in (1, 2):
            return

        # Iterate users who actually have data.
        user_ids = await self.bot.loop.run_in_executor(None, self.db_manager.list_users_with_productivity_data, 5000)
        for uid in user_ids or []:
            try:
                await self._send_monthly_report_for_user(int(uid), now_utc=now)
            except Exception as e:
                logger.warning(f"monthly_report_loop error for user {uid}: {e}")

    @monthly_report_loop.before_loop
    async def before_monthly_report_loop(self):
        await self.bot.wait_until_ready()

    async def _defer_if_interaction(self, ctx: commands.Context, *, ephemeral: bool = True) -> None:
        if not getattr(ctx, "interaction", None):
            return
        try:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer(ephemeral=ephemeral)
        except (discord.InteractionResponded, discord.HTTPException):
            return

    async def send_response(
        self,
        ctx: commands.Context,
        content: Optional[str] = None,
        *,
        embed: Optional[discord.Embed] = None,
        ephemeral: bool = True,
        wait: bool = False,
        view: Optional[discord.ui.View] = None,
    ):
        interaction = getattr(ctx, "interaction", None)
        if interaction:
            base_kwargs = {}
            if embed is not None:
                base_kwargs["embed"] = embed
            if content is not None:
                base_kwargs["content"] = content
            if view is not None:
                base_kwargs["view"] = view

            # If this is the first response, use response.send_message(). If the caller needs
            # a Message object, fetch it via original_response().
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(**base_kwargs, ephemeral=ephemeral)
                    if wait:
                        try:
                            return await interaction.original_response()
                        except discord.HTTPException:
                            return None
                    return None
            except (discord.InteractionResponded, discord.HTTPException):
                pass

            if wait:
                return await interaction.followup.send(**base_kwargs, ephemeral=ephemeral, wait=True)
            return await interaction.followup.send(**base_kwargs, ephemeral=ephemeral)

        kwargs2 = {}
        if content is not None:
            kwargs2["content"] = content
        if embed is not None:
            kwargs2["embed"] = embed
        if view is not None:
            kwargs2["view"] = view
        return await ctx.send(**kwargs2)

    async def _is_user_in_dnd(self, user_id: int) -> bool:
        """
        Best-effort DND check using existing settings keys (same as BooksCog).
        If preferences cannot be loaded/parsed, treat as not in DND.
        """
        if not self.db_manager:
            return False
        try:
            dnd_enabled = await self.bot.loop.run_in_executor(
                None, self.db_manager.get_user_preference, user_id, "dnd_enabled", False
            )
            if not dnd_enabled:
                return False

            dnd_start_str = await self.bot.loop.run_in_executor(
                None, self.db_manager.get_user_preference, user_id, "dnd_start_time", "00:00"
            )
            dnd_end_str = await self.bot.loop.run_in_executor(
                None, self.db_manager.get_user_preference, user_id, "dnd_end_time", "00:00"
            )
            try:
                start_t = datetime.strptime(dnd_start_str, "%H:%M").time()
                end_t = datetime.strptime(dnd_end_str, "%H:%M").time()
            except ValueError:
                start_t = dtime(0, 0)
                end_t = dtime(0, 0)

            now_t = datetime.now().time()
            # Treat DND as a half-open interval [start, end) so the "end" time is not suppressed.
            if start_t == end_t:
                return False
            if start_t < end_t:
                return start_t <= now_t < end_t
            return now_t >= start_t or now_t < end_t
        except Exception:
            return False

    async def _dm_user(self, user_id: int, *, content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> bool:
        user = self.bot.get_user(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException):
                return False
        try:
            await user.send(content=content, embed=embed)
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            return False

    async def _load_habit_for_ctx(self, ctx: commands.Context, habit_id: int) -> tuple[Optional[dict], int]:
        """
        Loads habit for guild or DM context, returning (habit_row, resolved_guild_id).
        In DMs, attempts to resolve the habit's original guild scope via any-scope lookup.
        """
        is_dm = ctx.guild is None
        guild_id = _scope_guild_id_from_ctx(ctx)
        if is_dm and hasattr(self.db_manager, "get_habit_any_scope"):
            habit = await self.bot.loop.run_in_executor(None, self.db_manager.get_habit_any_scope, ctx.author.id, int(habit_id))
            if habit and "guild_id" in habit:
                try:
                    guild_id = int(habit.get("guild_id") or 0)
                except Exception:
                    guild_id = 0
            return habit, guild_id
        habit = await self.bot.loop.run_in_executor(None, self.db_manager.get_habit, guild_id, ctx.author.id, int(habit_id))
        return habit, guild_id

    # -------------------------
    # To-do commands
    # -------------------------
    @commands.hybrid_command(name="todo_add", description="Add an item to your to-do list.")
    @discord.app_commands.describe(task="What do you want to add?")
    async def todo_add(self, ctx: commands.Context, task: str):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        guild_id = _scope_guild_id_from_ctx(ctx)
        todo_id = await self.bot.loop.run_in_executor(None, self.db_manager.create_todo_item, guild_id, ctx.author.id, task)
        if not todo_id:
            await self.send_response(ctx, "Could not create that to-do item.", ephemeral=not is_dm)
            return
        await self.send_response(ctx, f"✅ Added to-do **#{todo_id}**: {task}", ephemeral=not is_dm)

    @commands.hybrid_command(name="todo_list", description="List your to-do items.")
    @discord.app_commands.describe(include_done="Include completed items (default: False).")
    async def todo_list(self, ctx: commands.Context, include_done: bool = False):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        if is_dm and hasattr(self.db_manager, "list_todo_items_any_scope"):
            items = await self.bot.loop.run_in_executor(
                None, self.db_manager.list_todo_items_any_scope, ctx.author.id, include_done, 50
            )
            title = "✅ Your To‑Dos (all scopes)"
        else:
            guild_id = _scope_guild_id_from_ctx(ctx)
            items = await self.bot.loop.run_in_executor(None, self.db_manager.list_todo_items, guild_id, ctx.author.id, include_done, 50)
            title = "✅ Your To‑Dos"
        if not items:
            await self.send_response(ctx, "Your to-do list is empty. Use `/todo_add`.", ephemeral=not is_dm)
            return

        embed = discord.Embed(title=title, color=discord.Color.blurple())
        lines: List[str] = []
        for r in items[:50]:
            tid = r.get("id")
            content = r.get("content") or ""
            done = bool(r.get("is_done"))
            prefix = "☑️" if done else "⬜"
            # In DM + any-scope listing, include scope label (guild_id) so users can disambiguate.
            if is_dm and "guild_id" in r:
                gid = str(r.get("guild_id") or "0")
                scope = "DM" if gid == "0" else f"g:{gid}"
                lines.append(f"{prefix} **#{tid}** ({scope}) — {content}")
            else:
                lines.append(f"{prefix} **#{tid}** — {content}")
        embed.description = "\n".join(lines)[:4000]
        await self.send_response(ctx, embed=embed, ephemeral=not is_dm)

    @commands.hybrid_command(name="todo_done", description="Mark a to-do item as done.")
    @discord.app_commands.describe(todo_id="The numeric id (from /todo_list).")
    async def todo_done(self, ctx: commands.Context, todo_id: int):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return
        if is_dm and hasattr(self.db_manager, "set_todo_done_any_scope"):
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_todo_done_any_scope, ctx.author.id, int(todo_id), True)
        else:
            guild_id = _scope_guild_id_from_ctx(ctx)
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_todo_done, guild_id, ctx.author.id, int(todo_id), True)
        if not ok:
            await self.send_response(ctx, "Could not find that to-do (or it’s not yours).", ephemeral=not is_dm)
            return
        await self.send_response(ctx, f"✅ Marked **#{todo_id}** as done.", ephemeral=not is_dm)

    @commands.hybrid_command(name="todo_undo", description="Mark a to-do item as not done.")
    @discord.app_commands.describe(todo_id="The numeric id (from /todo_list).")
    async def todo_undo(self, ctx: commands.Context, todo_id: int):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return
        if is_dm and hasattr(self.db_manager, "set_todo_done_any_scope"):
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_todo_done_any_scope, ctx.author.id, int(todo_id), False)
        else:
            guild_id = _scope_guild_id_from_ctx(ctx)
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_todo_done, guild_id, ctx.author.id, int(todo_id), False)
        if not ok:
            await self.send_response(ctx, "Could not find that to-do (or it’s not yours).", ephemeral=not is_dm)
            return
        await self.send_response(ctx, f"↩️ Marked **#{todo_id}** as not done.", ephemeral=not is_dm)

    @commands.hybrid_command(name="todo_remove", description="Remove a to-do item.")
    @discord.app_commands.describe(todo_id="The numeric id (from /todo_list).")
    async def todo_remove(self, ctx: commands.Context, todo_id: int):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return
        if is_dm and hasattr(self.db_manager, "delete_todo_item_any_scope"):
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.delete_todo_item_any_scope, ctx.author.id, int(todo_id))
        else:
            guild_id = _scope_guild_id_from_ctx(ctx)
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.delete_todo_item, guild_id, ctx.author.id, int(todo_id))
        if not ok:
            await self.send_response(ctx, "Could not find that to-do (or it’s not yours).", ephemeral=not is_dm)
            return
        await self.send_response(ctx, f"🗑️ Removed **#{todo_id}**.", ephemeral=not is_dm)

    @commands.hybrid_command(name="todo_stats", description="Show your to-do stats (open/done, streak, speed).")
    @discord.app_commands.describe(days="How many past days to analyze (default: 30).")
    async def todo_stats(self, ctx: commands.Context, days: int = 30):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        days = max(1, min(365, int(days)))
        guild_id = _scope_guild_id_from_ctx(ctx)

        if is_dm and hasattr(self.db_manager, "get_todo_stats_any_scope"):
            stats = await self.bot.loop.run_in_executor(None, lambda: self.db_manager.get_todo_stats_any_scope(ctx.author.id, days=days))
            scope_label = "all scopes"
        else:
            stats = await self.bot.loop.run_in_executor(None, lambda: self.db_manager.get_todo_stats(guild_id, ctx.author.id, days=days))
            scope_label = "this server" if ctx.guild else "DM scope"

        if not isinstance(stats, dict):
            await self.send_response(ctx, "No stats available yet.", ephemeral=not is_dm)
            return

        open_count = int(stats.get("open_count") or 0)
        done_count = int(stats.get("done_count") or 0)
        total_count = int(stats.get("total_count") or 0)
        created_in = int(stats.get("created_in_range") or 0)
        done_in = int(stats.get("done_in_range") or 0)
        cur_streak = int(stats.get("current_done_streak_days") or 0)
        best_streak = int(stats.get("best_done_streak_days") or 0)
        avg_hours = stats.get("avg_hours_to_done")
        avg_str = "n/a" if avg_hours is None else f"{float(avg_hours):.1f}h"

        rstart = stats.get("range_start_utc_day") or ""
        rend = stats.get("range_end_utc_day") or ""

        embed = discord.Embed(title="📊 To‑do stats", color=discord.Color.blurple())
        embed.add_field(name=f"Scope ({scope_label})", value=f"- Range (UTC day): **{rstart} → {rend}**", inline=False)
        embed.add_field(name="Counts", value=f"- Open: **{open_count}**\n- Done: **{done_count}**\n- Total: **{total_count}**", inline=False)
        embed.add_field(
            name=f"Last {days} days",
            value=f"- Created: **{created_in}**\n- Completed: **{done_in}**\n- Avg time-to-done: **{avg_str}**",
            inline=False,
        )
        embed.add_field(
            name="Streaks (UTC day with ≥1 completed task)",
            value=f"- Current: **{cur_streak}** days\n- Best (in range): **{best_streak}** days",
            inline=False,
        )
        await self.send_response(ctx, embed=embed, ephemeral=not is_dm)

    @commands.hybrid_command(name="todo_graph", description="Show graphs for your to-dos (created vs done + weekday breakdown).")
    @discord.app_commands.describe(
        days="How many past days to chart (default: 30).",
        kind="trend | weekday | both (default: both)",
    )
    async def todo_graph(self, ctx: commands.Context, days: int = 30, kind: str = "both"):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        from utils.chart_utils import get_todo_daily_created_done_chart_image, get_todo_weekday_done_chart_image

        days = max(1, min(365, int(days)))
        kind_l = str(kind or "both").strip().lower()
        if kind_l not in {"trend", "weekday", "both"}:
            await self.send_response(ctx, "Kind must be `trend`, `weekday`, or `both`.", ephemeral=not is_dm)
            return

        guild_id = _scope_guild_id_from_ctx(ctx)
        if is_dm and hasattr(self.db_manager, "get_todo_stats_any_scope"):
            stats = await self.bot.loop.run_in_executor(None, lambda: self.db_manager.get_todo_stats_any_scope(ctx.author.id, days=days))
            title_scope = "To‑dos — all scopes"
        else:
            stats = await self.bot.loop.run_in_executor(None, lambda: self.db_manager.get_todo_stats(guild_id, ctx.author.id, days=days))
            title_scope = "To‑dos — this server" if ctx.guild else "To‑dos — DM scope"

        if not isinstance(stats, dict):
            await self.send_response(ctx, "No graph data yet.", ephemeral=not is_dm)
            return

        labels = stats.get("daily_labels") or []
        created = stats.get("daily_created") or []
        done = stats.get("daily_done") or []
        weekday_counts = stats.get("weekday_done_counts") or [0, 0, 0, 0, 0, 0, 0]
        weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        files: List[discord.File] = []
        if kind_l in {"trend", "both"}:
            title = f"{title_scope} — created vs done (last {days}d)"
            img = await self.bot.loop.run_in_executor(None, partial(get_todo_daily_created_done_chart_image, title, labels, created, done))
            if img:
                files.append(discord.File(fp=img, filename="todo_trend.png"))

        if kind_l in {"weekday", "both"}:
            title = f"{title_scope} — done by weekday (UTC)"
            img2 = await self.bot.loop.run_in_executor(None, partial(get_todo_weekday_done_chart_image, title, weekday_labels, weekday_counts))
            if img2:
                files.append(discord.File(fp=img2, filename="todo_weekday.png"))

        if not files:
            await self.send_response(ctx, "❌ Could not generate charts right now.", ephemeral=not is_dm)
            return

        if ctx.interaction:
            await ctx.interaction.followup.send(content="Here you go:", files=files, ephemeral=not is_dm)
        else:
            await ctx.send(content="Here you go:", files=files)

    @commands.hybrid_command(name="monthly_report", description="Enable/disable monthly stats DM reports.")
    @discord.app_commands.describe(enabled="Enable monthly reports (default: True).")
    async def monthly_report(self, ctx: commands.Context, enabled: bool = True):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return
        ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, ctx.author.id, "monthly_report_enabled", bool(enabled))
        if not ok:
            await self.send_response(ctx, "Could not update that setting.", ephemeral=not is_dm)
            return
        await self.send_response(ctx, ("✅ Monthly reports enabled." if enabled else "🔕 Monthly reports disabled."), ephemeral=not is_dm)

    @commands.hybrid_command(name="todo_nag", description="Enable/disable escalating reminders for a to-do item (DM).")
    @discord.app_commands.describe(
        todo_id="The numeric id (from /todo_list).",
        enabled="Enable reminders (default: True).",
        initial_minutes="When enabled, first reminder delay (default: 240).",
    )
    async def todo_nag(self, ctx: commands.Context, todo_id: int, enabled: bool = True, initial_minutes: int = 240):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return
        # Global anti-spam: don't schedule reminders more frequently than every 30 minutes.
        initial_minutes = max(30, min(7 * 24 * 60, int(initial_minutes)))
        next_remind = None
        if enabled:
            next_remind = _sqlite_utc_timestamp(_utc_now() + timedelta(minutes=initial_minutes))
        if is_dm and hasattr(self.db_manager, "set_todo_reminder_any_scope"):
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_todo_reminder_any_scope, ctx.author.id, int(todo_id), enabled, next_remind)
        else:
            guild_id = _scope_guild_id_from_ctx(ctx)
            ok = await self.bot.loop.run_in_executor(
                None, self.db_manager.set_todo_reminder, guild_id, ctx.author.id, int(todo_id), enabled, next_remind
            )
        if not ok:
            await self.send_response(ctx, "Could not update reminders for that to-do (maybe it’s done/doesn’t exist).", ephemeral=not is_dm)
            return
        if enabled:
            await self.send_response(ctx, f"🔔 Reminders enabled for **#{todo_id}**. I’ll DM you if it stays unfinished.", ephemeral=not is_dm)
        else:
            await self.send_response(ctx, f"🔕 Reminders disabled for **#{todo_id}**.", ephemeral=not is_dm)

    # -------------------------
    # Habit commands
    # -------------------------
    @commands.hybrid_command(name="habit_add", description="Create a recurring habit (with escalating reminders via DM).")
    @discord.app_commands.describe(
        name="Habit name (e.g. 'Programming').",
        days="Schedule days (e.g. 'mon-fri', 'weekdays', 'mon,wed,fri', or numbers 1-7 for Mon-Sun like '1-5', '1,3,5').",
        due_time_cet="Due time in CET/CEST (HH:MM, default 18:00).",
        remind="Whether reminders are enabled (default: True).",
    )
    async def habit_add(self, ctx: commands.Context, name: str, days: str = "mon-fri", due_time_cet: str = "18:00", remind: bool = True):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        tz = _tzinfo_from_name("Europe/Warsaw")
        due_local = _parse_hhmm_local(due_time_cet) or dtime(18, 0)
        days_list = _parse_days_spec(days)
        next_due = _next_due_datetime_cet_to_utc(_utc_now(), days_list, due_local, tz)

        guild_id = _scope_guild_id_from_ctx(ctx)
        habit_id = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.create_habit,
            guild_id,
            ctx.author.id,
            name,
            days_list,
            due_local.strftime("%H:%M"),
            "Europe/Warsaw",
            remind,
            _sqlite_utc_timestamp(next_due),
        )
        if not habit_id:
            await self.send_response(ctx, "Could not create that habit.", ephemeral=not is_dm)
            return

        await self.send_response(
            ctx,
            f"✅ Created habit **#{habit_id}**: **{name}** on `{days}` due `{due_local.strftime('%H:%M')}` CET/CEST.",
            ephemeral=not is_dm,
        )

    @commands.hybrid_command(name="habit_edit", description="Edit an existing habit (name/schedule/time/timezone/reminder profile).")
    @discord.app_commands.describe(
        habit_id="The numeric id (from /habit_list).",
        name="New name (optional).",
        days="New schedule days (optional, e.g. 'mon-fri', 'mon,wed,fri', or numbers 1-7 for Mon-Sun like '1-5', '1,3,5').",
        due_time="New due time HH:MM (optional). Interpreted in tz_name.",
        tz_name="Timezone name (optional). Use 'UTC' or an IANA name like 'Europe/Warsaw'.",
        remind_profile="catchup (default) | nag_gentle | nag_normal | nag_aggressive | nag_daily (optional).",
    )
    async def habit_edit(
        self,
        ctx: commands.Context,
        habit_id: int,
        name: Optional[str] = None,
        days: Optional[str] = None,
        due_time: Optional[str] = None,
        tz_name: Optional[str] = None,
        remind_profile: Optional[str] = None,
    ):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        # Load habit (needed to compute next due + keep unchanged fields)
        guild_id = _scope_guild_id_from_ctx(ctx)
        if is_dm and hasattr(self.db_manager, "get_habit_any_scope"):
            habit = await self.bot.loop.run_in_executor(None, self.db_manager.get_habit_any_scope, ctx.author.id, int(habit_id))
            if habit and "guild_id" in habit:
                try:
                    guild_id = int(habit.get("guild_id") or 0)
                except Exception:
                    guild_id = 0
        else:
            habit = await self.bot.loop.run_in_executor(None, self.db_manager.get_habit, guild_id, ctx.author.id, int(habit_id))
        if not habit:
            await self.send_response(ctx, "Could not find that habit (or it’s not yours).", ephemeral=not is_dm)
            return

        # Determine what changed
        schedule_changed = any(v is not None for v in (days, due_time, tz_name))
        profile_changed = remind_profile is not None

        # Days
        if days is None:
            try:
                days_list = json.loads(habit.get("days_of_week") or "[]")
            except Exception:
                days_list = [0, 1, 2, 3, 4]
        else:
            days_list = _parse_days_spec(days)

        # Timezone
        current_tz_name = str(habit.get("tz_name") or "Europe/Warsaw")
        new_tz_name = (tz_name.strip() if isinstance(tz_name, str) and tz_name.strip() else current_tz_name)
        # Validate tz if provided and zoneinfo available (avoid persisting garbage tz names)
        if tz_name is not None:
            if new_tz_name.upper() != "UTC" and ZoneInfo is not None:
                try:
                    ZoneInfo(new_tz_name)
                except Exception:
                    await self.send_response(ctx, f"Unknown timezone `{new_tz_name}`. Use `UTC` or a valid IANA name (e.g. `Europe/Warsaw`).", ephemeral=not is_dm)
                    return

        tz = _tzinfo_from_name(new_tz_name)

        # Due time string (interpreted in tz)
        current_due_str = str(habit.get("due_time_local") or habit.get("due_time_utc") or "18:00")
        new_due_str = (due_time.strip() if isinstance(due_time, str) and due_time.strip() else current_due_str)

        if tz == timezone.utc:
            due_utc = _parse_hhmm_utc(new_due_str)
            if not due_utc:
                await self.send_response(ctx, "Invalid `due_time`. Use `HH:MM` (e.g. `18:00`).", ephemeral=not is_dm)
                return
            next_due = _next_due_datetime_utc(_utc_now(), days_list, due_utc)
        else:
            due_local = _parse_hhmm_local(new_due_str)
            if not due_local:
                await self.send_response(ctx, "Invalid `due_time`. Use `HH:MM` (e.g. `18:00`).", ephemeral=not is_dm)
                return
            next_due = _next_due_datetime_cet_to_utc(_utc_now(), days_list, due_local, tz)

        # Persist
        next_due_s = _sqlite_utc_timestamp(next_due) if schedule_changed else None
        if is_dm and hasattr(self.db_manager, "set_habit_schedule_and_due_any_scope"):
            ok = await self.bot.loop.run_in_executor(
                None,
                lambda: self.db_manager.set_habit_schedule_and_due_any_scope(
                    ctx.author.id,
                    int(habit_id),
                    name=name,
                    days_of_week=(days_list if days is not None else None),
                    due_time_local=(new_due_str if due_time is not None else None),
                    tz_name=(new_tz_name if tz_name is not None else None),
                    next_due_at_utc=next_due_s,
                    remind_profile=remind_profile,
                    remind_level=(0 if schedule_changed else None),
                    clear_next_remind_at=bool(schedule_changed),
                    clear_snoozed_until=bool(schedule_changed),
                ),
            )
        else:
            ok = await self.bot.loop.run_in_executor(
                None,
                lambda: self.db_manager.set_habit_schedule_and_due(
                    guild_id,
                    ctx.author.id,
                    int(habit_id),
                    name=name,
                    days_of_week=(days_list if days is not None else None),
                    due_time_local=(new_due_str if due_time is not None else None),
                    tz_name=(new_tz_name if tz_name is not None else None),
                    next_due_at_utc=next_due_s,
                    remind_profile=remind_profile,
                    remind_level=(0 if schedule_changed else None),
                    clear_next_remind_at=bool(schedule_changed),
                    clear_snoozed_until=bool(schedule_changed),
                ),
            )

        if not ok:
            await self.send_response(ctx, "Could not update that habit.", ephemeral=not is_dm)
            return

        await self.send_response(
            ctx,
            (
                f"✅ Updated habit **#{habit_id}**."
                + (
                    (lambda local_str, tz_label: f" Next due: `{local_str}` {tz_label}.")(*_format_due_display(next_due, new_tz_name))
                    if schedule_changed
                    else ""
                )
                + (f" Reminders: `{_normalize_habit_remind_profile(remind_profile)}`." if profile_changed else "")
            ),
            ephemeral=not is_dm,
        )

    @commands.hybrid_command(name="habit_list", description="List your habits.")
    async def habit_list(self, ctx: commands.Context):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        # Refresh stale due pointers so `/habit_list` doesn't show "next due" stuck days/weeks in the past.
        # This advances `next_due_at` only when the schedule has already moved on to the next occurrence.
        try:
            now_str = _sqlite_utc_timestamp(_utc_now())
            if is_dm and hasattr(self.db_manager, "refresh_stale_habit_due_dates_any_scope"):
                await self.bot.loop.run_in_executor(
                    None, lambda: self.db_manager.refresh_stale_habit_due_dates_any_scope(ctx.author.id, now_utc=now_str, limit=200)
                )
            elif hasattr(self.db_manager, "refresh_stale_habit_due_dates"):
                guild_id0 = _scope_guild_id_from_ctx(ctx)
                await self.bot.loop.run_in_executor(
                    None, lambda: self.db_manager.refresh_stale_habit_due_dates(guild_id0, ctx.author.id, now_utc=now_str, limit=200)
                )
        except Exception:
            # Best-effort only; listing should still work even if refresh fails.
            pass

        if is_dm and hasattr(self.db_manager, "list_habits_any_scope"):
            habits = await self.bot.loop.run_in_executor(None, self.db_manager.list_habits_any_scope, ctx.author.id, 50)
            title = "📌 Your Habits (all scopes)"
        else:
            guild_id = _scope_guild_id_from_ctx(ctx)
            habits = await self.bot.loop.run_in_executor(None, self.db_manager.list_habits, guild_id, ctx.author.id, 50)
            title = "📌 Your Habits"
        if not habits:
            await self.send_response(ctx, "No habits yet. Use `/habit_add`.", ephemeral=not is_dm)
            return

        embed = discord.Embed(title=title, color=discord.Color.green())
        lines: List[str] = []
        now_utc = _utc_now()
        for h in habits[:50]:
            hid = h.get("id")
            name = h.get("name") or ""
            tz_name = h.get("tz_name")
            due_time = h.get("due_time_local") or h.get("due_time_utc") or "18:00"
            remind_enabled = bool(h.get("remind_enabled"))
            remind_profile = _normalize_habit_remind_profile(h.get("remind_profile"))
            next_due = h.get("next_due_at")
            last = h.get("last_checkin_at")
            rflag = "🔔" if remind_enabled else "🔕"
            if str(tz_name or "").strip() in ("Europe/Warsaw", "CET"):
                tz_label = "CET/CEST"
            elif str(tz_name or "").strip().upper() in ("UTC", "ETC/UTC", "Z"):
                tz_label = "UTC"
            elif tz_name:
                tz_label = str(tz_name)
            else:
                tz_label = "CET/CEST"
            scope_label = ""
            if is_dm and "guild_id" in h:
                gid = str(h.get("guild_id") or "0")
                scope_label = f" ({'DM' if gid == '0' else f'g:{gid}'})"

            next_due_disp = next_due or "n/a"
            status_prefix = "next due"
            if isinstance(next_due, str) and next_due.strip():
                dt_utc = _parse_sqlite_utc_timestamp(next_due)
                if dt_utc is not None:
                    local_str, tz_disp = _format_due_display(dt_utc, str(tz_name or "UTC"))
                    next_due_disp = f"{local_str} {tz_disp}"
                    if dt_utc <= now_utc:
                        status_prefix = "due (overdue)"

            lines.append(
                f"{rflag} **#{hid}**{scope_label} — **{name}** (due `{due_time}` `{tz_label}`, remind: `{remind_profile}`)\n"
                f"• {status_prefix}: `{next_due_disp}` | last check-in: `{last or 'n/a'}`"
            )
        embed.description = "\n".join(lines)[:4000]
        await self.send_response(ctx, embed=embed, ephemeral=not is_dm)

    @commands.hybrid_command(name="habit_checkin", description="Mark a habit as completed (check-in).")
    @discord.app_commands.describe(habit_id="The numeric id (from /habit_list).", note="Optional note.")
    async def habit_checkin(self, ctx: commands.Context, habit_id: int, note: Optional[str] = None):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        guild_id = _scope_guild_id_from_ctx(ctx)
        if is_dm and hasattr(self.db_manager, "get_habit_any_scope"):
            habit = await self.bot.loop.run_in_executor(None, self.db_manager.get_habit_any_scope, ctx.author.id, int(habit_id))
            if habit and "guild_id" in habit:
                try:
                    guild_id = int(habit.get("guild_id") or 0)
                except Exception:
                    guild_id = 0
        else:
            habit = await self.bot.loop.run_in_executor(None, self.db_manager.get_habit, guild_id, ctx.author.id, int(habit_id))
        if not habit:
            await self.send_response(ctx, "Could not find that habit (or it’s not yours).", ephemeral=not is_dm)
            return

        # compute next due from stored schedule
        days_list: List[int] = []
        try:
            days_list = json.loads(habit.get("days_of_week") or "[]")
        except Exception:
            days_list = [0, 1, 2, 3, 4]
        tz = _tzinfo_from_name(habit.get("tz_name"))
        due_local_str = str(habit.get("due_time_local") or habit.get("due_time_utc") or "18:00")

        if tz == timezone.utc:
            due_utc = _parse_hhmm_utc(due_local_str) or dtime(18, 0, tzinfo=timezone.utc)
            next_due = _next_due_datetime_utc(_utc_now(), days_list, due_utc)
        else:
            due_local = _parse_hhmm_local(due_local_str) or dtime(18, 0)
            next_due = _next_due_datetime_cet_to_utc(_utc_now(), days_list, due_local, tz)

        if is_dm and hasattr(self.db_manager, "record_habit_checkin_any_scope"):
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.record_habit_checkin_any_scope, ctx.author.id, int(habit_id), note, _sqlite_utc_timestamp(next_due))
        else:
            ok = await self.bot.loop.run_in_executor(
                None,
                self.db_manager.record_habit_checkin,
                guild_id,
                ctx.author.id,
                int(habit_id),
                note,
                _sqlite_utc_timestamp(next_due),
            )
        if not ok:
            await self.send_response(ctx, "Could not check in for that habit.", ephemeral=not is_dm)
            return
        local_str, tz_label = _format_due_display(next_due, str(habit.get("tz_name") or "UTC"))
        await self.send_response(
            ctx,
            f"✅ Check-in saved for **#{habit_id}**. Next due: `{local_str}` {tz_label}.",
            ephemeral=not is_dm,
        )

    @commands.hybrid_command(name="habit_stats", description="Show stats for a habit (streaks, completion rate, totals).")
    @discord.app_commands.describe(habit_id="The numeric id (from /habit_list).", days="How many past days to analyze (default: 30).")
    async def habit_stats(self, ctx: commands.Context, habit_id: int, days: str = "30"):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        days_n = self._parse_days_arg(days, max_days=3650)
        if days_n == -1:
            await self.send_response(ctx, "Invalid `days`. Use a number like `30` or `all`.", ephemeral=not is_dm)
            return
        habit, guild_id = await self._load_habit_for_ctx(ctx, int(habit_id))
        if not habit:
            await self.send_response(ctx, "Could not find that habit (or it’s not yours).", ephemeral=not is_dm)
            return

        stats = await self.bot.loop.run_in_executor(
            None,
            lambda: self.db_manager.get_habit_stats(guild_id, ctx.author.id, int(habit_id), days=days_n),
        )
        if not isinstance(stats, dict):
            await self.send_response(ctx, "No stats available yet. Try checking in a few times first.", ephemeral=not is_dm)
            return

        name = str(stats.get("name") or habit.get("name") or "Habit")
        scheduled = int(stats.get("scheduled_days") or 0)
        completed = int(stats.get("completed_days") or 0)
        rate = float(stats.get("completion_rate") or 0.0) * 100.0
        cur_streak = int(stats.get("current_streak") or 0)
        best_streak = int(stats.get("best_streak") or 0)
        total_checkins = int(stats.get("total_checkins") or 0)
        total_snoozes = int(stats.get("total_snoozes") or 0)
        snoozes_in_range = int(stats.get("snoozes_in_range") or 0)
        last_checkin_utc = stats.get("last_checkin_at_utc") or ""
        tz_name = str(stats.get("tz_name") or habit.get("tz_name") or "UTC")
        rstart = stats.get("range_start_local") or ""
        rend = stats.get("range_end_local") or ""

        embed = discord.Embed(title=f"📊 Habit stats — {name} (#{habit_id})", color=discord.Color.green())
        range_label = "All time" if days_n is None else f"Last {days_n} days"
        embed.add_field(name=f"{range_label} (local)", value=f"- Range: **{rstart} → {rend}**\n- TZ: `{tz_name}`", inline=False)
        embed.add_field(
            name="Completion vs schedule",
            value=f"- Scheduled days: **{scheduled}**\n- Completed days: **{completed}**\n- Rate: **{rate:.0f}%**",
            inline=False,
        )
        embed.add_field(
            name="Streaks (scheduled days)",
            value=f"- Current: **{cur_streak}**\n- Best (last ~10y max): **{best_streak}**",
            inline=False,
        )
        last_disp = "n/a"
        if isinstance(last_checkin_utc, str) and last_checkin_utc.strip():
            dt_last = _parse_sqlite_utc_timestamp(last_checkin_utc)
            if dt_last is not None:
                last_local, last_tz = _format_due_display(dt_last, tz_name)
                last_disp = f"{last_local} {last_tz}"
        embed.add_field(
            name="Totals",
            value=(
                f"- Total check-ins: **{total_checkins}**\n"
                f"- Snoozes (this range): **{snoozes_in_range}**\n"
                f"- Total snoozes: **{total_snoozes}**\n"
                f"- Last check-in: `{last_disp}`"
            ),
            inline=False,
        )
        await self.send_response(ctx, embed=embed, ephemeral=not is_dm)

    @commands.hybrid_command(name="habits_stats", description="Show overall stats across all your habits.")
    @discord.app_commands.describe(days="How many past days to analyze (default: 30).", limit_habits="Max habits to include (default: 50).")
    async def habits_stats(self, ctx: commands.Context, days: str = "30", limit_habits: int = 50):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        days_n = self._parse_days_arg(days, max_days=3650)
        if days_n == -1:
            await self.send_response(ctx, "Invalid `days`. Use a number like `30` or `all`.", ephemeral=not is_dm)
            return
        limit_habits = max(1, min(200, int(limit_habits)))

        guild_id = _scope_guild_id_from_ctx(ctx)

        if is_dm and hasattr(self.db_manager, "get_habits_overall_stats_any_scope"):
            stats = await self.bot.loop.run_in_executor(
                None, lambda: self.db_manager.get_habits_overall_stats_any_scope(ctx.author.id, days=days_n, limit_habits=limit_habits)
            )
            scope_label = "all scopes"
        else:
            stats = await self.bot.loop.run_in_executor(
                None, lambda: self.db_manager.get_habits_overall_stats(guild_id, ctx.author.id, days=days_n, limit_habits=limit_habits)
            )
            scope_label = "this server" if ctx.guild else "DM scope"

        if not isinstance(stats, dict) or not stats.get("habits"):
            await self.send_response(ctx, "No habit stats available yet. Create habits with `/habit_add` and check in with `/habit_checkin`.", ephemeral=not is_dm)
            return

        total_habits = int(stats.get("habits_with_stats") or 0)
        total_sched = int(stats.get("total_scheduled_days") or 0)
        total_comp = int(stats.get("total_completed_days") or 0)
        overall_rate = float(stats.get("overall_completion_rate") or 0.0) * 100.0
        avg_rate = float(stats.get("avg_habit_completion_rate") or 0.0) * 100.0
        total_checkins = int(stats.get("total_checkins") or 0)
        total_snoozes = int(stats.get("total_snoozes") or 0)
        snoozes_in_range = int(stats.get("snoozes_in_range") or 0)
        best_streak = int(stats.get("best_streak_max") or 0)
        avg_cur = float(stats.get("avg_current_streak") or 0.0)

        embed = discord.Embed(title="📊 Habits — overall stats", color=discord.Color.green())
        range_label = "All time" if days_n is None else f"Last **{days_n}** days"
        embed.add_field(name=f"Scope ({scope_label})", value=f"- {range_label}", inline=False)
        embed.add_field(
            name="Totals",
            value=(
                f"- Habits analyzed: **{total_habits}**\n"
                f"- Scheduled days (sum): **{total_sched}**\n"
                f"- Completed scheduled days (sum): **{total_comp}**\n"
                f"- Overall completion: **{overall_rate:.0f}%**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Streaks & check-ins",
            value=(
                f"- Total check-ins: **{total_checkins}**\n"
                f"- Snoozes (this range): **{snoozes_in_range}**\n"
                f"- Total snoozes: **{total_snoozes}**\n"
                f"- Best streak (max across habits): **{best_streak}**\n"
                f"- Avg current streak: **{avg_cur:.1f}**\n"
                f"- Avg habit completion rate: **{avg_rate:.0f}%**"
            ),
            inline=False,
        )

        habits = stats.get("habits") or []
        if isinstance(habits, list):
            # Rank by completion_rate desc, then scheduled_days desc (avoid tiny-schedule habits dominating too much)
            habits_sorted = sorted(
                [h for h in habits if isinstance(h, dict)],
                key=lambda h: (float(h.get("completion_rate") or 0.0), int(h.get("scheduled_days") or 0)),
                reverse=True,
            )
            lines: List[str] = []
            for h in habits_sorted[:8]:
                hid = int(h.get("id") or 0)
                name = str(h.get("name") or "Habit")
                scheduled = int(h.get("scheduled_days") or 0)
                completed = int(h.get("completed_days") or 0)
                rate = float(h.get("completion_rate") or 0.0) * 100.0
                bst = int(h.get("best_streak") or 0)
                # Include scope tag in DMs when available
                scope = ""
                if is_dm and "guild_id" in h:
                    gid = str(h.get("guild_id") or "0")
                    scope = f" ({'DM' if gid == '0' else f'g:{gid}'})"
                lines.append(f"- **#{hid}**{scope} {name}: **{completed}/{scheduled}** (**{rate:.0f}%**), best streak **{bst}**")
            if lines:
                embed.add_field(name="🏁 Top habits (by completion)", value="\n".join(lines)[:1024], inline=False)

        await self.send_response(ctx, embed=embed, ephemeral=not is_dm)

    @commands.hybrid_command(name="habit_graph", description="Show graphs for a habit (trend + weekday breakdown).")
    @discord.app_commands.describe(
        habit_id="The numeric id (from /habit_list).",
        days="How many past days to chart (default: 30).",
        kind="trend | weekday | both (default: both)",
    )
    async def habit_graph(self, ctx: commands.Context, habit_id: int, days: int = 30, kind: str = "both"):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        from utils.chart_utils import get_habit_daily_chart_image, get_habit_weekday_chart_image

        days = max(1, min(365, int(days)))
        kind_l = str(kind or "both").strip().lower()
        if kind_l not in {"trend", "weekday", "both"}:
            await self.send_response(ctx, "Kind must be `trend`, `weekday`, or `both`.", ephemeral=not is_dm)
            return

        habit, guild_id = await self._load_habit_for_ctx(ctx, int(habit_id))
        if not habit:
            await self.send_response(ctx, "Could not find that habit (or it’s not yours).", ephemeral=not is_dm)
            return

        stats = await self.bot.loop.run_in_executor(
            None,
            lambda: self.db_manager.get_habit_stats(guild_id, ctx.author.id, int(habit_id), days=days),
        )
        if not isinstance(stats, dict):
            await self.send_response(ctx, "No graph data yet. Try checking in a few times first.", ephemeral=not is_dm)
            return

        name = str(stats.get("name") or habit.get("name") or "Habit")
        labels = stats.get("daily_labels") or []
        values = stats.get("daily_counts") or []
        weekday_counts = stats.get("weekday_counts") or [0, 0, 0, 0, 0, 0, 0]
        weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        files: List[discord.File] = []
        if kind_l in {"trend", "both"}:
            title = f"{name} — daily check-ins (last {days}d)"
            img = await self.bot.loop.run_in_executor(None, partial(get_habit_daily_chart_image, title, labels, values))
            if img:
                files.append(discord.File(fp=img, filename=f"habit_{habit_id}_trend.png"))

        if kind_l in {"weekday", "both"}:
            title = f"{name} — weekday distribution"
            img2 = await self.bot.loop.run_in_executor(None, partial(get_habit_weekday_chart_image, title, weekday_labels, weekday_counts))
            if img2:
                files.append(discord.File(fp=img2, filename=f"habit_{habit_id}_weekday.png"))

        if not files:
            await self.send_response(ctx, "❌ Could not generate charts right now.", ephemeral=not is_dm)
            return

        content = "Here you go:"
        if ctx.interaction:
            await ctx.interaction.followup.send(content=content, files=files, ephemeral=not is_dm)
        else:
            await ctx.send(content=content, files=files)

    @commands.hybrid_command(name="habit_remove", description="Remove a habit.")
    @discord.app_commands.describe(habit_id="The numeric id (from /habit_list).")
    async def habit_remove(self, ctx: commands.Context, habit_id: int):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        habit, resolved_guild_id = await self._load_habit_for_ctx(ctx, int(habit_id))
        if not habit:
            await self.send_response(ctx, "Could not find that habit (or it’s not yours).", ephemeral=not is_dm)
            return

        habit_name = str(habit.get("name") or "Habit")

        # Interactive confirmation (buttons) for slash/hybrid invocations.
        # For prefix commands (no interaction), fall back to reactions.
        class _HabitRemoveView(discord.ui.View):
            def __init__(self, *, timeout: int = 45):
                super().__init__(timeout=timeout)
                self.choice: Optional[str] = None  # "archive" | "purge" | "cancel"

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message("This isn't for you.", ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="Remove (keep stats)", style=discord.ButtonStyle.primary, emoji="📦")
            async def archive_btn(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                self.choice = "archive"
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        child.disabled = True
                await interaction.response.edit_message(view=self)
                self.stop()

            @discord.ui.button(label="Remove + delete stats", style=discord.ButtonStyle.danger, emoji="🗑️")
            async def purge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                self.choice = "purge"
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        child.disabled = True
                await interaction.response.edit_message(view=self)
                self.stop()

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
            async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                self.choice = "cancel"
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        child.disabled = True
                await interaction.response.edit_message(view=self)
                self.stop()

        prompt = (
            f"You're removing habit **#{habit_id}**: **{habit_name}**\n\n"
            f"- **Remove (keep stats)**: hides it from `/habit_list`, stops reminders, but keeps history for `/habit_stats {habit_id}`.\n"
            f"- **Remove + delete stats**: permanently deletes the habit and its check-in history.\n"
        )

        # Button flow (preferred)
        if getattr(ctx, "interaction", None):
            view = _HabitRemoveView()
            msg_obj = await self.send_response(ctx, prompt, ephemeral=not is_dm, wait=True, view=view)
            try:
                await view.wait()
            except Exception:
                pass

            choice = getattr(view, "choice", None)
            if choice not in ("archive", "purge"):
                if msg_obj and view.is_finished():
                    try:
                        await msg_obj.edit(content=f"❌ Cancelled removing habit **#{habit_id}**.", view=None)
                    except Exception:
                        pass
                else:
                    await self.send_response(ctx, f"❌ Cancelled removing habit **#{habit_id}**.", ephemeral=not is_dm)
                return

            if choice == "archive":
                if is_dm and hasattr(self.db_manager, "archive_habit_any_scope"):
                    ok = await self.bot.loop.run_in_executor(None, self.db_manager.archive_habit_any_scope, ctx.author.id, int(habit_id))
                else:
                    ok = await self.bot.loop.run_in_executor(None, self.db_manager.archive_habit, int(resolved_guild_id), ctx.author.id, int(habit_id))
                if ok:
                    out = f"📦 Removed habit **#{habit_id}** (kept stats). You can still view: `/habit_stats {habit_id}`."
                else:
                    out = "Could not remove that habit (maybe it was already removed)."
            else:
                if is_dm and hasattr(self.db_manager, "purge_habit_any_scope"):
                    ok = await self.bot.loop.run_in_executor(None, self.db_manager.purge_habit_any_scope, ctx.author.id, int(habit_id))
                else:
                    ok = await self.bot.loop.run_in_executor(None, self.db_manager.purge_habit, int(resolved_guild_id), ctx.author.id, int(habit_id))
                out = f"🗑️ Removed habit **#{habit_id}** and deleted its stats/history." if ok else "Could not remove that habit."

            if msg_obj:
                try:
                    await msg_obj.edit(content=out, view=None)
                    return
                except Exception:
                    pass
            await self.send_response(ctx, out, ephemeral=not is_dm)
            return

        # Reaction fallback for prefix usage
        msg = await self.send_response(ctx, prompt + "\nReact with 📦 (keep stats), 🗑️ (delete stats), or ❌ (cancel).", ephemeral=False, wait=True)
        if not isinstance(msg, discord.Message):
            await self.send_response(ctx, "Could not open confirmation prompt. Please try again.", ephemeral=False)
            return
        try:
            await msg.add_reaction("📦")
            await msg.add_reaction("🗑️")
            await msg.add_reaction("❌")
        except Exception:
            pass

        def _check(reaction: discord.Reaction, user: discord.User) -> bool:
            return (
                user.id == ctx.author.id
                and reaction.message.id == msg.id
                and str(reaction.emoji) in ("📦", "🗑️", "❌")
            )

        choice_emoji = None
        try:
            reaction, _user = await self.bot.wait_for("reaction_add", timeout=45.0, check=_check)
            choice_emoji = str(reaction.emoji)
        except Exception:
            choice_emoji = "❌"

        if choice_emoji == "📦":
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.archive_habit, int(resolved_guild_id), ctx.author.id, int(habit_id))
            out = f"📦 Removed habit **#{habit_id}** (kept stats). You can still view: `/habit_stats {habit_id}`." if ok else "Could not remove that habit."
        elif choice_emoji == "🗑️":
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.purge_habit, int(resolved_guild_id), ctx.author.id, int(habit_id))
            out = f"🗑️ Removed habit **#{habit_id}** and deleted its stats/history." if ok else "Could not remove that habit."
        else:
            out = f"❌ Cancelled removing habit **#{habit_id}**."

        try:
            await msg.edit(content=out)
        except Exception:
            await ctx.send(out)
        return

    @commands.hybrid_command(name="habit_remind", description="Enable/disable reminders for a habit.")
    @discord.app_commands.describe(habit_id="The numeric id (from /habit_list).", enabled="Enable reminders (default: True).")
    async def habit_remind(self, ctx: commands.Context, habit_id: int, enabled: bool = True):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return
        if is_dm and hasattr(self.db_manager, "set_habit_reminder_enabled_any_scope"):
            ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_habit_reminder_enabled_any_scope, ctx.author.id, int(habit_id), enabled)
        else:
            guild_id = _scope_guild_id_from_ctx(ctx)
            ok = await self.bot.loop.run_in_executor(
                None, self.db_manager.set_habit_reminder_enabled, guild_id, ctx.author.id, int(habit_id), enabled
            )
        if not ok:
            await self.send_response(ctx, "Could not update that habit.", ephemeral=not is_dm)
            return
        await self.send_response(ctx, ("🔔 Reminders enabled." if enabled else "🔕 Reminders disabled."), ephemeral=not is_dm)

    @commands.hybrid_command(
        name="habit_remind_profile",
        description="Set habit reminders: default is catch-up; nagging is opt-in.",
    )
    @discord.app_commands.describe(
        habit_id="The numeric id (from /habit_list).",
        profile="catchup | nag_gentle | nag_normal | nag_aggressive | nag_daily",
    )
    async def habit_remind_profile(self, ctx: commands.Context, habit_id: int, profile: str = "catchup"):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        if is_dm and hasattr(self.db_manager, "set_habit_reminder_profile_any_scope"):
            ok = await self.bot.loop.run_in_executor(
                None, self.db_manager.set_habit_reminder_profile_any_scope, ctx.author.id, int(habit_id), profile
            )
        else:
            guild_id = _scope_guild_id_from_ctx(ctx)
            ok = await self.bot.loop.run_in_executor(
                None, self.db_manager.set_habit_reminder_profile, guild_id, ctx.author.id, int(habit_id), profile
            )

        if not ok:
            await self.send_response(ctx, "Could not update that habit (or it’s not yours).", ephemeral=not is_dm)
            return

        # Accept aliases; DB layer normalizes (back-compat for old names like "normal", "aggressive", "digest").
        p_raw = str(profile or "catchup").strip().lower()
        allowed = _HABIT_REMIND_PROFILES
        aliases = set(_HABIT_REMIND_PROFILE_ALIASES.keys())
        # If the user uses an alias, let it through; DB will normalize. Otherwise: fall back to catchup.
        if p_raw not in allowed and p_raw not in aliases:
            p_raw = "catchup"

        # Prefer DB normalization (single source of truth); fall back to local normalization for safety.
        normalizer = getattr(self.db_manager, "_normalize_habit_remind_profile", None)
        p_effective = normalizer(p_raw) if callable(normalizer) else _normalize_habit_remind_profile(p_raw)

        await self.send_response(ctx, f"✅ Reminder mode set to **{p_effective}** for habit **#{habit_id}**.", ephemeral=not is_dm)

    @commands.hybrid_command(
        name="habit_snooze",
        description="Snooze a habit for 1 day (can be limited to once per week or month).",
    )
    @discord.app_commands.describe(
        habit_id="The numeric id (from /habit_list).",
        period="Cooldown: week | month (default: week)",
    )
    async def habit_snooze(self, ctx: commands.Context, habit_id: int, period: str = "week"):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        # Load habit (for timezone display)
        guild_id = _scope_guild_id_from_ctx(ctx)
        habit = None
        if is_dm and hasattr(self.db_manager, "get_habit_any_scope"):
            habit = await self.bot.loop.run_in_executor(None, self.db_manager.get_habit_any_scope, ctx.author.id, int(habit_id))
            if habit and "guild_id" in habit:
                try:
                    guild_id = int(habit.get("guild_id") or 0)
                except Exception:
                    guild_id = 0
        else:
            habit = await self.bot.loop.run_in_executor(None, self.db_manager.get_habit, guild_id, ctx.author.id, int(habit_id))

        tz_name = str((habit or {}).get("tz_name") or "Europe/Warsaw")

        now = _utc_now()
        now_s = _sqlite_utc_timestamp(now)

        if is_dm and hasattr(self.db_manager, "snooze_habit_for_day_any_scope"):
            res = await self.bot.loop.run_in_executor(
                None,
                self.db_manager.snooze_habit_for_day_any_scope,
                ctx.author.id,
                int(habit_id),
                now_s,
                period,
                1,
            )
        else:
            res = await self.bot.loop.run_in_executor(
                None,
                self.db_manager.snooze_habit_for_day,
                guild_id,
                ctx.author.id,
                int(habit_id),
                now_s,
                period,
                1,
            )

        if not isinstance(res, dict) or not res.get("ok"):
            if isinstance(res, dict) and res.get("error") == "cooldown":
                next_allowed = res.get("next_allowed_at")
                eff = res.get("effective_period") or str(period or "week")
                next_allowed_disp = str(next_allowed or "n/a")
                if isinstance(next_allowed, str) and next_allowed.strip():
                    dt_na = _parse_sqlite_utc_timestamp(next_allowed)
                    if dt_na is not None:
                        local_na, tz_lbl = _format_due_display(dt_na, tz_name)
                        next_allowed_disp = f"{local_na} {tz_lbl}"
                await self.send_response(
                    ctx,
                    f"⏳ You can only snooze this habit **once per {eff}**. Next snooze available at `{next_allowed_disp}`.",
                    ephemeral=not is_dm,
                )
                return
            await self.send_response(ctx, "Could not snooze that habit (or it’s not yours).", ephemeral=not is_dm)
            return

        until_s = res.get("snoozed_until") or _sqlite_utc_timestamp(now + timedelta(days=1))
        until_disp = str(until_s or "n/a")
        if isinstance(until_s, str) and until_s.strip():
            dt_until = _parse_sqlite_utc_timestamp(until_s)
            if dt_until is not None:
                local_until, tz_lbl = _format_due_display(dt_until, tz_name)
                until_disp = f"{local_until} {tz_lbl}"
        await self.send_response(
            ctx,
            f"😴 Snoozed habit **#{habit_id}**. Reminders will resume after `{until_disp}`.",
            ephemeral=not is_dm,
        )

    # -------------------------
    # Habit catch-up (DM)
    # -------------------------
    @tasks.loop(minutes=5)
    async def habit_catchup_loop(self):
        """
        Next-day catch-up for habits with remind_profile='catchup'.

        Sends once per day per user (after a configured local time, default 09:00 in user's timezone):
        "Did you complete these habits yesterday?"

        This avoids repeated/nagging reminders and lets users confirm yesterday's completion while keeping stats accurate.
        """
        if not self.db_manager:
            return

        now_utc = _utc_now()

        # Users with any productivity data.
        user_ids = await self.bot.loop.run_in_executor(None, self.db_manager.list_users_with_productivity_data)
        for uid in user_ids or []:
            try:
                uid_i = int(uid)
            except Exception:
                continue

            # Respect DND
            try:
                if await self._is_user_in_dnd(uid_i):
                    continue
            except Exception:
                pass

            # User timezone for "send time" gating + once-per-day tracking.
            tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid_i, "timezone", "Europe/Warsaw")
            tz = _tzinfo_from_name(str(tz_name or "Europe/Warsaw"))
            now_local = now_utc.astimezone(tz)
            today_iso = now_local.date().isoformat()

            last_day = await self.bot.loop.run_in_executor(
                None, self.db_manager.get_user_preference, uid_i, "habit_catchup_last_sent_day", ""
            )
            # Backwards-compatible fallback (pre-rename)
            if not last_day:
                last_day = await self.bot.loop.run_in_executor(
                    None, self.db_manager.get_user_preference, uid_i, "habit_digest_last_sent_day", ""
                )
            if isinstance(last_day, str) and last_day == today_iso:
                continue

            # After this local time, we send the catch-up.
            digest_time = await self.bot.loop.run_in_executor(
                None, self.db_manager.get_user_preference, uid_i, "habit_catchup_time", "09:00"
            )
            if not digest_time:
                digest_time = await self.bot.loop.run_in_executor(
                    None, self.db_manager.get_user_preference, uid_i, "habit_digest_time", "09:00"
                )
            try:
                dtm = datetime.strptime(str(digest_time), "%H:%M").time()
            except Exception:
                dtm = dtime(9, 0)
            if (now_local.hour * 60 + now_local.minute) < (dtm.hour * 60 + dtm.minute):
                continue

            habits = await self.bot.loop.run_in_executor(None, self.db_manager.list_habits_any_scope, uid_i, 200)
            digest_habits = []
            for h in habits or []:
                try:
                    p = _normalize_habit_remind_profile(h.get("remind_profile"))
                except Exception:
                    p = "catchup"
                if p == "catchup" and bool(h.get("remind_enabled", True)):
                    digest_habits.append(h)
            if not digest_habits:
                continue

            missed: list[dict] = []
            for h in digest_habits[:200]:
                try:
                    hid = int(h.get("id"))
                except Exception:
                    continue
                try:
                    gid = int(h.get("guild_id") or 0)
                except Exception:
                    gid = 0

                htz = _tzinfo_from_name(str(h.get("tz_name") or tz_name or "UTC"))
                now_hlocal = now_utc.astimezone(htz)
                yday = now_hlocal.date() - timedelta(days=1)

                # Skip if habit didn't exist yet yesterday (local).
                created_dt = _parse_sqlite_utc_timestamp(h.get("created_at"))
                if created_dt is not None:
                    if created_dt.astimezone(htz).date() > yday:
                        continue

                # Was it scheduled yesterday?
                try:
                    days_list = json.loads(h.get("days_of_week") or "[]")
                    days_set = {int(x) for x in (days_list or []) if 0 <= int(x) <= 6}
                except Exception:
                    days_set = {0, 1, 2, 3, 4}
                if yday.weekday() not in days_set:
                    continue

                # Check if there is any check-in on that local date.
                since_local = datetime.combine(yday, dtime(0, 0)).replace(tzinfo=htz)
                since_utc_s = _sqlite_utc_timestamp(since_local.astimezone(timezone.utc) - timedelta(days=2))
                checkins = await self.bot.loop.run_in_executor(
                    None, self.db_manager.list_habit_checkins, gid, uid_i, hid, since_utc_s, 5000
                )
                completed = False
                for r in checkins or []:
                    dt_utc = _parse_sqlite_utc_timestamp(r.get("checked_in_at") if isinstance(r, dict) else None)
                    if dt_utc is None:
                        continue
                    if dt_utc.astimezone(htz).date() == yday:
                        completed = True
                        break
                if completed:
                    continue

                # Precompute a backdated timestamp (at the habit's due time) for accurate stats.
                due_local_str = str(h.get("due_time_local") or h.get("due_time_utc") or "18:00")
                due_local = _parse_hhmm_local(due_local_str) or dtime(18, 0)
                checked_local = datetime.combine(yday, due_local).replace(tzinfo=htz)
                checked_utc_s = _sqlite_utc_timestamp(checked_local.astimezone(timezone.utc))

                missed.append(
                    {
                        "habit_id": hid,
                        "guild_id": gid,
                        "name": str(h.get("name") or "Habit"),
                        "tz_name": str(h.get("tz_name") or "UTC"),
                        "yday": yday.isoformat(),
                        "checked_in_at_utc": checked_utc_s,
                        "days_of_week": h.get("days_of_week"),
                        "due_time_local": due_local_str,
                    }
                )

            if not missed:
                # Still mark as sent so we don't spam if the user has only catch-up habits but none were scheduled yesterday.
                await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid_i, "habit_catchup_last_sent_day", today_iso)
                continue

            # Create a small button UI (up to 10) to confirm yesterday's completion.
            user = self.bot.get_user(uid_i)
            if not user:
                try:
                    user = await self.bot.fetch_user(uid_i)
                except (discord.NotFound, discord.HTTPException):
                    continue

            title_day = (now_local.date() - timedelta(days=1)).isoformat()
            msg_lines = [f"🧾 Habit catch-up for **{title_day}**:"]
            for it in missed[:10]:
                msg_lines.append(f"- **{it['name']}** (#{it['habit_id']})")
            if len(missed) > 10:
                msg_lines.append(f"...and **{len(missed) - 10}** more. (Use `/habit_checkin <id>` manually.)")
            msg_lines.append("\nTap a button to mark it done for yesterday (this will backdate the check-in so stats stay accurate).")
            content = "\n".join(msg_lines)

            class HabitDigestView(discord.ui.View):
                def __init__(self, items: list[dict], timeout: int = 120):
                    super().__init__(timeout=timeout)
                    self.items = items[:10]
                    for it in self.items:
                        hid2 = int(it["habit_id"])
                        label = f"✅ #{hid2}"
                        btn = discord.ui.Button(style=discord.ButtonStyle.success, label=label)

                        async def _cb(interaction: discord.Interaction, _it=it):
                            if interaction.user.id != uid_i:
                                await interaction.response.send_message("This isn't for you.", ephemeral=True)
                                return

                            # Compute next due (from now) using the stored schedule.
                            try:
                                days_list2 = json.loads(_it.get("days_of_week") or "[]")
                            except Exception:
                                days_list2 = [0, 1, 2, 3, 4]
                            htz2 = _tzinfo_from_name(_it.get("tz_name"))
                            due_local_str2 = str(_it.get("due_time_local") or "18:00")
                            if htz2 == timezone.utc:
                                due_utc2 = _parse_hhmm_utc(due_local_str2) or dtime(18, 0, tzinfo=timezone.utc)
                                next_due2 = _next_due_datetime_utc(_utc_now(), days_list2, due_utc2)
                            else:
                                due_local2 = _parse_hhmm_local(due_local_str2) or dtime(18, 0)
                                next_due2 = _next_due_datetime_cet_to_utc(_utc_now(), days_list2, due_local2, htz2)

                            ok2 = await self.bot.loop.run_in_executor(
                                None,
                                self.db_manager.record_habit_checkin,
                                int(_it.get("guild_id") or 0),
                                uid_i,
                                int(_it.get("habit_id")),
                                "catch-up",
                                _sqlite_utc_timestamp(next_due2),
                                str(_it.get("checked_in_at_utc") or ""),
                            )
                            if ok2:
                                btn.disabled = True
                                try:
                                    await interaction.response.edit_message(view=self)
                                except Exception:
                                    try:
                                        await interaction.response.send_message("✅ Saved.", ephemeral=True)
                                    except Exception:
                                        pass
                            else:
                                try:
                                    await interaction.response.send_message("❌ Could not save that check-in.", ephemeral=True)
                                except Exception:
                                    pass

                        btn.callback = _cb  # type: ignore[assignment]
                        self.add_item(btn)

            view = HabitDigestView(missed)
            try:
                await user.send(content=content, view=view)
            except discord.Forbidden:
                continue
            except discord.HTTPException:
                continue

            await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid_i, "habit_catchup_last_sent_day", today_iso)

    @habit_catchup_loop.before_loop
    async def before_habit_catchup_loop(self):
        await self.bot.wait_until_ready()

    # -------------------------
    # Reminder loop (DM)
    # -------------------------
    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        if not self.db_manager:
            return

        now = _utc_now()
        now_str = _sqlite_utc_timestamp(now)

        MAX_NAG_SENDS = 5
        MAX_NAG_LEVEL = MAX_NAG_SENDS - 1  # level starts at 0

        habit_messages = [
            "⏰ Habit reminder: **{name}** (id #{hid}) is due.\nCheck in with `/habit_checkin {hid}`",
            "📌 Quick nudge: **{name}** is still due (#{hid}).\nLog it with `/habit_checkin {hid}`",
            "🧠 Future-you says hi: time for **{name}** (#{hid}).\n`/habit_checkin {hid}`",
            "🔥 Keep the streak alive: **{name}** (#{hid}) is due.\n`/habit_checkin {hid}`",
            "✅ Small step time: **{name}** (#{hid}).\nCheck in: `/habit_checkin {hid}`",
        ]
        todo_messages = [
            "🔔 To‑do reminder: **#{tid}** — {content}\nMark done with `/todo_done {tid}` or disable with `/todo_nag {tid} enabled:false`.",
            "🧾 Still open: **#{tid}** — {content}\nDone? `/todo_done {tid}` • Stop nags: `/todo_nag {tid} enabled:false`",
            "⏳ Gentle ping: **#{tid}** — {content}\n`/todo_done {tid}` when it’s done.",
            "🎯 Focus moment: **#{tid}** — {content}\nClose it: `/todo_done {tid}`",
            "🧩 One more step: **#{tid}** — {content}\nDone? `/todo_done {tid}`",
        ]

        # Habits first
        due_habits = await self.bot.loop.run_in_executor(None, self.db_manager.list_due_habit_reminders, now_str, 50)
        for h in due_habits or []:
            try:
                uid = int(h.get("user_id"))

                # If the stored due pointer is stale (schedule has already moved on),
                # advance it and skip sending an outdated reminder.
                try:
                    if hasattr(self.db_manager, "refresh_stale_habit_due_date_any_scope"):
                        advanced = await self.bot.loop.run_in_executor(
                            None,
                            lambda: self.db_manager.refresh_stale_habit_due_date_any_scope(
                                uid, int(h.get("id") or 0), now_utc=now_str
                            ),
                        )
                        if advanced:
                            continue
                except Exception:
                    pass

                if await self._is_user_in_dnd(uid):
                    # Avoid reprocessing every minute while in DND.
                    next_rem = _sqlite_utc_timestamp(now + timedelta(minutes=30))
                    await self.bot.loop.run_in_executor(
                        None,
                        self.db_manager.bump_habit_reminder,
                        int(h.get("guild_id") or 0),
                        uid,
                        int(h.get("id") or 0),
                        int(h.get("remind_level") or 0),
                        next_rem,
                    )
                    continue

                hid = h.get("id")
                name = h.get("name") or "Habit"
                profile = h.get("remind_profile") or "catchup"
                profile_n = _normalize_habit_remind_profile(profile)
                level = int(h.get("remind_level") or 0)

                # Catch-up mode: don't nag; just back off so we don't re-process every minute.
                if profile_n == "catchup":
                    next_rem = _sqlite_utc_timestamp(now + timedelta(days=1))
                    await self.bot.loop.run_in_executor(
                        None,
                        self.db_manager.bump_habit_reminder,
                        int(h.get("guild_id") or 0),
                        uid,
                        int(hid),
                        0,
                        next_rem,
                    )
                    continue

                # Anti-spam: after N nags, stop (user can re-enable).
                if level >= MAX_NAG_LEVEL:
                    await self.bot.loop.run_in_executor(
                        None,
                        self.db_manager.set_habit_reminder_enabled,
                        int(h.get("guild_id") or 0),
                        uid,
                        int(hid),
                        False,
                    )
                    continue

                tpl = habit_messages[level % len(habit_messages)]
                sent = await self._dm_user(
                    uid,
                    content=tpl.format(name=name, hid=hid),
                )
                if not sent:
                    # If DM fails, don't spin; back off a bit.
                    next_rem = _sqlite_utc_timestamp(now + timedelta(hours=12))
                    await self.bot.loop.run_in_executor(
                        None,
                        self.db_manager.bump_habit_reminder,
                        int(h.get("guild_id") or 0),
                        uid,
                        int(hid),
                        level,
                        next_rem,
                    )
                    continue

                next_minutes = _escalation_interval_minutes(level + 1, profile_n)
                next_rem = _sqlite_utc_timestamp(now + timedelta(minutes=next_minutes))
                await self.bot.loop.run_in_executor(
                    None,
                    self.db_manager.bump_habit_reminder,
                    int(h.get("guild_id") or 0),
                    uid,
                    int(hid),
                    level + 1,
                    next_rem,
                )
            except Exception as e:
                logger.warning(f"reminder_loop habit error: {e}")

        # To-dos
        due_todos = await self.bot.loop.run_in_executor(None, self.db_manager.list_due_todo_reminders, now_str, 50)
        for t in due_todos or []:
            try:
                uid = int(t.get("user_id"))
                if await self._is_user_in_dnd(uid):
                    # Avoid reprocessing every minute while in DND.
                    next_rem = _sqlite_utc_timestamp(now + timedelta(minutes=30))
                    await self.bot.loop.run_in_executor(
                        None,
                        self.db_manager.bump_todo_reminder,
                        int(t.get("guild_id") or 0),
                        uid,
                        int(t.get("id") or 0),
                        int(t.get("remind_level") or 0),
                        next_rem,
                    )
                    continue

                tid = t.get("id")
                content = t.get("content") or "To-do"
                level = int(t.get("remind_level") or 0)

                # Anti-spam: after N nags, stop (user can re-enable).
                if level >= MAX_NAG_LEVEL:
                    await self.bot.loop.run_in_executor(
                        None,
                        self.db_manager.set_todo_reminder,
                        int(t.get("guild_id") or 0),
                        uid,
                        int(tid),
                        False,
                        None,
                    )
                    continue

                tpl = todo_messages[level % len(todo_messages)]
                sent = await self._dm_user(
                    uid,
                    content=tpl.format(tid=tid, content=content),
                )
                if not sent:
                    next_rem = _sqlite_utc_timestamp(now + timedelta(hours=12))
                    await self.bot.loop.run_in_executor(
                        None,
                        self.db_manager.bump_todo_reminder,
                        int(t.get("guild_id") or 0),
                        uid,
                        int(tid),
                        level,
                        next_rem,
                    )
                    continue

                next_minutes = _escalation_interval_minutes(level + 1, "normal")
                next_rem = _sqlite_utc_timestamp(now + timedelta(minutes=next_minutes))
                await self.bot.loop.run_in_executor(
                    None,
                    self.db_manager.bump_todo_reminder,
                    int(t.get("guild_id") or 0),
                    uid,
                    int(tid),
                    level + 1,
                    next_rem,
                )
            except Exception as e:
                logger.warning(f"reminder_loop todo error: {e}")

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ProductivityCog(bot, db_manager=getattr(bot, "db_manager", None)))
    logger.info("ProductivityCog has been loaded.")



