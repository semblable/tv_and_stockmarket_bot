import logging
import re
from datetime import datetime, timedelta, timezone, time as dtime
import json
from typing import List, Optional

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)


def _scope_guild_id_from_ctx(ctx: commands.Context) -> int:
    return ctx.guild.id if ctx.guild else 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sqlite_utc_timestamp(dt: datetime) -> str:
    # SQLite CURRENT_TIMESTAMP uses "YYYY-MM-DD HH:MM:SS" in UTC.
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


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
      - "1,2,3" (Mon=1 .. Sun=7) or "0..6" (Mon=0 .. Sun=6)
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


def _escalation_interval_minutes(level: int) -> int:
    """
    Increasing frequency as level grows.
    level starts at 0.
    """
    schedule = [240, 120, 60, 30, 15]  # 4h, 2h, 1h, 30m, 15m
    idx = max(0, min(len(schedule) - 1, int(level)))
    return schedule[idx]


class ProductivityCog(commands.Cog, name="Productivity"):
    def __init__(self, bot: commands.Bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager

    async def cog_load(self):
        self.reminder_loop.start()
        logger.info("ProductivityCog loaded and reminder loop started.")

    async def cog_unload(self):
        self.reminder_loop.cancel()
        logger.info("ProductivityCog unloaded and reminder loop cancelled.")

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
    ):
        if ctx.interaction:
            kwargs = {"ephemeral": ephemeral}
            if embed is not None:
                kwargs["embed"] = embed
            if content is not None:
                kwargs["content"] = content
            try:
                if not ctx.interaction.response.is_done():
                    if wait:
                        return await ctx.interaction.followup.send(**kwargs, wait=True)
                    return await ctx.interaction.response.send_message(**kwargs)
            except discord.HTTPException:
                pass
            if wait:
                return await ctx.interaction.followup.send(**kwargs, wait=True)
            return await ctx.interaction.followup.send(**kwargs)

        kwargs2 = {}
        if content is not None:
            kwargs2["content"] = content
        if embed is not None:
            kwargs2["embed"] = embed
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
            if start_t <= end_t:
                return start_t <= now_t <= end_t
            return now_t >= start_t or now_t <= end_t
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

        guild_id = _scope_guild_id_from_ctx(ctx)
        items = await self.bot.loop.run_in_executor(None, self.db_manager.list_todo_items, guild_id, ctx.author.id, include_done, 50)
        if not items:
            await self.send_response(ctx, "Your to-do list is empty. Use `/todo_add`.", ephemeral=not is_dm)
            return

        embed = discord.Embed(title="✅ Your To‑Dos", color=discord.Color.blurple())
        lines: List[str] = []
        for r in items[:50]:
            tid = r.get("id")
            content = r.get("content") or ""
            done = bool(r.get("is_done"))
            prefix = "☑️" if done else "⬜"
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
        guild_id = _scope_guild_id_from_ctx(ctx)
        ok = await self.bot.loop.run_in_executor(None, self.db_manager.delete_todo_item, guild_id, ctx.author.id, int(todo_id))
        if not ok:
            await self.send_response(ctx, "Could not find that to-do (or it’s not yours).", ephemeral=not is_dm)
            return
        await self.send_response(ctx, f"🗑️ Removed **#{todo_id}**.", ephemeral=not is_dm)

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
        initial_minutes = max(5, min(7 * 24 * 60, int(initial_minutes)))
        guild_id = _scope_guild_id_from_ctx(ctx)
        next_remind = None
        if enabled:
            next_remind = _sqlite_utc_timestamp(_utc_now() + timedelta(minutes=initial_minutes))
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
        days="Schedule days (e.g. 'mon-fri', 'weekdays', 'mon,wed,fri').",
        due_time_utc="Due time in UTC (HH:MM, default 18:00).",
        remind="Whether reminders are enabled (default: True).",
    )
    async def habit_add(self, ctx: commands.Context, name: str, days: str = "mon-fri", due_time_utc: str = "18:00", remind: bool = True):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        due_t = _parse_hhmm_utc(due_time_utc) or dtime(18, 0, tzinfo=timezone.utc)
        days_list = _parse_days_spec(days)
        next_due = _next_due_datetime_utc(_utc_now(), days_list, due_t)

        guild_id = _scope_guild_id_from_ctx(ctx)
        habit_id = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.create_habit,
            guild_id,
            ctx.author.id,
            name,
            days_list,
            due_t.strftime("%H:%M"),
            remind,
            _sqlite_utc_timestamp(next_due),
        )
        if not habit_id:
            await self.send_response(ctx, "Could not create that habit.", ephemeral=not is_dm)
            return

        await self.send_response(
            ctx,
            f"✅ Created habit **#{habit_id}**: **{name}** on `{days}` due `{due_t.strftime('%H:%M')} UTC`.",
            ephemeral=not is_dm,
        )

    @commands.hybrid_command(name="habit_list", description="List your habits.")
    async def habit_list(self, ctx: commands.Context):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        guild_id = _scope_guild_id_from_ctx(ctx)
        habits = await self.bot.loop.run_in_executor(None, self.db_manager.list_habits, guild_id, ctx.author.id, 50)
        if not habits:
            await self.send_response(ctx, "No habits yet. Use `/habit_add`.", ephemeral=not is_dm)
            return

        embed = discord.Embed(title="📌 Your Habits", color=discord.Color.green())
        lines: List[str] = []
        for h in habits[:50]:
            hid = h.get("id")
            name = h.get("name") or ""
            due_time = h.get("due_time_utc") or "18:00"
            remind_enabled = bool(h.get("remind_enabled"))
            next_due = h.get("next_due_at")
            last = h.get("last_checkin_at")
            rflag = "🔔" if remind_enabled else "🔕"
            lines.append(
                f"{rflag} **#{hid}** — **{name}** (due `{due_time} UTC`)\n"
                f"• next due: `{next_due or 'n/a'}` | last check-in: `{last or 'n/a'}`"
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
        due_t = _parse_hhmm_utc(str(habit.get("due_time_utc") or "18:00")) or dtime(18, 0, tzinfo=timezone.utc)
        next_due = _next_due_datetime_utc(_utc_now(), days_list, due_t)

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
        await self.send_response(ctx, f"✅ Check-in saved for **#{habit_id}**. Next due: `{_sqlite_utc_timestamp(next_due)}`.", ephemeral=not is_dm)

    @commands.hybrid_command(name="habit_remove", description="Remove a habit.")
    @discord.app_commands.describe(habit_id="The numeric id (from /habit_list).")
    async def habit_remove(self, ctx: commands.Context, habit_id: int):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        guild_id = _scope_guild_id_from_ctx(ctx)
        ok = await self.bot.loop.run_in_executor(None, self.db_manager.delete_habit, guild_id, ctx.author.id, int(habit_id))
        if not ok:
            await self.send_response(ctx, "Could not find that habit (or it’s not yours).", ephemeral=not is_dm)
            return
        await self.send_response(ctx, f"🗑️ Removed habit **#{habit_id}**.", ephemeral=not is_dm)

    @commands.hybrid_command(name="habit_remind", description="Enable/disable reminders for a habit.")
    @discord.app_commands.describe(habit_id="The numeric id (from /habit_list).", enabled="Enable reminders (default: True).")
    async def habit_remind(self, ctx: commands.Context, habit_id: int, enabled: bool = True):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return
        guild_id = _scope_guild_id_from_ctx(ctx)
        ok = await self.bot.loop.run_in_executor(
            None, self.db_manager.set_habit_reminder_enabled, guild_id, ctx.author.id, int(habit_id), enabled
        )
        if not ok:
            await self.send_response(ctx, "Could not update that habit.", ephemeral=not is_dm)
            return
        await self.send_response(ctx, ("🔔 Reminders enabled." if enabled else "🔕 Reminders disabled."), ephemeral=not is_dm)

    # -------------------------
    # Reminder loop (DM)
    # -------------------------
    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        if not self.db_manager:
            return

        now = _utc_now()
        now_str = _sqlite_utc_timestamp(now)

        # Habits first
        due_habits = await self.bot.loop.run_in_executor(None, self.db_manager.list_due_habit_reminders, now_str, 50)
        for h in due_habits or []:
            try:
                uid = int(h.get("user_id"))
                if await self._is_user_in_dnd(uid):
                    continue

                hid = h.get("id")
                name = h.get("name") or "Habit"
                level = int(h.get("remind_level") or 0)

                sent = await self._dm_user(
                    uid,
                    content=f"⏰ Habit reminder: **{name}** (id #{hid}) is due.\n"
                            f"Check in with `/habit_checkin {hid}`",
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

                next_minutes = _escalation_interval_minutes(level + 1)
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
                    continue

                tid = t.get("id")
                content = t.get("content") or "To-do"
                level = int(t.get("remind_level") or 0)

                sent = await self._dm_user(
                    uid,
                    content=f"🔔 To‑do reminder: **#{tid}** — {content}\n"
                            f"Mark done with `/todo_done {tid}` or disable with `/todo_nag {tid} enabled:false`",
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

                next_minutes = _escalation_interval_minutes(level + 1)
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

