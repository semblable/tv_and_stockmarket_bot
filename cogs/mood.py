import logging
import re
from datetime import datetime, timedelta, timezone, time as dtime, date
from functools import partial
from typing import Optional, Tuple, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks
import io

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)

# Preferences (stored in user_preferences table)
PREF_MOOD_ENABLED = "mood_enabled"
PREF_MOOD_REMINDER_TIME = "mood_reminder_time"  # "HH:MM" in user's timezone
PREF_MOOD_REMINDER_LAST_HANDLED_LOCAL_DATE = "mood_reminder_last_handled_local_date"  # "YYYY-MM-DD" in user's tz

# Late-window: avoid sending a missed reminder many hours late on startup.
REMINDER_GRACE = timedelta(hours=2)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sqlite_utc_timestamp(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_sqlite_utc_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        dt = datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


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
    return timezone.utc


def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (s or "").strip())
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


class MoodCog(commands.Cog, name="Mood"):
    """
    Mood tracking (opt-in).

    Design principles (from your report):
    - Optional by default; no spam for users who didn't ask for it.
    - No streaks/badges; gaps are normal.
    - Gentle reflection prompt via optional notes.
    """

    def __init__(self, bot: commands.Bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager

    async def cog_load(self):
        self.mood_reminder_loop.start()
        # Make sure mood slash commands are available in DMs.
        #
        # Important nuance:
        # For slash command *groups* (like `/mood`), Discord may hide the entire command tree in DMs
        # unless the *group object itself* is DM-enabled — even if subcommands are.
        #
        # Hybrid groups can be tricky to patch by iterating `tree.walk_commands()` (some versions
        # skip the top-level group). So we patch the group + all its descendants directly.
        def _patch(cmd: app_commands.AppCommand) -> None:
            app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)(cmd)
            app_commands.allowed_installs(guilds=True, users=True)(cmd)

        try:
            hybrid_group = getattr(self, "mood_group", None)
            app_group = getattr(hybrid_group, "app_command", None)
            if app_group is not None:
                try:
                    _patch(app_group)
                except Exception:
                    pass
                try:
                    for child in app_group.walk_commands():
                        try:
                            _patch(child)
                        except Exception:
                            continue
                except Exception:
                    pass
        except Exception:
            pass
        logger.info("MoodCog loaded and reminder loop started.")

    async def cog_unload(self):
        self.mood_reminder_loop.cancel()
        logger.info("MoodCog unloaded and reminder loop cancelled.")

    async def _send_ctx(
        self,
        ctx: commands.Context,
        content: str,
        *,
        ephemeral: bool = True,
        embed: Optional[discord.Embed] = None,
    ) -> None:
        if getattr(ctx, "interaction", None):
            # Ephemeral responses are not supported/meaningful in DMs; some clients/APIs reject them.
            # To stay maximally compatible, we don't pass the `ephemeral` kwarg at all in DMs.
            base_kwargs: Dict[str, Any] = {"content": content if content else None, "embed": embed}
            if getattr(ctx, "guild", None) is not None:
                base_kwargs["ephemeral"] = bool(ephemeral)
            await ctx.send(**base_kwargs)
        else:
            await ctx.send(content=content if content else None, embed=embed)

    async def _is_user_in_dnd(self, user_id: int) -> bool:
        """
        Best-effort DND check using settings keys (same semantics as RemindersCog).
        """
        if not self.db_manager:
            return False
        try:
            dnd_enabled = await self.bot.loop.run_in_executor(
                None, self.db_manager.get_user_preference, int(user_id), "dnd_enabled", False
            )
            if not dnd_enabled:
                return False

            dnd_start_str = await self.bot.loop.run_in_executor(
                None, self.db_manager.get_user_preference, int(user_id), "dnd_start_time", "00:00"
            )
            dnd_end_str = await self.bot.loop.run_in_executor(
                None, self.db_manager.get_user_preference, int(user_id), "dnd_end_time", "00:00"
            )
            try:
                start_t = datetime.strptime(str(dnd_start_str), "%H:%M").time()
                end_t = datetime.strptime(str(dnd_end_str), "%H:%M").time()
            except ValueError:
                start_t = dtime(0, 0)
                end_t = dtime(0, 0)

            now_t = datetime.now().time()
            if start_t == end_t:
                return False
            if start_t < end_t:
                return start_t <= now_t < end_t
            return now_t >= start_t or now_t < end_t
        except Exception:
            return False

    async def _dm_user(self, user_id: int, content: str) -> bool:
        try:
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
            await user.send(content=content)
            return True
        except Exception:
            return False

    async def _user_has_mood_entry_today(self, user_id: int, tz_name: str) -> bool:
        if not self.db_manager:
            return False
        tz = _tzinfo_from_name(tz_name)
        now_local = _utc_now().astimezone(tz)
        start_local = datetime.combine(now_local.date(), dtime(0, 0), tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        start_utc = _sqlite_utc_timestamp(start_local.astimezone(timezone.utc))
        end_utc = _sqlite_utc_timestamp(end_local.astimezone(timezone.utc))
        rows = await self.bot.loop.run_in_executor(
            None, self.db_manager.list_mood_entries_between, int(user_id), start_utc, end_utc, 1
        )
        return bool(rows)

    @tasks.loop(minutes=1)
    async def mood_reminder_loop(self):
        if not self.db_manager:
            return

        # Get opted-in users (only users who explicitly enabled mood tracking).
        enabled_rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_users_with_preference, PREF_MOOD_ENABLED)
        enabled_uids = set()
        for r in enabled_rows or []:
            try:
                if bool(r.get("value")):
                    enabled_uids.add(int(r.get("user_id")))
            except Exception:
                continue
        if not enabled_uids:
            return

        # Get reminder times for users who set it.
        time_rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_users_with_preference, PREF_MOOD_REMINDER_TIME)
        reminder_by_uid: Dict[int, str] = {}
        for r in time_rows or []:
            try:
                uid = int(r.get("user_id"))
            except Exception:
                continue
            if uid not in enabled_uids:
                continue
            v = r.get("value")
            if isinstance(v, str) and _parse_hhmm(v):
                reminder_by_uid[uid] = v.strip()

        if not reminder_by_uid:
            return

        now_utc = _utc_now()
        for uid, hhmm in reminder_by_uid.items():
            try:
                # DND respected (best effort)
                if await self._is_user_in_dnd(uid):
                    continue

                tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "timezone", "Europe/Warsaw")
                tz_name_s = str(tz_name or "Europe/Warsaw")
                tz = _tzinfo_from_name(tz_name_s)
                now_local = now_utc.astimezone(tz)
                today_s = now_local.date().isoformat()

                last_handled = await self.bot.loop.run_in_executor(
                    None, self.db_manager.get_user_preference, uid, PREF_MOOD_REMINDER_LAST_HANDLED_LOCAL_DATE, None
                )
                if isinstance(last_handled, str) and last_handled.strip() == today_s:
                    continue

                hm = _parse_hhmm(hhmm)
                if hm is None:
                    continue
                hh, mm = hm
                scheduled_local = datetime.combine(now_local.date(), dtime(hh, mm), tzinfo=tz)
                if now_local < scheduled_local:
                    continue

                # Avoid sending many hours late.
                if (now_local - scheduled_local) > REMINDER_GRACE:
                    await self.bot.loop.run_in_executor(
                        None, self.db_manager.set_user_preference, uid, PREF_MOOD_REMINDER_LAST_HANDLED_LOCAL_DATE, today_s
                    )
                    continue

                # If user already logged today, skip the reminder.
                if await self._user_has_mood_entry_today(uid, tz_name_s):
                    await self.bot.loop.run_in_executor(
                        None, self.db_manager.set_user_preference, uid, PREF_MOOD_REMINDER_LAST_HANDLED_LOCAL_DATE, today_s
                    )
                    continue

                sent = await self._dm_user(
                    uid,
                    "🧠 Optional mood check-in: how are you feeling right now?\n"
                    "Log it with `/mood log <1-10> [note]` (you can log multiple times per day). "
                    "Gaps are okay—this is just information, not a score.",
                )
                # Mark handled regardless to avoid spamming on failures/retries.
                await self.bot.loop.run_in_executor(
                    None, self.db_manager.set_user_preference, uid, PREF_MOOD_REMINDER_LAST_HANDLED_LOCAL_DATE, today_s
                )
                if not sent:
                    # If DMs are blocked, we quietly stop for the day (user can still use commands).
                    continue
            except Exception as e:
                logger.debug(f"mood_reminder_loop error for user {uid}: {e}")
                continue

    @mood_reminder_loop.before_loop
    async def before_mood_reminder_loop(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_group(name="mood", fallback="help", description="Track your mood (optional, no streaks).")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def mood_group(self, ctx: commands.Context):
        # NOTE:
        # For hybrid groups, `fallback="help"` registers the group callback itself as `/mood help`.
        # Defining an additional `@mood_group.command(name="help")` would double-register the same
        # slash subcommand and crash on startup with CommandAlreadyRegistered.
        if ctx.invoked_subcommand is not None:
            return

        msg = (
            "**Mood tracking (opt-in)**\n"
            "- `/mood enable` — turn mood tracking on\n"
            "- `/mood disable` — turn it off (no reminders)\n"
            "- `/mood reminder <HH:MM|off>` — set a daily reminder (uses your `settings timezone`)\n"
            "- `/mood log <1-10> [note] [energy]` — log a mood (multiple times/day supported)\n"
            "- `/mood edit [count]` — edit a recent entry (interactive)\n"
            "- `/mood delete [count]` — delete a recent entry (interactive)\n"
            "- `/mood today` — show today’s entries\n"
            "- `/mood week` — simple 7-day summary (with neutral “gaps”)\n"
            "- `/mood report <week|month|year> <html|csv|both>` — export files\n"
        )
        await self._send_ctx(ctx, msg, ephemeral=True)

    @mood_group.command(name="enable", description="Enable mood tracking (optional reminders).")
    async def mood_enable(self, ctx: commands.Context):
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return
        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, ctx.author.id, PREF_MOOD_ENABLED, True)
        await self._send_ctx(
            ctx,
            "✅ Mood tracking enabled. If you want a daily reminder, set one with `/mood reminder HH:MM`.",
            ephemeral=True,
        )

    @mood_group.command(name="disable", description="Disable mood tracking and stop reminders.")
    async def mood_disable(self, ctx: commands.Context):
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return
        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, ctx.author.id, PREF_MOOD_ENABLED, False)
        await self._send_ctx(ctx, "🛑 Mood tracking disabled. I won’t remind you about it.", ephemeral=True)

    @mood_group.command(name="reminder", description="Set or disable your daily mood reminder (HH:MM or off).")
    async def mood_reminder(self, ctx: commands.Context, when: str):
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return
        w = (when or "").strip().lower()
        if w in ("off", "disable", "none", "no", "stop"):
            await self.bot.loop.run_in_executor(None, self.db_manager.delete_user_preference, ctx.author.id, PREF_MOOD_REMINDER_TIME)
            await self.bot.loop.run_in_executor(None, self.db_manager.delete_user_preference, ctx.author.id, PREF_MOOD_REMINDER_LAST_HANDLED_LOCAL_DATE)
            await self._send_ctx(ctx, "✅ Mood reminder disabled.", ephemeral=True)
            return

        hm = _parse_hhmm(w)
        if hm is None:
            await self._send_ctx(ctx, "❌ Invalid time. Use `HH:MM` (24h), e.g. `21:30`, or `off`.", ephemeral=True)
            return

        # Setting a reminder implies opt-in.
        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, ctx.author.id, PREF_MOOD_ENABLED, True)
        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, ctx.author.id, PREF_MOOD_REMINDER_TIME, f"{hm[0]:02d}:{hm[1]:02d}")
        await self.bot.loop.run_in_executor(None, self.db_manager.delete_user_preference, ctx.author.id, PREF_MOOD_REMINDER_LAST_HANDLED_LOCAL_DATE)

        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        tz_disp = str(tz_name or "Europe/Warsaw").strip()
        if tz_disp in ("Europe/Warsaw", "CET", "CEST"):
            tz_disp = "CET/CEST"
        await self._send_ctx(ctx, f"✅ Daily mood reminder set for `{hm[0]:02d}:{hm[1]:02d}` ({tz_disp}).", ephemeral=True)

    @mood_group.command(name="log", description="Log your mood (1-10) with an optional note and energy (1-10).")
    async def mood_log(self, ctx: commands.Context, mood: int, note: Optional[str] = None, energy: Optional[int] = None):
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return

        # Must be explicitly enabled (avoid accidental tracking).
        enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, PREF_MOOD_ENABLED, False)
        if not enabled:
            await self._send_ctx(ctx, "Mood tracking is currently off. Enable it with `/mood enable`.", ephemeral=True)
            return

        entry_id = await self.bot.loop.run_in_executor(
            None,
            partial(
                self.db_manager.create_mood_entry,
                ctx.author.id,
                int(mood),
                energy=energy,
                note=note,
                created_at_utc=_sqlite_utc_timestamp(_utc_now()),
            ),
        )
        if not entry_id:
            await self._send_ctx(ctx, "❌ Could not log that. Mood and energy must be 1–10.", ephemeral=True)
            return

        # Mark today's reminder as handled so we don't ping again after logging.
        try:
            tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
            tz = _tzinfo_from_name(str(tz_name or "Europe/Warsaw"))
            today_s = _utc_now().astimezone(tz).date().isoformat()
            await self.bot.loop.run_in_executor(
                None, self.db_manager.set_user_preference, ctx.author.id, PREF_MOOD_REMINDER_LAST_HANDLED_LOCAL_DATE, today_s
            )
        except Exception:
            pass

        gentle = "Logged. Thanks for checking in—this is data, not a grade."
        if not (note or "").strip():
            gentle += " If you want, add a tiny note next time (e.g. “why” or “what happened”)."
        await self._send_ctx(ctx, f"✅ Mood entry **#{entry_id}** saved. {gentle}", ephemeral=True)

    @mood_group.command(name="edit", description="Edit one of your recent mood entries (interactive).")
    async def mood_edit(self, ctx: commands.Context, count: int = 5):
        """
        Slash command: dropdown + modal.
        Prefix command: reactions (1️⃣..5️⃣) then text prompt.
        """
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return
        enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, PREF_MOOD_ENABLED, False)
        if not enabled:
            await self._send_ctx(ctx, "Mood tracking is currently off. Enable it with `/mood enable`.", ephemeral=True)
            return

        n = max(1, min(10, int(count or 5)))
        rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_mood_entries, ctx.author.id, n)
        if not rows:
            await self._send_ctx(ctx, "No mood entries found to edit yet.", ephemeral=True)
            return

        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        tz_name_s = str(tz_name or "Europe/Warsaw")
        tz = _tzinfo_from_name(tz_name_s)

        def fmt_row(r: dict) -> str:
            dt_utc = _parse_sqlite_utc_timestamp(r.get("created_at"))
            t = dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt_utc else "unknown time"
            mood_v = r.get("mood")
            energy_v = r.get("energy")
            note_v = (r.get("note") or "").strip()
            parts = [f"{t} — {mood_v}/10"]
            if energy_v is not None:
                parts.append(f"energy {energy_v}/10")
            if note_v:
                parts.append(note_v[:80])
            return " · ".join(parts)

        # --- Slash interaction flow: select menu + modal ---
        if getattr(ctx, "interaction", None):
            interaction: discord.Interaction = ctx.interaction  # type: ignore

            options = []
            for r in rows:
                try:
                    rid = int(r.get("id"))
                except Exception:
                    continue
                label = fmt_row(r)
                if len(label) > 100:
                    label = label[:97] + "..."
                options.append(discord.SelectOption(label=label, value=str(rid)))

            if not options:
                await self._send_ctx(ctx, "Could not load entries to edit.", ephemeral=True)
                return

            class _EditModal(discord.ui.Modal, title="Edit mood entry"):
                new_mood = discord.ui.TextInput(label="Mood (1-10)", required=False, placeholder="Leave blank to keep unchanged")
                new_energy = discord.ui.TextInput(label="Energy (1-10) or 'clear'", required=False, placeholder="Leave blank to keep unchanged")
                new_note = discord.ui.TextInput(
                    label="Note (optional; empty clears)",
                    required=False,
                    style=discord.TextStyle.paragraph,
                    placeholder="Leave blank to keep unchanged. Type 'clear' to remove.",
                    max_length=1000,
                )

                def __init__(self, *, entry_id: int, cog: "MoodCog"):
                    super().__init__()
                    self.entry_id = int(entry_id)
                    self.cog = cog

                async def on_submit(self, interaction2: discord.Interaction):  # type: ignore
                    # Map fields: blank => keep; energy: empty string explicitly clears; note: empty clears.
                    mood_in = (str(self.new_mood.value) if self.new_mood.value is not None else "").strip()
                    energy_in = (str(self.new_energy.value) if self.new_energy.value is not None else "").strip()
                    note_in = (str(self.new_note.value) if self.new_note.value is not None else "").strip()

                    mood_arg = None
                    if mood_in:
                        try:
                            mood_arg = int(mood_in)
                        except Exception:
                            if interaction2.guild is not None:
                                await interaction2.response.send_message("❌ Mood must be a number 1–10.", ephemeral=True)
                            else:
                                await interaction2.response.send_message("❌ Mood must be a number 1–10.")
                            return

                    energy_unset = True
                    energy_arg = None
                    if energy_in:
                        energy_unset = False
                        if energy_in.lower() in ("clear", "none", "null"):
                            energy_arg = None
                        else:
                            try:
                                energy_arg = int(energy_in)
                            except Exception:
                                if interaction2.guild is not None:
                                    await interaction2.response.send_message(
                                        "❌ Energy must be a number 1–10 (or 'clear').", ephemeral=True
                                    )
                                else:
                                    await interaction2.response.send_message("❌ Energy must be a number 1–10 (or 'clear').")
                                return

                    note_unset = True
                    note_arg = None
                    if note_in:
                        note_unset = False
                        if note_in.lower() in ("clear", "none", "null"):
                            note_arg = None
                        else:
                            note_arg = note_in

                    # If user wants explicit clears, they can type "clear"/"none" for energy.
                    kwargs = {}
                    if mood_arg is not None:
                        kwargs["mood"] = mood_arg
                    if not energy_unset:
                        kwargs["energy"] = energy_arg
                    if not note_unset:
                        kwargs["note"] = note_arg

                    if not kwargs:
                        if interaction2.guild is not None:
                            await interaction2.response.send_message("No changes provided.", ephemeral=True)
                        else:
                            await interaction2.response.send_message("No changes provided.")
                        return

                    ok = await self.cog.bot.loop.run_in_executor(
                        None,
                        self.cog.db_manager.update_mood_entry,
                        int(interaction2.user.id),
                        int(self.entry_id),
                        **kwargs,
                    )
                    if not ok:
                        if interaction2.guild is not None:
                            await interaction2.response.send_message(
                                "❌ Could not update that entry (check ranges 1–10).", ephemeral=True
                            )
                        else:
                            await interaction2.response.send_message("❌ Could not update that entry (check ranges 1–10).")
                        return
                    if interaction2.guild is not None:
                        await interaction2.response.send_message(f"✅ Updated mood entry **#{self.entry_id}**.", ephemeral=True)
                    else:
                        await interaction2.response.send_message(f"✅ Updated mood entry **#{self.entry_id}**.")

            class _SelectView(discord.ui.View):
                def __init__(self, *, cog: "MoodCog"):
                    super().__init__(timeout=120)
                    self.cog = cog

                @discord.ui.select(placeholder="Select an entry to edit…", min_values=1, max_values=1, options=options)
                async def select_cb(self, interaction3: discord.Interaction, select: discord.ui.Select):  # type: ignore
                    if interaction3.user.id != ctx.author.id:
                        if interaction3.guild is not None:
                            await interaction3.response.send_message("❌ This picker isn’t for you.", ephemeral=True)
                        else:
                            await interaction3.response.send_message("❌ This picker isn’t for you.")
                        return
                    entry_id = int(select.values[0])
                    await interaction3.response.send_modal(_EditModal(entry_id=entry_id, cog=self.cog))

            embed = discord.Embed(title="✏️ Edit mood entry", color=discord.Color.blurple())
            embed.description = "Pick an entry below to edit it. (Only you can see this.)"
            if ctx.guild is not None:
                await ctx.send(embed=embed, view=_SelectView(cog=self), ephemeral=True)
            else:
                await ctx.send(embed=embed, view=_SelectView(cog=self))
            return

        # --- Prefix flow: reactions ---
        # We’ll list up to 5 and let user pick by emoji, then ask for a single-line edit spec.
        pick_rows = rows[: min(5, len(rows))]
        emoji_map = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        lines = []
        for i, r in enumerate(pick_rows):
            lines.append(f"{emoji_map[i]}  {fmt_row(r)}  (id={r.get('id')})")
        prompt = (
            "Reply by reacting with the number for the entry to edit:\n\n"
            + "\n".join(lines)
            + "\n\nThen I’ll ask for the new values."
        )
        msg = await ctx.send(prompt)
        for i in range(len(pick_rows)):
            try:
                await msg.add_reaction(emoji_map[i])
            except Exception:
                pass

        def check(reaction: discord.Reaction, user: discord.User):
            return user.id == ctx.author.id and reaction.message.id == msg.id and str(reaction.emoji) in emoji_map[: len(pick_rows)]

        try:
            reaction, _user = await self.bot.wait_for("reaction_add", timeout=90, check=check)
        except Exception:
            await ctx.send("Timed out waiting for a reaction.")
            return

        idx = emoji_map.index(str(reaction.emoji))
        picked = pick_rows[idx]
        entry_id = int(picked.get("id"))

        await ctx.send(
            "Send your edit in one message. Examples:\n"
            "- `mood=8`\n"
            "- `energy=4`\n"
            "- `note=had a stressful meeting`\n"
            "- `mood=6 energy=5 note=felt better after walk`\n"
            "Use `energy=clear` to clear energy. Use `note=clear` to clear note."
        )

        def msg_check(m: discord.Message):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            reply = await self.bot.wait_for("message", timeout=120, check=msg_check)
        except Exception:
            await ctx.send("Timed out waiting for your edit message.")
            return

        text = (reply.content or "").strip()
        # Parser that supports spaces in note=... (note consumes the rest of the message).
        mood_arg = None
        m_mood = re.search(r"\bmood=(\d+)\b", text, flags=re.IGNORECASE)
        if m_mood:
            try:
                mood_arg = int(m_mood.group(1))
            except Exception:
                mood_arg = None

        energy_provided = False
        energy_arg = None
        m_energy = re.search(r"\benergy=([^\s]+)\b", text, flags=re.IGNORECASE)
        if m_energy:
            energy_provided = True
            ev = (m_energy.group(1) or "").strip().lower()
            if ev in ("clear", "none", "null", ""):
                energy_arg = None
            else:
                try:
                    energy_arg = int(ev)
                except Exception:
                    energy_arg = "bad"

        note_provided = False
        note_arg = None
        m_note = re.search(r"\bnote=(.+)$", text, flags=re.IGNORECASE)
        if m_note:
            note_provided = True
            nv = (m_note.group(1) or "").strip()
            if nv.lower() in ("clear", "none", "null", ""):
                note_arg = None
            else:
                note_arg = nv[:1000]

        kwargs = {}
        if mood_arg is not None:
            kwargs["mood"] = mood_arg
        if energy_provided:
            if energy_arg == "bad":
                await ctx.send("❌ Energy must be 1–10 or `clear`.")
                return
            kwargs["energy"] = energy_arg
        if note_provided:
            kwargs["note"] = note_arg

        if not kwargs:
            await ctx.send("No valid changes found.")
            return

        ok = await self.bot.loop.run_in_executor(
            None, partial(self.db_manager.update_mood_entry, ctx.author.id, entry_id, **kwargs)
        )
        if not ok:
            await ctx.send("❌ Could not update that entry (check ranges 1–10).")
            return
        await ctx.send(f"✅ Updated mood entry **#{entry_id}**.")

    @mood_group.command(name="delete", description="Delete one of your recent mood entries (interactive).")
    async def mood_delete(self, ctx: commands.Context, count: int = 5):
        """
        Slash command: dropdown + confirm buttons.
        Prefix command: reactions (1️⃣..5️⃣) then typed confirmation.
        """
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return
        enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, PREF_MOOD_ENABLED, False)
        if not enabled:
            await self._send_ctx(ctx, "Mood tracking is currently off. Enable it with `/mood enable`.", ephemeral=True)
            return

        n = max(1, min(10, int(count or 5)))
        rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_mood_entries, ctx.author.id, n)
        if not rows:
            await self._send_ctx(ctx, "No mood entries found to delete yet.", ephemeral=True)
            return

        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        tz_name_s = str(tz_name or "Europe/Warsaw")
        tz = _tzinfo_from_name(tz_name_s)

        def fmt_row(r: dict) -> str:
            dt_utc = _parse_sqlite_utc_timestamp(r.get("created_at"))
            t = dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt_utc else "unknown time"
            mood_v = r.get("mood")
            energy_v = r.get("energy")
            note_v = (r.get("note") or "").strip()
            parts = [f"{t} — {mood_v}/10"]
            if energy_v is not None:
                parts.append(f"energy {energy_v}/10")
            if note_v:
                parts.append(note_v[:80])
            return " · ".join(parts)

        # Slash interaction flow
        if getattr(ctx, "interaction", None):
            options = []
            for r in rows:
                try:
                    rid = int(r.get("id"))
                except Exception:
                    continue
                label = fmt_row(r)
                if len(label) > 100:
                    label = label[:97] + "..."
                options.append(discord.SelectOption(label=label, value=str(rid)))

            if not options:
                await self._send_ctx(ctx, "Could not load entries to delete.", ephemeral=True)
                return

            class _ConfirmView(discord.ui.View):
                def __init__(self, *, cog: "MoodCog", entry_id: int):
                    super().__init__(timeout=90)
                    self.cog = cog
                    self.entry_id = int(entry_id)

                @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
                async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore
                    if interaction.user.id != ctx.author.id:
                        if interaction.guild is not None:
                            await interaction.response.send_message("❌ This isn’t for you.", ephemeral=True)
                        else:
                            await interaction.response.send_message("❌ This isn’t for you.")
                        return
                    ok = await self.cog.bot.loop.run_in_executor(
                        None, self.cog.db_manager.delete_mood_entry, int(interaction.user.id), int(self.entry_id)
                    )
                    if not ok:
                        if interaction.guild is not None:
                            await interaction.response.send_message("❌ Could not delete that entry.", ephemeral=True)
                        else:
                            await interaction.response.send_message("❌ Could not delete that entry.")
                        return
                    if interaction.guild is not None:
                        await interaction.response.send_message(f"🗑️ Deleted mood entry **#{self.entry_id}**.", ephemeral=True)
                    else:
                        await interaction.response.send_message(f"🗑️ Deleted mood entry **#{self.entry_id}**.")
                    try:
                        self.stop()
                    except Exception:
                        pass

                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore
                    if interaction.user.id != ctx.author.id:
                        if interaction.guild is not None:
                            await interaction.response.send_message("❌ This isn’t for you.", ephemeral=True)
                        else:
                            await interaction.response.send_message("❌ This isn’t for you.")
                        return
                    if interaction.guild is not None:
                        await interaction.response.send_message("Cancelled.", ephemeral=True)
                    else:
                        await interaction.response.send_message("Cancelled.")
                    try:
                        self.stop()
                    except Exception:
                        pass

            class _SelectDeleteView(discord.ui.View):
                def __init__(self, *, cog: "MoodCog"):
                    super().__init__(timeout=120)
                    self.cog = cog

                @discord.ui.select(placeholder="Select an entry to delete…", min_values=1, max_values=1, options=options)
                async def select_cb(self, interaction: discord.Interaction, select: discord.ui.Select):  # type: ignore
                    if interaction.user.id != ctx.author.id:
                        if interaction.guild is not None:
                            await interaction.response.send_message("❌ This picker isn’t for you.", ephemeral=True)
                        else:
                            await interaction.response.send_message("❌ This picker isn’t for you.")
                        return
                    entry_id = int(select.values[0])
                    if interaction.guild is not None:
                        await interaction.response.send_message(
                            f"Delete mood entry **#{entry_id}**?\nThis can’t be undone.",
                            view=_ConfirmView(cog=self.cog, entry_id=entry_id),
                            ephemeral=True,
                        )
                    else:
                        await interaction.response.send_message(
                            f"Delete mood entry **#{entry_id}**?\nThis can’t be undone.",
                            view=_ConfirmView(cog=self.cog, entry_id=entry_id),
                        )

            embed = discord.Embed(title="🗑️ Delete mood entry", color=discord.Color.red())
            embed.description = "Pick an entry below to delete it (you’ll be asked to confirm)."
            if ctx.guild is not None:
                await ctx.send(embed=embed, view=_SelectDeleteView(cog=self), ephemeral=True)
            else:
                await ctx.send(embed=embed, view=_SelectDeleteView(cog=self))
            return

        # Prefix flow: reactions + typed confirm
        pick_rows = rows[: min(5, len(rows))]
        emoji_map = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        lines = []
        for i, r in enumerate(pick_rows):
            lines.append(f"{emoji_map[i]}  {fmt_row(r)}  (id={r.get('id')})")
        prompt = (
            "React with the number for the entry to **delete**:\n\n"
            + "\n".join(lines)
            + "\n\nThen type `delete` to confirm (anything else cancels)."
        )
        msg = await ctx.send(prompt)
        for i in range(len(pick_rows)):
            try:
                await msg.add_reaction(emoji_map[i])
            except Exception:
                pass

        def check(reaction: discord.Reaction, user: discord.User):
            return user.id == ctx.author.id and reaction.message.id == msg.id and str(reaction.emoji) in emoji_map[: len(pick_rows)]

        try:
            reaction, _user = await self.bot.wait_for("reaction_add", timeout=90, check=check)
        except Exception:
            await ctx.send("Timed out waiting for a reaction.")
            return

        idx = emoji_map.index(str(reaction.emoji))
        picked = pick_rows[idx]
        entry_id = int(picked.get("id"))

        def msg_check(m: discord.Message):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            confirm = await self.bot.wait_for("message", timeout=60, check=msg_check)
        except Exception:
            await ctx.send("Timed out waiting for confirmation.")
            return

        if (confirm.content or "").strip().lower() != "delete":
            await ctx.send("Cancelled.")
            return

        ok = await self.bot.loop.run_in_executor(None, self.db_manager.delete_mood_entry, ctx.author.id, entry_id)
        if not ok:
            await ctx.send("❌ Could not delete that entry.")
            return
        await ctx.send(f"🗑️ Deleted mood entry **#{entry_id}**.")

    @mood_group.command(name="today", description="Show your mood entries for today (in your timezone).")
    async def mood_today(self, ctx: commands.Context):
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return

        enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, PREF_MOOD_ENABLED, False)
        if not enabled:
            await self._send_ctx(ctx, "Mood tracking is currently off. Enable it with `/mood enable`.", ephemeral=True)
            return

        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        tz_name_s = str(tz_name or "Europe/Warsaw")
        tz = _tzinfo_from_name(tz_name_s)
        now_local = _utc_now().astimezone(tz)
        start_local = datetime.combine(now_local.date(), dtime(0, 0), tzinfo=tz)
        end_local = start_local + timedelta(days=1)

        rows = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.list_mood_entries_between,
            ctx.author.id,
            _sqlite_utc_timestamp(start_local.astimezone(timezone.utc)),
            _sqlite_utc_timestamp(end_local.astimezone(timezone.utc)),
            200,
        )
        if not rows:
            await self._send_ctx(ctx, "No mood entries yet today. If you want, log one with `/mood log 1-10 [note]`.", ephemeral=True)
            return

        tz_disp = tz_name_s
        if tz_disp in ("Europe/Warsaw", "CET", "CEST"):
            tz_disp = "CET/CEST"
        embed = discord.Embed(title="🧠 Mood — today", color=discord.Color.blurple())
        embed.set_footer(text=f"Timezone: {tz_disp}. Gaps are okay.")
        lines = []
        for r in rows[:25]:
            dt_utc = _parse_sqlite_utc_timestamp(r.get("created_at"))
            t = dt_utc.astimezone(tz).strftime("%H:%M") if dt_utc else "??:??"
            mood_v = r.get("mood")
            energy_v = r.get("energy")
            note_v = (r.get("note") or "").strip()
            extra = f" (energy {energy_v}/10)" if energy_v is not None else ""
            if note_v:
                lines.append(f"- `{t}` — **{mood_v}/10**{extra} — {note_v[:120]}")
            else:
                lines.append(f"- `{t}` — **{mood_v}/10**{extra}")
        embed.description = "\n".join(lines)[:4000]
        await self._send_ctx(ctx, "", ephemeral=True, embed=embed)

    @mood_group.command(name="week", description="Show a simple 7-day summary (neutral framing, with gaps).")
    async def mood_week(self, ctx: commands.Context):
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return

        enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, PREF_MOOD_ENABLED, False)
        if not enabled:
            await self._send_ctx(ctx, "Mood tracking is currently off. Enable it with `/mood enable`.", ephemeral=True)
            return

        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        tz_name_s = str(tz_name or "Europe/Warsaw")
        tz = _tzinfo_from_name(tz_name_s)
        now_local = _utc_now().astimezone(tz)
        start_local = datetime.combine((now_local.date() - timedelta(days=6)), dtime(0, 0), tzinfo=tz)
        end_local = datetime.combine((now_local.date() + timedelta(days=1)), dtime(0, 0), tzinfo=tz)

        rows = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.list_mood_entries_between,
            ctx.author.id,
            _sqlite_utc_timestamp(start_local.astimezone(timezone.utc)),
            _sqlite_utc_timestamp(end_local.astimezone(timezone.utc)),
            2000,
        )

        # Bucket by local date -> list of moods
        by_day: Dict[str, list[int]] = {}
        for r in rows or []:
            dt_utc = _parse_sqlite_utc_timestamp(r.get("created_at"))
            if dt_utc is None:
                continue
            day = dt_utc.astimezone(tz).date().isoformat()
            try:
                mv = int(r.get("mood"))
            except Exception:
                continue
            by_day.setdefault(day, []).append(mv)

        tz_disp = tz_name_s
        if tz_disp in ("Europe/Warsaw", "CET", "CEST"):
            tz_disp = "CET/CEST"

        embed = discord.Embed(title="🧠 Mood — last 7 days", color=discord.Color.blurple())
        embed.set_footer(text=f"Timezone: {tz_disp}. Gaps are neutral.")
        lines = []
        for i in range(6, -1, -1):
            d = (now_local.date() - timedelta(days=i)).isoformat()
            vals = by_day.get(d, [])
            if not vals:
                lines.append(f"- `{d}` — gap")
            else:
                avg = sum(vals) / len(vals)
                lines.append(f"- `{d}` — avg **{avg:.1f}/10** (n={len(vals)})")
        embed.description = "\n".join(lines)
        await self._send_ctx(ctx, "", ephemeral=True, embed=embed)

    @mood_group.command(name="report", description="Export a pretty mood report as a file (week/month/year).")
    async def mood_report(self, ctx: commands.Context, period: str = "week", export: str = "both"):
        """
        Export report files:
        - HTML (pretty, includes embedded chart image)
        - CSV (raw buckets)

        Args:
            period: week | month | year
            export: html | csv | both
        """
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return

        enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, PREF_MOOD_ENABLED, False)
        if not enabled:
            await self._send_ctx(ctx, "Mood tracking is currently off. Enable it with `/mood enable`.", ephemeral=True)
            return

        p = (period or "").strip().lower()
        if p not in ("week", "month", "year"):
            await self._send_ctx(ctx, "❌ Invalid period. Use `week`, `month`, or `year`.", ephemeral=True)
            return
        ex = (export or "").strip().lower()
        if ex not in ("html", "csv", "both"):
            await self._send_ctx(ctx, "❌ Invalid export. Use `html`, `csv`, or `both`.", ephemeral=True)
            return

        tz_name = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, ctx.author.id, "timezone", "Europe/Warsaw")
        tz_name_s = str(tz_name or "Europe/Warsaw")
        tz = _tzinfo_from_name(tz_name_s)
        tz_disp = tz_name_s
        if tz_disp in ("Europe/Warsaw", "CET", "CEST"):
            tz_disp = "CET/CEST"

        now_local = _utc_now().astimezone(tz)

        # Determine local range and bucketing granularity
        if p == "week":
            start_local = datetime.combine((now_local.date() - timedelta(days=6)), dtime(0, 0), tzinfo=tz)
            end_local = datetime.combine((now_local.date() + timedelta(days=1)), dtime(0, 0), tzinfo=tz)
            bucket_kind = "day"
            period_label = "Last 7 days"
        elif p == "month":
            first = now_local.date().replace(day=1)
            # next month
            if first.month == 12:
                nm = first.replace(year=first.year + 1, month=1, day=1)
            else:
                nm = first.replace(month=first.month + 1, day=1)
            start_local = datetime.combine(first, dtime(0, 0), tzinfo=tz)
            end_local = datetime.combine(nm, dtime(0, 0), tzinfo=tz)
            bucket_kind = "day"
            period_label = f"Month {first.strftime('%Y-%m')}"
        else:
            first = now_local.date().replace(month=1, day=1)
            ny = first.replace(year=first.year + 1, month=1, day=1)
            start_local = datetime.combine(first, dtime(0, 0), tzinfo=tz)
            end_local = datetime.combine(ny, dtime(0, 0), tzinfo=tz)
            bucket_kind = "month"
            period_label = f"Year {first.strftime('%Y')}"

        start_utc_s = _sqlite_utc_timestamp(start_local.astimezone(timezone.utc))
        end_utc_s = _sqlite_utc_timestamp(end_local.astimezone(timezone.utc))
        rows = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.list_mood_entries_between,
            ctx.author.id,
            start_utc_s,
            end_utc_s,
            5000,
        )

        # Build buckets
        # day_key -> list of moods/energies
        by_key: Dict[str, Dict[str, list]] = {}
        for r in rows or []:
            dt_utc = _parse_sqlite_utc_timestamp(r.get("created_at"))
            if dt_utc is None:
                continue
            local_dt = dt_utc.astimezone(tz)
            if bucket_kind == "day":
                key = local_dt.date().isoformat()
                start_day = local_dt.date()
            else:
                key = local_dt.strftime("%Y-%m")
                start_day = local_dt.date().replace(day=1)
            try:
                mv = int(r.get("mood"))
            except Exception:
                continue
            ev = r.get("energy")
            try:
                ev_i = int(ev) if ev is not None else None
            except Exception:
                ev_i = None
            b = by_key.setdefault(key, {"moods": [], "energies": [], "start_day": [start_day]})
            b["moods"].append(mv)
            if ev_i is not None:
                b["energies"].append(ev_i)

        from utils.mood_report import MoodDaySummary, to_csv_bytes, to_html_report_bytes
        from utils.chart_utils import get_mood_daily_chart_image

        summaries: list[MoodDaySummary] = []
        if bucket_kind == "day":
            cur = start_local.date()
            while cur < end_local.date():
                key = cur.isoformat()
                b = by_key.get(key)
                if not b or not b.get("moods"):
                    summaries.append(MoodDaySummary(label=key, start_day=cur, n=0, avg_mood=None, avg_energy=None, min_mood=None, max_mood=None))
                else:
                    moods = list(b.get("moods") or [])
                    ens = list(b.get("energies") or [])
                    summaries.append(
                        MoodDaySummary(
                            label=key,
                            start_day=cur,
                            n=len(moods),
                            avg_mood=(sum(moods) / len(moods)) if moods else None,
                            avg_energy=(sum(ens) / len(ens)) if ens else None,
                            min_mood=min(moods) if moods else None,
                            max_mood=max(moods) if moods else None,
                        )
                    )
                cur = cur + timedelta(days=1)
        else:
            # Month buckets for a year view: iterate month-by-month
            y = start_local.date().year
            m = 1
            for m in range(1, 13):
                key = f"{y:04d}-{m:02d}"
                cur = date(y, m, 1)
                b = by_key.get(key)
                if not b or not b.get("moods"):
                    summaries.append(MoodDaySummary(label=key, start_day=cur, n=0, avg_mood=None, avg_energy=None, min_mood=None, max_mood=None))
                else:
                    moods = list(b.get("moods") or [])
                    ens = list(b.get("energies") or [])
                    summaries.append(
                        MoodDaySummary(
                            label=key,
                            start_day=cur,
                            n=len(moods),
                            avg_mood=(sum(moods) / len(moods)) if moods else None,
                            avg_energy=(sum(ens) / len(ens)) if ens else None,
                            min_mood=min(moods) if moods else None,
                            max_mood=max(moods) if moods else None,
                        )
                    )

        # Chart data (use averages; gaps => None to break lines)
        labels = [s.label for s in summaries]
        mood_vals = [None if s.avg_mood is None else round(float(s.avg_mood), 2) for s in summaries]
        # Only include energy dataset if at least one bucket has energy.
        if any(s.avg_energy is not None for s in summaries):
            energy_vals = [None if s.avg_energy is None else round(float(s.avg_energy), 2) for s in summaries]
        else:
            energy_vals = None

        chart_png_bytes: Optional[bytes] = None
        try:
            chart_buf = get_mood_daily_chart_image(
                f"Mood report — {period_label}",
                labels,
                mood_vals,
                energy_vals,
                chart_width=980,
                chart_height=420,
            )
            if chart_buf is not None:
                chart_png_bytes = chart_buf.getvalue()
        except Exception:
            chart_png_bytes = None

        html_bytes = to_html_report_bytes(
            title=f"Mood report — {period_label}",
            tz_label=tz_disp,
            period_label=period_label,
            days=summaries,
            chart_png_bytes=chart_png_bytes,
        )
        csv_bytes = to_csv_bytes(summaries)

        files = []
        base = f"mood-report-{p}-{now_local.strftime('%Y%m%d')}"
        if ex in ("html", "both"):
            files.append(discord.File(fp=io.BytesIO(html_bytes), filename=f"{base}.html"))
        if ex in ("csv", "both"):
            files.append(discord.File(fp=io.BytesIO(csv_bytes), filename=f"{base}.csv"))

        msg = f"✅ Generated your **{p}** mood report ({tz_disp})."
        # Prefer ephemeral for privacy, but some Discord clients/versions can be picky with ephemeral attachments.
        if getattr(ctx, "interaction", None):
            if ctx.guild is not None:
                try:
                    await ctx.send(content=msg, files=files, ephemeral=True)
                except Exception:
                    # Fallback to non-ephemeral so the export still works.
                    await ctx.send(content=msg + " (Sent non-ephemeral due to attachment limitation.)", files=files)
            else:
                # DMs: don't pass ephemeral kwarg at all.
                await ctx.send(content=msg, files=files)
        else:
            await ctx.send(content=msg, files=files)


async def setup(bot: commands.Bot):
    await bot.add_cog(MoodCog(bot, db_manager=getattr(bot, "db_manager", None)))
    logger.info("MoodCog has been loaded.")

