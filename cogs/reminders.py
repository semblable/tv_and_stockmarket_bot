import logging
import re
from datetime import datetime, timedelta, timezone, time as dtime
from functools import partial
from typing import Optional, Tuple

import discord
from discord.ext import commands, tasks

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)

MIN_REMINDER_SPACING = timedelta(minutes=30)
MAX_REPEAT_SENDS = 5
MAX_BATCH_PER_SEND = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sqlite_utc_timestamp(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _tzinfo_from_name(tz_name: Optional[str]):
    name = (tz_name or "").strip()
    if not name:
        # Default to CET/CEST
        if ZoneInfo is not None:
            try:
                return ZoneInfo("Europe/Warsaw")
            except Exception:
                return timezone(timedelta(hours=1), name="CET")
        return timezone(timedelta(hours=1), name="CET")
    if name.upper() in ("UTC", "ETC/UTC", "Z"):
        return timezone.utc
    if name.upper() in ("CET", "CEST"):
        if ZoneInfo is not None:
            try:
                return ZoneInfo("Europe/Warsaw")
            except Exception:
                return timezone(timedelta(hours=1), name="CET")
        return timezone(timedelta(hours=1), name="CET")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            return timezone.utc
    # Without zoneinfo data, best-effort fallback is UTC.
    return timezone.utc


def _parse_sqlite_utc_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        dt = datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _format_local(dt_utc: datetime, tz_name: Optional[str]) -> tuple[str, str]:
    tz = _tzinfo_from_name(tz_name)
    label = (str(tz_name or "Europe/Warsaw") or "").strip()
    if label in ("Europe/Warsaw", "CET", "CEST"):
        label = "CET/CEST"
    return (dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M"), label)


def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (s or "").strip())
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def _parse_duration_seconds(spec: str) -> Optional[int]:
    """
    Parses durations like:
      - 10m, 2h, 3d, 1w, 45s
      - 1h30m, 2d 4h, etc.
    """
    s = (spec or "").strip().lower().replace(" ", "")
    if not s:
        return None

    token_re = re.compile(r"(\d+)(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)")
    pos = 0
    total = 0
    for m in token_re.finditer(s):
        if m.start() != pos:
            return None
        n = int(m.group(1))
        unit = m.group(2)
        if unit in ("s", "sec", "secs", "second", "seconds"):
            total += n
        elif unit in ("m", "min", "mins", "minute", "minutes"):
            total += n * 60
        elif unit in ("h", "hr", "hrs", "hour", "hours"):
            total += n * 3600
        elif unit in ("d", "day", "days"):
            total += n * 86400
        elif unit in ("w", "week", "weeks"):
            total += n * 7 * 86400
        pos = m.end()

    if pos != len(s):
        return None
    if total <= 0:
        return None
    # Safety cap: 10 years
    return min(total, 10 * 365 * 86400)


def _parse_when_to_utc(when: str, tz) -> Optional[datetime]:
    """
    Accepts:
      - "YYYY-MM-DD HH:MM" or "YYYY-MM-DDTHH:MM"
      - "HH:MM" (next occurrence in tz)
    Returns aware UTC datetime.
    """
    s = (when or "").strip()
    if not s:
        return None

    # Full datetime
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})", s)
    if m:
        y, mo, d, hh, mm = map(int, m.groups())
        try:
            local = datetime(y, mo, d, hh, mm, tzinfo=tz)
        except Exception:
            return None
        return local.astimezone(timezone.utc)

    # Only time => next occurrence
    hm = _parse_hhmm(s)
    if hm:
        hh, mm = hm
        now_local = _utc_now().astimezone(tz)
        cand = datetime.combine(now_local.date(), dtime(hh, mm)).replace(tzinfo=tz)
        if cand <= now_local:
            cand = cand + timedelta(days=1)
        return cand.astimezone(timezone.utc)

    return None


