import logging
import re
from functools import partial
from typing import Optional, List

import discord
from discord.ext import commands

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
            lines.append(f"- **#{it.get('id')}**: **{title}**{author} ({status}) — {summary}")
        embed.description = "\n".join(lines)
        embed.set_footer(text="Tip: /reading start sets current; /reading now shows current.")
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

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
        embed.description = f"**{item.get('title', 'Untitled')}**\n\n" + "\n".join(fmt_row(r) for r in rows)
        await self._send(ctx, embed=embed, ephemeral=not is_dm)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReadingProgressCog(bot, db_manager=bot.db_manager))
    logger.info("ReadingProgressCog has been loaded.")

