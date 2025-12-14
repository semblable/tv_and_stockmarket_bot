import logging
import re
import csv
import io
import json
from datetime import datetime, timedelta, time as dt_time, timezone
from functools import partial
from typing import Optional, List

import discord
from discord.ext import commands, tasks

from utils.chart_utils import get_weekly_reading_chart_image

logger = logging.getLogger(__name__)


def _format_seconds(total_seconds: Optional[int]) -> str:
    if total_seconds is None:
        return "N/A"
    try:
        s = max(0, int(total_seconds))
    except (TypeError, ValueError):
        return "N/A"
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:d}:{sec:02d}"


def _parse_duration_to_seconds(text: str) -> Optional[int]:
    """
    Accepts:
    - HH:MM:SS
    - MM:SS
    - 1h30m / 45m / 90s
    - plain number -> minutes
    """
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if not t:
        return None

    # Colon formats
    if ":" in t:
        parts = [p.strip() for p in t.split(":")]
        if not all(p.isdigit() for p in parts):
            return None
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 3:
            h, m, s = nums
            return max(0, h * 3600 + m * 60 + s)
        if len(nums) == 2:
            m, s = nums
            return max(0, m * 60 + s)
        return None

    # 1h30m20s format
    m = re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", t)
    if m:
        h_s, m_s, s_s = m.group(1), m.group(2), m.group(3)
        if not (h_s or m_s or s_s):
            return None
        h = int(h_s) if h_s else 0
        mm = int(m_s) if m_s else 0
        ss = int(s_s) if s_s else 0
        return max(0, h * 3600 + mm * 60 + ss)

    # Plain integer -> minutes
    if t.isdigit():
        return max(0, int(t) * 60)

    return None