class RemindersCog(commands.Cog, name="Reminders"):
    def __init__(self, bot: commands.Bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager
        # Best-effort in-memory throttle (persisted fallback is in user_preferences).
        self._last_sent_by_user: dict[int, datetime] = {}

    async def cog_load(self):
        self.reminder_loop.start()
        logger.info("RemindersCog loaded and reminder loop started.")

    async def cog_unload(self):
        self.reminder_loop.cancel()
        logger.info("RemindersCog unloaded and reminder loop cancelled.")

    async def _is_user_in_dnd(self, user_id: int) -> bool:
        """
        Best-effort DND check using settings keys.
        Uses local machine time (same as other cogs).
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
            # This avoids surprises like DND 22:00-07:00 suppressing notifications at exactly 07:00.
            if start_t == end_t:
                return False
            if start_t < end_t:
                return start_t <= now_t < end_t
            return now_t >= start_t or now_t < end_t
        except Exception:
            return False

    async def _send_reminder(self, *, user_id: int, guild_id: int, channel_id: int, message: str) -> bool:
        content = f"⏰ <@{user_id}> reminder: {message}"
        # DM scope or missing channel => DM user
        if int(channel_id or 0) == 0:
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
            try:
                await user.send(content=content)
                return True
            except Exception:
                return False

        ch = self.bot.get_channel(int(channel_id))
        if ch is None:
            # fall back to DM
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
            try:
                await user.send(content=content)
                return True
            except Exception:
                return False
        try:
            await ch.send(content=content)
            return True
        except Exception:
            return False

    async def _get_user_last_sent(self, user_id: int) -> Optional[datetime]:
        # Prefer in-memory for speed; fall back to persisted preference.
        dt = self._last_sent_by_user.get(int(user_id))
        if isinstance(dt, datetime):
            return dt
        if not self.db_manager:
            return None
        try:
            last_s = await self.bot.loop.run_in_executor(
                None,
                self.db_manager.get_user_preference,
                int(user_id),
                "generic_reminder_last_sent_at_utc",
                None,
            )
            if isinstance(last_s, str):
                return _parse_sqlite_utc_timestamp(last_s)
        except Exception:
            return None
        return None

    async def _set_user_last_sent(self, user_id: int, dt_utc: datetime) -> None:
        dt_utc = dt_utc.astimezone(timezone.utc)
        self._last_sent_by_user[int(user_id)] = dt_utc
        if not self.db_manager:
            return
        try:
            await self.bot.loop.run_in_executor(
                None,
                self.db_manager.set_user_preference,
                int(user_id),
                "generic_reminder_last_sent_at_utc",
                _sqlite_utc_timestamp(dt_utc),
            )
        except Exception:
            return

    @tasks.loop(seconds=30)
    async def reminder_loop(self):
        if not self.db_manager:
            return
        now = _utc_now()
        now_s = _sqlite_utc_timestamp(now)

        due = await self.bot.loop.run_in_executor(None, self.db_manager.list_due_reminders, now_s, 50)
        if not due:
            return

        # Group by user to apply 30min spacing globally.
        by_user: dict[int, list[dict]] = {}
        for r in due or []:
            try:
                uid = int(r.get("user_id"))
            except Exception:
                continue
            by_user.setdefault(uid, []).append(r)

        for uid, rows in by_user.items():
            try:
                # DND: don't spin every 30s; snooze a bit and retry later.
                if await self._is_user_in_dnd(uid):
                    snooze_to = _sqlite_utc_timestamp(now + MIN_REMINDER_SPACING)
                    for r in rows[:50]:
                        try:
                            rid = int(r.get("id"))
                            await self.bot.loop.run_in_executor(
                                None, partial(self.db_manager.snooze_reminder, rid, next_trigger_at_utc=snooze_to)
                            )
                        except Exception:
                            continue
                    continue

                last_sent = await self._get_user_last_sent(uid)
                if last_sent is not None and (now - last_sent) < MIN_REMINDER_SPACING:
                    # Too soon: postpone all due reminders for this user a bit.
                    snooze_to = _sqlite_utc_timestamp(last_sent + MIN_REMINDER_SPACING)
                    for r in rows[:50]:
                        try:
                            rid = int(r.get("id"))
                            await self.bot.loop.run_in_executor(
                                None, partial(self.db_manager.snooze_reminder, rid, next_trigger_at_utc=snooze_to)
                            )
                        except Exception:
                            continue
                    continue

                # Sort due reminders (oldest first), then choose a "destination" (channel/DM) to send as a batch.
                def _key(rr):
                    return str(rr.get("trigger_at") or ""), int(rr.get("id") or 0)

                rows_sorted = sorted(rows, key=_key)
                first = rows_sorted[0]
                try:
                    dest_cid = int(first.get("channel_id") or 0)
                except Exception:
                    dest_cid = 0
                try:
                    dest_gid = int(first.get("guild_id") or 0)
                except Exception:
                    dest_gid = 0

                # Prepare batch: same destination only, cap size, and enforce per-reminder repeat max.
                batch: list[dict] = []
                postpone: list[dict] = []
                for r in rows_sorted:
                    try:
                        cid = int(r.get("channel_id") or 0)
                    except Exception:
                        cid = 0
                    if cid != dest_cid:
                        postpone.append(r)
                        continue

                    # Stop repeating reminders after N sends.
                    rep = r.get("repeat_interval_seconds")
                    rep_s = int(rep) if rep is not None else 0
                    rc = 0
                    try:
                        rc = int(r.get("repeat_count") or 0)
                    except Exception:
                        rc = 0
                    if rep_s > 0 and rc >= MAX_REPEAT_SENDS:
                        try:
                            rid = int(r.get("id"))
                            await self.bot.loop.run_in_executor(None, self.db_manager.complete_oneoff_reminder, rid)
                        except Exception:
                            pass
                        continue

                    if len(batch) < MAX_BATCH_PER_SEND:
                        batch.append(r)
                    else:
                        postpone.append(r)

                # Postpone anything we didn't include in the batch, to respect global 30min spacing.
                if postpone:
                    snooze_to = _sqlite_utc_timestamp(now + MIN_REMINDER_SPACING)
                    for r in postpone[:50]:
                        try:
                            rid = int(r.get("id"))
                            await self.bot.loop.run_in_executor(
                                None, partial(self.db_manager.snooze_reminder, rid, next_trigger_at_utc=snooze_to)
                            )
                        except Exception:
                            continue

                if not batch:
                    continue

                # Compose message.
                if len(batch) == 1:
                    msg = str(batch[0].get("message") or "").strip()
                    sent = await self._send_reminder(user_id=uid, guild_id=dest_gid, channel_id=dest_cid, message=msg)
                else:
                    lines = []
                    for r in batch:
                        m = str(r.get("message") or "").strip()
                        if m:
                            lines.append(f"- {m}")
                    combined = "Multiple reminders due:\n" + ("\n".join(lines)[:1500] if lines else "(no messages)")
                    sent = await self._send_reminder(user_id=uid, guild_id=dest_gid, channel_id=dest_cid, message=combined)

                if not sent:
                    # If delivery fails, back off for 12h to avoid spinning.
                    backoff_s = _sqlite_utc_timestamp(now + timedelta(hours=12))
                    for r in batch:
                        try:
                            rid = int(r.get("id"))
                            await self.bot.loop.run_in_executor(
                                None, partial(self.db_manager.snooze_reminder, rid, next_trigger_at_utc=backoff_s)
                            )
                        except Exception:
                            continue
                    continue

                await self._set_user_last_sent(uid, now)

                # Update each reminder in the batch after successful send.
                for r in batch:
                    try:
                        rid = int(r.get("id"))
                        rep = r.get("repeat_interval_seconds")
                        rep_s = int(rep) if rep is not None else 0
                        rep_s_eff = max(rep_s, 30 * 60) if rep_s and rep_s > 0 else 0
                        if rep_s_eff and rep_s_eff > 0:
                            nxt = now + timedelta(seconds=rep_s_eff)
                            nxt_s = _sqlite_utc_timestamp(nxt)
                            await self.bot.loop.run_in_executor(
                                None, partial(self.db_manager.bump_reminder_after_send, rid, next_trigger_at_utc=nxt_s)
                            )
                        else:
                            await self.bot.loop.run_in_executor(None, self.db_manager.complete_oneoff_reminder, rid)
                    except Exception:
                        continue

            except Exception as e:
                logger.warning(f"reminder_loop error for user {uid}: {e}")

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="remind_in", description="Set a reminder after a time period (e.g. 10m, 2h, 1d).")
    async def remind_in(self, ctx: commands.Context, duration: str, *, message: str):
        if not self.db_manager:
            await ctx.send("Database is not available right now. Please try again later.")
            return
        seconds = _parse_duration_seconds(duration)
        if seconds is None:
            await ctx.send("❌ Invalid duration. Examples: `10m`, `2h`, `1d`, `1h30m`.")
            return

        now = _utc_now()
        trigger = now + timedelta(seconds=seconds)
        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        local_str, tz_label = _format_local(trigger, str(tz_name or "Europe/Warsaw"))
        guild_id = ctx.guild.id if ctx.guild else 0
        channel_id = ctx.channel.id if ctx.channel else 0
        if ctx.guild is None:
            channel_id = 0

        rid = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.create_reminder,
            guild_id,
            channel_id,
            ctx.author.id,
            message,
            _sqlite_utc_timestamp(trigger),
            None,
        )
        if not rid:
            await ctx.send("❌ Could not create that reminder.")
            return
        await ctx.send(f"✅ Reminder **#{rid}** set for `{duration}` from now (at `{local_str}` {tz_label}).")

    @commands.hybrid_command(name="remind_every", description="Set a repeating reminder (e.g. every 2h).")
    async def remind_every(self, ctx: commands.Context, interval: str, *, message: str):
        if not self.db_manager:
            await ctx.send("Database is not available right now. Please try again later.")
            return
        raw_seconds = _parse_duration_seconds(interval)
        if raw_seconds is None:
            await ctx.send("❌ Invalid interval. Examples: `30m`, `2h`, `1d`, `1h30m`.")
            return
        # Anti-spam: repeating reminders cannot be more frequent than every 30 minutes.
        seconds = max(int(raw_seconds), 30 * 60)

        now = _utc_now()
        trigger = now + timedelta(seconds=seconds)
        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        local_str, tz_label = _format_local(trigger, str(tz_name or "Europe/Warsaw"))
        guild_id = ctx.guild.id if ctx.guild else 0
        channel_id = ctx.channel.id if ctx.channel else 0
        if ctx.guild is None:
            channel_id = 0

        rid = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.create_reminder,
            guild_id,
            channel_id,
            ctx.author.id,
            message,
            _sqlite_utc_timestamp(trigger),
            int(seconds),
        )
        if not rid:
            await ctx.send("❌ Could not create that repeating reminder.")
            return
        if int(raw_seconds) < 30 * 60:
            await ctx.send(f"✅ Repeating reminder **#{rid}** set every **30m** (minimum). Next at `{local_str}` {tz_label}.")
        else:
            await ctx.send(f"✅ Repeating reminder **#{rid}** set every `{interval}` (next at `{local_str}` {tz_label}).")

    @commands.hybrid_command(name="remind_at", description="Set a reminder at a specific time (uses your saved timezone).")
    async def remind_at(self, ctx: commands.Context, when: str, *, message: str):
        if not self.db_manager:
            await ctx.send("Database is not available right now. Please try again later.")
            return

        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        tz = _tzinfo_from_name(str(tz_name or "Europe/Warsaw"))

        when_utc = _parse_when_to_utc(when, tz)
        if when_utc is None:
            await ctx.send("❌ Invalid time. Use `HH:MM` (next occurrence) or `YYYY-MM-DD HH:MM` (in your timezone).")
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        channel_id = ctx.channel.id if ctx.channel else 0
        if ctx.guild is None:
            channel_id = 0

        rid = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.create_reminder,
            guild_id,
            channel_id,
            ctx.author.id,
            message,
            _sqlite_utc_timestamp(when_utc),
            None,
        )
        if not rid:
            await ctx.send("❌ Could not create that reminder.")
            return

        local_str = when_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")
        tz_label = "CET/CEST" if str(tz_name or "").strip() in ("Europe/Warsaw", "CET", "CEST") else str(tz_name)
        await ctx.send(f"✅ Reminder **#{rid}** set for `{local_str}` {tz_label}.")

    @commands.hybrid_command(name="remind_list", description="List your active reminders.")
    async def remind_list(self, ctx: commands.Context):
        if not self.db_manager:
            await ctx.send("Database is not available right now. Please try again later.")
            return

        rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_user_reminders, ctx.author.id, False, 50)
        if not rows:
            await ctx.send("You have no active reminders.")
            return

        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        tz = _tzinfo_from_name(str(tz_name or "Europe/Warsaw"))
        tz_label = "CET/CEST" if str(tz_name or "").strip() in ("Europe/Warsaw", "CET", "CEST") else str(tz_name)

        embed = discord.Embed(title="⏰ Your reminders", color=discord.Color.gold())
        lines = []
        for r in rows[:50]:
            rid = r.get("id")
            msg = (r.get("message") or "")[:80]
            at_utc_s = r.get("trigger_at") or ""
            at_disp = "n/a"
            dt_at = _parse_sqlite_utc_timestamp(at_utc_s)
            if dt_at is not None:
                at_disp = dt_at.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            rep = r.get("repeat_interval_seconds")
            if rep:
                lines.append(f"- **#{rid}** every **{int(rep)}s** next `{at_disp}` {tz_label} — {msg}")
            else:
                lines.append(f"- **#{rid}** at `{at_disp}` {tz_label} — {msg}")
        embed.description = "\n".join(lines)[:4000]
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="remind_cancel", description="Cancel an active reminder by id.")
    async def remind_cancel(self, ctx: commands.Context, reminder_id: int):
        if not self.db_manager:
            await ctx.send("Database is not available right now. Please try again later.")
            return
        ok = await self.bot.loop.run_in_executor(None, self.db_manager.deactivate_reminder, ctx.author.id, int(reminder_id))
        if not ok:
            await ctx.send("❌ Could not cancel that reminder (maybe it doesn’t exist, or isn’t yours).")
            return
        await ctx.send(f"🗑️ Cancelled reminder **#{reminder_id}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(RemindersCog(bot, db_manager=getattr(bot, "db_manager", None)))
    logger.info("RemindersCog has been loaded.")