class ReadingProgressCog(commands.Cog, name="Reading"):
    """
    Track personal reading progress across:
    - paper/ebook pages
    - Kindle location
    - audiobook listening time
    """

    def __init__(self, bot: commands.Bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager
        self.reading_reminders.start()

    def cog_unload(self):
        try:
            self.reading_reminders.cancel()
        except Exception:
            pass

    @staticmethod
    def _is_dm_ctx(ctx: commands.Context) -> bool:
        return ctx.guild is None

    async def _defer_if_interaction(self, ctx: commands.Context, *, ephemeral: bool = True) -> None:
        if not getattr(ctx, "interaction", None):
            return
        try:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer(ephemeral=ephemeral)
        except discord.HTTPException:
            pass
        except discord.InteractionResponded:
            pass

    async def _send(self, ctx: commands.Context, content: Optional[str] = None, *, embed: Optional[discord.Embed] = None, ephemeral: bool = True):
        if getattr(ctx, "interaction", None):
            # If not responded/deferred yet, use initial response.
            try:
                if not ctx.interaction.response.is_done():
                    return await ctx.interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
            except discord.HTTPException:
                pass
            return await ctx.interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        return await ctx.send(content=content, embed=embed)

    async def _is_user_in_dnd(self, user_id: int) -> bool:
        """
        Best-effort DND check using SettingsCog preferences (local clock).
        """
        try:
            dnd_enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_enabled", False)
            if not dnd_enabled:
                return False
            dnd_start_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_start_time", "00:00")
            dnd_end_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_end_time", "00:00")
            try:
                dnd_start = datetime.strptime(str(dnd_start_str), "%H:%M").time()
                dnd_end = datetime.strptime(str(dnd_end_str), "%H:%M").time()
            except ValueError:
                return False

            now_t = datetime.now().time()
            if dnd_start <= dnd_end:
                return dnd_start <= now_t <= dnd_end
            # crosses midnight
            return now_t >= dnd_start or now_t <= dnd_end
        except Exception:
            return False

    @staticmethod
    def _utc_today_iso() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _week_start_end_utc(today: Optional[datetime] = None) -> tuple[str, str]:
        dt = today or datetime.now(timezone.utc)
        d = dt.date()
        start = d - timedelta(days=d.weekday())  # Monday
        end = d
        return start.isoformat(), end.isoformat()

    @staticmethod
    def _seconds_to_minutes(seconds: int) -> int:
        try:
            return max(0, int(round(int(seconds) / 60.0)))
        except Exception:
            return 0

    def _compute_streaks_from_daily(self, daily_rows: List[dict]) -> tuple[int, int]:
        """
        Returns (current_streak_days, best_streak_days) from daily rows {day,pages,audio_seconds}.
        """
        active = []
        for r in daily_rows:
            try:
                day = r.get("day")
                pages = int(r.get("pages") or 0)
                audio = int(r.get("audio_seconds") or 0)
            except Exception:
                continue
            if not isinstance(day, str):
                continue
            active.append((day, (pages > 0) or (audio > 0)))

        if not active:
            return 0, 0

        # best streak
        best = 0
        cur = 0
        for _, is_active in active:
            if is_active:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0

        # current streak: count backwards from the end until first inactive
        cur_streak = 0
        for _, is_active in reversed(active):
            if is_active:
                cur_streak += 1
            else:
                break
        return cur_streak, best

    def _progress_lines(self, item: dict) -> List[str]:
        lines: List[str] = []
        if item.get("current_page") is not None:
            if item.get("total_pages"):
                lines.append(f"- **Pages**: {item['current_page']}/{item['total_pages']}")
            else:
                lines.append(f"- **Pages**: {item['current_page']}")
        if item.get("current_kindle_location") is not None:
            lines.append(f"- **Kindle location**: {item['current_kindle_location']}")
        if item.get("current_percent") is not None:
            try:
                lines.append(f"- **Percent**: {float(item['current_percent']):.1f}%")
            except (TypeError, ValueError):
                lines.append(f"- **Percent**: {item['current_percent']}")
        if item.get("current_audio_seconds") is not None:
            total_s = item.get("total_audio_seconds")
            cur_s = item.get("current_audio_seconds")
            if total_s:
                lines.append(f"- **Audiobook**: {_format_seconds(cur_s)}/{_format_seconds(total_s)}")
            else:
                lines.append(f"- **Audiobook**: {_format_seconds(cur_s)}")
        if not lines:
            lines.append("- **Progress**: (no updates yet)")
        return lines

    @commands.hybrid_group(name="reading", aliases=["read"], fallback="now", description="Track your reading progress (pages / Kindle / audiobook).")
    async def reading_group(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self.reading_now(ctx)

    @reading_group.command(name="start", description="Start tracking a new book/audiobook and set it as current.")
    async def reading_start(
        self,
        ctx: commands.Context,
        title: str,
        author: Optional[str] = None,
        format: Optional[str] = None,
        total_pages: Optional[int] = None,
        total_audio: Optional[str] = None,
    ):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        total_audio_seconds = _parse_duration_to_seconds(total_audio) if total_audio else None
        item_id = await self.bot.loop.run_in_executor(
            None,
            self.db_manager.create_reading_item,
            ctx.author.id,
            title,
            author,
            format,
            total_pages,
            total_audio_seconds,
        )
        if not item_id:
            await self._send(ctx, "❌ Could not create that reading entry.", ephemeral=not is_dm)
            return

        await self.bot.loop.run_in_executor(None, self.db_manager.set_current_reading_item_id, ctx.author.id, item_id)
        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_reading_item, ctx.author.id, item_id)

        embed = discord.Embed(title="📖 Tracking started", color=discord.Color.green())
        embed.add_field(name="Title", value=item.get("title", title), inline=False)
        if item.get("author"):
            embed.add_field(name="Author", value=item["author"], inline=True)
        if item.get("format"):
            embed.add_field(name="Format", value=item["format"], inline=True)
        if item.get("total_pages"):
            embed.add_field(name="Total pages", value=str(item["total_pages"]), inline=True)
        if item.get("total_audio_seconds"):
            embed.add_field(name="Total audio", value=_format_seconds(item["total_audio_seconds"]), inline=True)
        embed.set_footer(text="Use /reading update to log progress.")
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @reading_group.command(name="now", description="Show your current book/audiobook progress.")
    async def reading_now(self, ctx: commands.Context):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_reading_item, ctx.author.id)
        if not item:
            await self._send(ctx, "You’re not tracking anything yet. Use `/reading start`.", ephemeral=not is_dm)
            return

        embed = discord.Embed(title="📚 Current reading", color=discord.Color.blurple())
        embed.description = f"**{item.get('title', 'Untitled')}**" + (f"\nby *{item['author']}*" if item.get("author") else "")
        embed.add_field(name="Status", value=str(item.get("status", "reading")), inline=True)
        if item.get("format"):
            embed.add_field(name="Format", value=str(item["format"]), inline=True)
        embed.add_field(name="Progress", value="\n".join(self._progress_lines(item)), inline=False)
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @reading_group.command(name="update", description="Update progress (pages / kindle loc / percent / audiobook).")
    async def reading_update(
        self,
        ctx: commands.Context,
        page: Optional[int] = None,
        pages_delta: Optional[int] = None,
        kindle_loc: Optional[int] = None,
        percent: Optional[float] = None,
        audio: Optional[str] = None,
        audio_delta: Optional[str] = None,
        note: Optional[str] = None,
    ):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_reading_item, ctx.author.id)
        if not item:
            await self._send(ctx, "You’re not tracking anything yet. Use `/reading start`.", ephemeral=not is_dm)
            return

        audio_seconds = _parse_duration_to_seconds(audio) if audio else None
        audio_delta_seconds = _parse_duration_to_seconds(audio_delta) if audio_delta else None

        if (
            page is None
            and pages_delta is None
            and kindle_loc is None
            and percent is None
            and audio_seconds is None
            and audio_delta_seconds is None
            and not (isinstance(note, str) and note.strip())
        ):
            await self._send(
                ctx,
                "Provide at least one update: `page`, `pages_delta`, `kindle_loc`, `percent`, `audio`, `audio_delta`, or `note`.",
                ephemeral=not is_dm,
            )
            return

        updated = await self.bot.loop.run_in_executor(
            None,
            partial(
                self.db_manager.update_reading_progress,
                ctx.author.id,
                int(item["id"]),
                page=page,
                pages_delta=pages_delta,
                kindle_loc=kindle_loc,
                percent=percent,
                audio_seconds=audio_seconds,
                audio_delta_seconds=audio_delta_seconds,
                note=note,
            ),
        )
        if not updated:
            await self._send(ctx, "❌ Failed to save progress update.", ephemeral=not is_dm)
            return

        # If auto-finished, clear "current" pointer for this user/item.
        try:
            if str(updated.get("status")) == "finished":
                cur_id = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_reading_item_id, ctx.author.id)
                if cur_id is not None and int(cur_id) == int(updated.get("id")):
                    await self.bot.loop.run_in_executor(None, self.db_manager.set_current_reading_item_id, ctx.author.id, None)
        except Exception:
            pass

        if str(updated.get("status")) == "finished":
            embed = discord.Embed(title="🏁 Finished!", color=discord.Color.gold())
        else:
            embed = discord.Embed(title="✅ Progress updated", color=discord.Color.green())
        embed.description = f"**{updated.get('title', 'Untitled')}**" + (f"\nby *{updated['author']}*" if updated.get("author") else "")
        embed.add_field(name="Progress", value="\n".join(self._progress_lines(updated)), inline=False)
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @reading_group.command(name="finish", description="Mark your current book/audiobook as finished.")
    async def reading_finish(self, ctx: commands.Context):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_reading_item, ctx.author.id)
        if not item:
            await self._send(ctx, "You’re not tracking anything yet. Use `/reading start`.", ephemeral=not is_dm)
            return

        ok = await self.bot.loop.run_in_executor(None, self.db_manager.finish_reading_item, ctx.author.id, int(item["id"]))
        if not ok:
            await self._send(ctx, "❌ Failed to mark it finished.", ephemeral=not is_dm)
            return

        await self.bot.loop.run_in_executor(None, self.db_manager.set_current_reading_item_id, ctx.author.id, None)
        await self._send(ctx, f"🏁 Finished **{item.get('title', 'Untitled')}**!", ephemeral=not is_dm)

    @reading_group.command(name="list", description="List your active reading items.")
    async def reading_list(self, ctx: commands.Context):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        current_id = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_reading_item_id, ctx.author.id)
        items = await self.bot.loop.run_in_executor(None, self.db_manager.list_reading_items, ctx.author.id, ["reading", "paused"], 15)
        if not items:
            await self._send(ctx, "No active reading items. Use `/reading start`.", ephemeral=not is_dm)
            return

        embed = discord.Embed(title="📚 Active reading", color=discord.Color.blurple())
        lines: List[str] = []
        for it in items:
            title = it.get("title", "Untitled")
            author = f" — {it['author']}" if it.get("author") else ""
            status = it.get("status", "reading")
            prog = self._progress_lines(it)
            # One-line summary: prefer pages/percent/audio
            summary = prog[0].replace("- **", "").replace("**", "")
            marker = "👉 " if current_id is not None and int(it.get("id") or -1) == int(current_id) else ""
            lines.append(f"- {marker}**#{it.get('id')}**: **{title}**{author} ({status}) — {summary}")
        embed.description = "\n".join(lines)
        embed.set_footer(text="Tip: /reading switch <id> changes current.")
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @reading_group.command(name="switch", description="Switch your current reading item to a different active entry.")
    async def reading_switch(self, ctx: commands.Context, item_id: int):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_reading_item, ctx.author.id, int(item_id))
        if not item:
            await self._send(ctx, f"❌ I can’t find item **#{item_id}**.", ephemeral=not is_dm)
            return
        if str(item.get("status")) == "finished":
            await self._send(ctx, "That item is already finished. Pick an active one from `/reading list`.", ephemeral=not is_dm)
            return

        await self.bot.loop.run_in_executor(None, self.db_manager.set_current_reading_item_id, ctx.author.id, int(item_id))
        await self.reading_now(ctx)

    @reading_group.command(name="history", description="Show recent progress updates for your current item.")
    async def reading_history(self, ctx: commands.Context, limit: int = 10):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_reading_item, ctx.author.id)
        if not item:
            await self._send(ctx, "You’re not tracking anything yet. Use `/reading start`.", ephemeral=not is_dm)
            return

        limit = max(1, min(25, int(limit)))
        rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_reading_updates, ctx.author.id, int(item["id"]), limit)
        if not rows:
            await self._send(ctx, "No updates yet. Use `/reading update`.", ephemeral=not is_dm)
            return

        def fmt_row(r: dict) -> str:
            kind = r.get("kind")
            ts = r.get("created_at") or ""
            note = r.get("note")
            val = r.get("value")
            # Hide delta-only bookkeeping from history output.
            if kind in ("pages_delta", "audio_delta_seconds"):
                return ""
            if kind == "audio_seconds" and val is not None:
                v = _format_seconds(int(val))
            elif kind == "percent" and val is not None:
                v = f"{float(val):.1f}%"
            elif val is not None:
                v = str(int(val)) if float(val).is_integer() else str(val)
            else:
                v = ""
            main = f"**{kind}** {v}".strip()
            if note:
                main += f" — {note}"
            if ts:
                main += f" (`{ts}`)"
            return f"- {main}"

        embed = discord.Embed(title="🧾 Reading history", color=discord.Color.dark_teal())
        lines = [fmt_row(r) for r in rows]
        lines = [ln for ln in lines if ln]
        embed.description = f"**{item.get('title', 'Untitled')}**\n\n" + "\n".join(lines)
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @reading_group.group(name="goals", invoke_without_command=True, description="View or set reading goals (pages/minutes).")
    async def reading_goals_group(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self.reading_goals_view(ctx)

    @reading_goals_group.command(name="view", description="Show your reading goals.")
    async def reading_goals_view(self, ctx: commands.Context):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        uid = ctx.author.id
        pages_day = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_goal_pages_per_day", None)
        minutes_day = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_goal_minutes_per_day", None)
        pages_week = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_goal_pages_per_week", None)
        minutes_week = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_goal_minutes_per_week", None)

        embed = discord.Embed(title="🎯 Reading goals", color=discord.Color.blurple())
        embed.add_field(name="Daily", value=f"- Pages/day: **{pages_day if pages_day is not None else '—'}**\n- Minutes/day: **{minutes_day if minutes_day is not None else '—'}**", inline=False)
        embed.add_field(name="Weekly", value=f"- Pages/week: **{pages_week if pages_week is not None else '—'}**\n- Minutes/week: **{minutes_week if minutes_week is not None else '—'}**", inline=False)
        embed.set_footer(text="Set via /reading goals set ...  (times are UTC for reminder scheduling)")
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @reading_goals_group.command(name="set", description="Set your reading goals (any field optional).")
    async def reading_goals_set(
        self,
        ctx: commands.Context,
        pages_per_day: Optional[int] = None,
        minutes_per_day: Optional[int] = None,
        pages_per_week: Optional[int] = None,
        minutes_per_week: Optional[int] = None,
    ):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        uid = ctx.author.id
        updates = 0

        def norm_int(v: Optional[int]) -> Optional[int]:
            if v is None:
                return None
            try:
                vv = int(v)
                if vv <= 0:
                    return None
                return vv
            except Exception:
                return None

        pday = norm_int(pages_per_day)
        mday = norm_int(minutes_per_day)
        pweek = norm_int(pages_per_week)
        mweek = norm_int(minutes_per_week)

        if pages_per_day is not None:
            if pday is None:
                await self.bot.loop.run_in_executor(None, self.db_manager.delete_user_preference, uid, "reading_goal_pages_per_day")
            else:
                await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid, "reading_goal_pages_per_day", pday)
            updates += 1
        if minutes_per_day is not None:
            if mday is None:
                await self.bot.loop.run_in_executor(None, self.db_manager.delete_user_preference, uid, "reading_goal_minutes_per_day")
            else:
                await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid, "reading_goal_minutes_per_day", mday)
            updates += 1
        if pages_per_week is not None:
            if pweek is None:
                await self.bot.loop.run_in_executor(None, self.db_manager.delete_user_preference, uid, "reading_goal_pages_per_week")
            else:
                await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid, "reading_goal_pages_per_week", pweek)
            updates += 1
        if minutes_per_week is not None:
            if mweek is None:
                await self.bot.loop.run_in_executor(None, self.db_manager.delete_user_preference, uid, "reading_goal_minutes_per_week")
            else:
                await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid, "reading_goal_minutes_per_week", mweek)
            updates += 1

        if updates == 0:
            await self._send(ctx, "Provide at least one value to set (or set it to 0 to clear).", ephemeral=not is_dm)
            return
        await self._send(ctx, "✅ Goals updated.", ephemeral=not is_dm)

    @reading_group.command(name="stats", description="Show your reading stats (today/week + streak).")
    async def reading_stats(self, ctx: commands.Context, days: int = 7):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        uid = ctx.author.id
        days = max(1, min(365, int(days)))
        today_iso = self._utc_today_iso()
        week_start, week_end = self._week_start_end_utc()

        today_totals = await self.bot.loop.run_in_executor(None, self.db_manager.get_reading_day_totals, uid, today_iso)
        week_totals = await self.bot.loop.run_in_executor(None, self.db_manager.get_reading_range_totals, uid, week_start, week_end)
        daily = await self.bot.loop.run_in_executor(None, self.db_manager.get_reading_daily_totals, uid, days)
        cur_streak, best_streak = self._compute_streaks_from_daily(daily)

        pages_day_goal = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_goal_pages_per_day", None)
        minutes_day_goal = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_goal_minutes_per_day", None)
        pages_week_goal = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_goal_pages_per_week", None)
        minutes_week_goal = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_goal_minutes_per_week", None)

        today_minutes = self._seconds_to_minutes(int(today_totals.get("audio_seconds", 0)))
        week_minutes = self._seconds_to_minutes(int(week_totals.get("audio_seconds", 0)))

        def prog(val: int, goal: Optional[int]) -> str:
            if goal is None:
                return f"**{val}**"
            return f"**{val}/{int(goal)}**"

        embed = discord.Embed(title="📈 Reading stats", color=discord.Color.dark_teal())
        embed.add_field(
            name=f"Today ({today_iso})",
            value=f"- Pages: {prog(int(today_totals.get('pages', 0)), pages_day_goal)}\n- Minutes: {prog(today_minutes, minutes_day_goal)}",
            inline=False,
        )
        embed.add_field(
            name=f"This week ({week_start} → {week_end})",
            value=f"- Pages: {prog(int(week_totals.get('pages', 0)), pages_week_goal)}\n- Minutes: {prog(week_minutes, minutes_week_goal)}",
            inline=False,
        )
        embed.add_field(name="Streaks (UTC day)", value=f"- Current: **{cur_streak}** days\n- Best: **{best_streak}** days", inline=False)
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @reading_group.command(name="chart", description="Show a weekly pages/minutes chart.")
    async def reading_chart(self, ctx: commands.Context, metric: str = "pages"):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        metric_l = (metric or "").lower().strip()
        if metric_l not in ("pages", "minutes"):
            await self._send(ctx, "Metric must be `pages` or `minutes`.", ephemeral=not is_dm)
            return

        rows = await self.bot.loop.run_in_executor(None, self.db_manager.get_reading_daily_totals, ctx.author.id, 7)
        labels = []
        values = []
        for r in rows:
            day = r.get("day")
            if not isinstance(day, str):
                continue
            # Show MM-DD for compactness
            labels.append(day[5:] if len(day) >= 10 else day)
            if metric_l == "pages":
                values.append(int(r.get("pages") or 0))
            else:
                values.append(self._seconds_to_minutes(int(r.get("audio_seconds") or 0)))

        title = "Weekly reading — pages" if metric_l == "pages" else "Weekly reading — minutes"
        unit = "pages" if metric_l == "pages" else "minutes"
        img = await self.bot.loop.run_in_executor(None, partial(get_weekly_reading_chart_image, title, labels, values, unit=unit))
        if not img:
            await self._send(ctx, "❌ Could not generate chart right now.", ephemeral=not is_dm)
            return

        filename = "reading_weekly_pages.png" if metric_l == "pages" else "reading_weekly_minutes.png"
        file = discord.File(fp=img, filename=filename)
        if ctx.interaction:
            # Defer already happened, so use followup with file.
            await ctx.interaction.followup.send(content="Here you go:", file=file, ephemeral=not is_dm)
        else:
            await ctx.send(content="Here you go:", file=file)

    @reading_group.command(name="export", description="Export your reading data as JSON.")
    async def reading_export(self, ctx: commands.Context):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        uid = ctx.author.id
        items = await self.bot.loop.run_in_executor(None, self.db_manager.list_reading_items_all, uid, 500)
        updates = await self.bot.loop.run_in_executor(None, self.db_manager.list_reading_updates_all, uid, 5000)
        prefs = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_all_preferences, uid)
        reading_prefs = {k: v for k, v in (prefs or {}).items() if isinstance(k, str) and k.startswith("reading_")}

        payload = {
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "user_id": str(uid),
            "reading_preferences": reading_prefs,
            "reading_items": items,
            "reading_updates": updates,
        }
        data = io.BytesIO()
        data.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        data.seek(0)

        file = discord.File(fp=data, filename="reading_export.json")
        if ctx.interaction:
            await ctx.interaction.followup.send(content="✅ Export ready:", file=file, ephemeral=not is_dm)
        else:
            await ctx.send(content="✅ Export ready:", file=file)

    @reading_group.command(name="import", description="Import reading items from Goodreads/StoryGraph CSV.")
    async def reading_import(
        self,
        ctx: commands.Context,
        csv_file: Optional[discord.Attachment] = None,
        source: Optional[str] = None,
    ):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        # Prefix-command fallback: use first attachment in the message.
        if csv_file is None and getattr(ctx, "message", None) and getattr(ctx.message, "attachments", None):
            if ctx.message.attachments:
                csv_file = ctx.message.attachments[0]

        if csv_file is None:
            await self._send(ctx, "Attach a CSV file (Goodreads or StoryGraph) to import.", ephemeral=not is_dm)
            return

        try:
            raw_bytes = await csv_file.read()
        except Exception:
            await self._send(ctx, "❌ Could not download that file.", ephemeral=not is_dm)
            return

        try:
            text = raw_bytes.decode("utf-8-sig", errors="replace")
        except Exception:
            text = raw_bytes.decode(errors="replace")

        src = (source or "").strip().lower()
        # Heuristic auto-detect
        if not src:
            if "exclusive shelf" in text.lower():
                src = "goodreads"
            elif "storygraph" in (csv_file.filename or "").lower():
                src = "storygraph"

        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            await self._send(ctx, "No rows found in CSV.", ephemeral=not is_dm)
            return

        created = 0
        finished = 0
        uid = ctx.author.id

        def pick(d: dict, *keys: str) -> Optional[str]:
            for k in keys:
                if k in d and isinstance(d[k], str) and d[k].strip():
                    return d[k].strip()
            return None

        for r in rows[:2000]:
            title = pick(r, "Title", "title")
            if not title:
                continue
            author = pick(r, "Author", "Authors", "author", "authors")
            fmt = pick(r, "Binding", "Format", "format")

            pages_s = pick(r, "Number of Pages", "Pages", "pages")
            total_pages = None
            if pages_s:
                try:
                    total_pages = int(float(pages_s))
                except Exception:
                    total_pages = None

            status = "reading"
            finished_at_iso = None
            if src == "goodreads":
                shelf = (pick(r, "Exclusive Shelf", "Bookshelves") or "").lower()
                if "read" in shelf:
                    status = "finished"
                elif "currently-reading" in shelf:
                    status = "reading"
                elif "to-read" in shelf:
                    status = "paused"
                date_read = pick(r, "Date Read")
                if date_read:
                    # Goodreads uses m/d/Y sometimes; accept ISO if provided
                    try:
                        dt = datetime.strptime(date_read, "%Y-%m-%d")
                        finished_at_iso = dt.date().isoformat()
                    except Exception:
                        try:
                            dt = datetime.strptime(date_read, "%m/%d/%Y")
                            finished_at_iso = dt.date().isoformat()
                        except Exception:
                            finished_at_iso = None
            else:
                sg_status = (pick(r, "Status", "status") or "").lower()
                if "read" in sg_status or "finished" in sg_status:
                    status = "finished"
                elif "currently" in sg_status:
                    status = "reading"
                elif "to-read" in sg_status:
                    status = "paused"
                end_date = pick(r, "End Date", "Date Read")
                if end_date:
                    for fmt_date in ("%Y-%m-%d", "%m/%d/%Y"):
                        try:
                            dt = datetime.strptime(end_date, fmt_date)
                            finished_at_iso = dt.date().isoformat()
                            break
                        except Exception:
                            continue

            item_id = await self.bot.loop.run_in_executor(
                None,
                partial(
                    self.db_manager.import_reading_item,
                    uid,
                    title,
                    author=author,
                    format=fmt,
                    status=status,
                    total_pages=total_pages,
                    finished_at_iso=finished_at_iso,
                ),
            )
            if item_id:
                created += 1
                if status == "finished":
                    finished += 1

        await self._send(ctx, f"✅ Imported **{created}** items ({finished} finished).", ephemeral=not is_dm)

    @reading_group.group(name="reminders", invoke_without_command=True, description="Configure daily reading reminders (respects DND).")
    async def reading_reminders_group(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self.reading_reminders_view(ctx)

    @reading_reminders_group.command(name="view", description="Show your reading reminder settings.")
    async def reading_reminders_view(self, ctx: commands.Context):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        uid = ctx.author.id
        enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_reminder_enabled", False)
        remind_time = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, uid, "reading_reminder_time", "20:00")
        embed = discord.Embed(title="⏰ Reading reminders", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value="✅ On" if enabled else "❌ Off", inline=True)
        embed.add_field(name="Time (UTC)", value=f"`{remind_time}`", inline=True)
        embed.set_footer(text="Set via /reading reminders set <on|off> <HH:MM>")
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @reading_reminders_group.command(name="set", description="Turn reminders on/off and set the time (UTC).")
    async def reading_reminders_set(self, ctx: commands.Context, enabled: str, remind_time: Optional[str] = None):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        uid = ctx.author.id
        enabled_l = (enabled or "").lower().strip()
        if enabled_l not in ("on", "off"):
            await self._send(ctx, "Use `on` or `off`.", ephemeral=not is_dm)
            return

        if enabled_l == "off":
            await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid, "reading_reminder_enabled", False)
            await self._send(ctx, "✅ Reading reminders disabled.", ephemeral=not is_dm)
            return

        # Validate time
        if remind_time is not None:
            m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", remind_time.strip())
            if not m:
                await self._send(ctx, "Time must be `HH:MM` (UTC), e.g. `20:00`.", ephemeral=not is_dm)
                return
            await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid, "reading_reminder_time", remind_time.strip())

        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid, "reading_reminder_enabled", True)
        await self._send(ctx, "✅ Reading reminders enabled.", ephemeral=not is_dm)

    @tasks.loop(minutes=5)
    async def reading_reminders(self):
        """
        Sends a best-effort daily reminder if:
        - reminders enabled
        - it's after the configured reminder time (UTC)
        - user has not logged reading deltas today
        - not already reminded today
        - user is not in DND right now
        """
        if not self.db_manager:
            return

        now_utc = datetime.now(timezone.utc)
        today_iso = now_utc.date().isoformat()
        now_minutes = now_utc.hour * 60 + now_utc.minute

        enabled_rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_users_with_preference, "reading_reminder_enabled")
        time_rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_users_with_preference, "reading_reminder_time")
        last_rows = await self.bot.loop.run_in_executor(None, self.db_manager.list_users_with_preference, "reading_reminder_last_sent_day")

        enabled_map = {r["user_id"]: bool(r.get("value")) for r in enabled_rows if isinstance(r, dict) and "user_id" in r}
        time_map = {r["user_id"]: r.get("value") for r in time_rows if isinstance(r, dict) and "user_id" in r}
        last_map = {r["user_id"]: r.get("value") for r in last_rows if isinstance(r, dict) and "user_id" in r}

        for uid_str, enabled in enabled_map.items():
            if not enabled:
                continue
            try:
                uid = int(uid_str)
            except Exception:
                continue

            last_day = last_map.get(uid_str)
            if isinstance(last_day, str) and last_day == today_iso:
                continue

            t = time_map.get(uid_str) or "20:00"
            try:
                tm = datetime.strptime(str(t), "%H:%M").time()
            except Exception:
                tm = dt_time(20, 0)
            remind_minutes = tm.hour * 60 + tm.minute
            if now_minutes < remind_minutes:
                continue

            # Skip if already read today
            totals = await self.bot.loop.run_in_executor(None, self.db_manager.get_reading_day_totals, uid, today_iso)
            if int(totals.get("pages", 0)) > 0 or int(totals.get("audio_seconds", 0)) > 0:
                continue

            # Respect DND
            if await self._is_user_in_dnd(uid):
                continue

            user = self.bot.get_user(uid)
            if not user:
                try:
                    user = await self.bot.fetch_user(uid)
                except (discord.NotFound, discord.HTTPException):
                    continue

            try:
                await user.send("📚 Quick reminder: you haven’t logged any reading today. Even 5 minutes counts. (`/reading update`)")
                await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, uid, "reading_reminder_last_sent_day", today_iso)
            except discord.Forbidden:
                continue
            except discord.HTTPException:
                continue

    @reading_reminders.before_loop
    async def before_reading_reminders(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ReadingProgressCog(bot, db_manager=bot.db_manager))
    logger.info("ReadingProgressCog has been loaded.")

