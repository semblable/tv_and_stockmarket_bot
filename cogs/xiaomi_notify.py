import os
import logging
from typing import Optional

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)


def _base_url() -> str:
    # Prefer explicit base URL (HTTPS, public) for correct links in Discord.
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip()
    return base.rstrip("/")


class XiaomiNotifyCog(commands.Cog, name="Xiaomi / Notify"):
    def __init__(self, bot: commands.Bot, db_manager) -> None:
        self.bot = bot
        self.db_manager = db_manager

    async def _send_ctx(self, ctx: commands.Context, content: str, *, ephemeral: bool = True) -> None:
        if getattr(ctx, "interaction", None):
            await ctx.send(content, ephemeral=ephemeral)
        else:
            await ctx.send(content)

    async def _dm(self, user: discord.abc.User, content: str) -> bool:
        try:
            await user.send(content)
            return True
        except Exception:
            return False

    def _build_webhook_url(self, token: str) -> str:
        base = _base_url()
        if base:
            return f"{base}/webhook/xiaomi/{token}"
        # Fallback: user must replace with their public bot URL.
        return f"https://YOUR-BOT-DOMAIN.example/webhook/xiaomi/{token}"

    # NOTE: don't use fallback="status" here because it implicitly registers a "status"
    # subcommand, which conflicts with our explicit /xiaomi status command.
    @commands.hybrid_group(name="xiaomi")
    async def xiaomi_group(self, ctx: commands.Context) -> None:
        """Manage Notify for Xiaomi webhook ingestion."""
        if ctx.invoked_subcommand is None:
            await self.xiaomi_status(ctx)

    @xiaomi_group.command(name="webhook", aliases=["link", "url"])
    async def xiaomi_webhook(self, ctx: commands.Context) -> None:
        """
        Create (or show) your personal Xiaomi webhook URL.
        This URL is secret and uniquely routes data to your Discord user.
        """
        if not self.db_manager:
            await self._send_ctx(ctx, "❌ Database is not available right now.", ephemeral=True)
            return

        user_id = int(ctx.author.id)
        row = await self.bot.loop.run_in_executor(None, self.db_manager.create_or_get_xiaomi_webhook, user_id)
        token = str(row.get("token") or "").strip()
        if not token:
            await self._send_ctx(ctx, "❌ Failed to create your webhook token.", ephemeral=True)
            return

        url = self._build_webhook_url(token)
        dm_ok = await self._dm(
            ctx.author,
            (
                "**Your Notify/Xiaomi webhook URL (keep this private):**\n"
                f"`{url}`\n\n"
                "In Notify for Xiaomi, set the webhook to **POST** to that URL.\n"
                "Tip: If Notify can only send limited fields, use Tasker to POST JSON to the same URL."
            ),
        )

        if dm_ok:
            await self._send_ctx(ctx, "✅ I sent your personal webhook URL via DM.", ephemeral=True)
        else:
            await self._send_ctx(
                ctx,
                "❌ I couldn't DM you (do you have DMs disabled?).\n"
                f"Your webhook URL is: `{url}`\n"
                "**Warning:** don’t run this in a public channel.",
                ephemeral=True,
            )

    @xiaomi_group.command(name="rotate")
    async def xiaomi_rotate(self, ctx: commands.Context) -> None:
        """Invalidate your old URL and generate a new secret URL."""
        if not self.db_manager:
            await self._send_ctx(ctx, "❌ Database is not available right now.", ephemeral=True)
            return

        user_id = int(ctx.author.id)
        row = await self.bot.loop.run_in_executor(None, self.db_manager.rotate_xiaomi_webhook, user_id)
        token = str(row.get("token") or "").strip()
        url = self._build_webhook_url(token)

        dm_ok = await self._dm(
            ctx.author,
            (
                "✅ **Your Xiaomi webhook URL was rotated.** Old URL is now invalid.\n"
                f"**New URL:** `{url}`"
            ),
        )
        if dm_ok:
            await self._send_ctx(ctx, "✅ Rotated. I sent the new URL via DM.", ephemeral=True)
        else:
            await self._send_ctx(ctx, f"✅ Rotated. New URL: `{url}`", ephemeral=True)

    @xiaomi_group.command(name="enable")
    async def xiaomi_enable(self, ctx: commands.Context) -> None:
        """Enable processing of your Xiaomi webhook URL."""
        user_id = int(ctx.author.id)
        await self.bot.loop.run_in_executor(None, self.db_manager.create_or_get_xiaomi_webhook, user_id)
        ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_xiaomi_webhook_enabled, user_id, True)
        await self._send_ctx(ctx, "✅ Enabled." if ok else "❌ Failed to enable.", ephemeral=True)

    @xiaomi_group.command(name="disable")
    async def xiaomi_disable(self, ctx: commands.Context) -> None:
        """Disable processing of your Xiaomi webhook URL."""
        user_id = int(ctx.author.id)
        ok = await self.bot.loop.run_in_executor(None, self.db_manager.set_xiaomi_webhook_enabled, user_id, False)
        await self._send_ctx(ctx, "✅ Disabled." if ok else "❌ Failed to disable.", ephemeral=True)

    @xiaomi_group.command(name="status")
    async def xiaomi_status(self, ctx: commands.Context) -> None:
        """Show whether your Xiaomi webhook is enabled and when it last received data."""
        if not self.db_manager:
            await self._send_ctx(ctx, "❌ Database is not available right now.", ephemeral=True)
            return

        user_id = int(ctx.author.id)
        row = await self.bot.loop.run_in_executor(None, self.db_manager.get_xiaomi_webhook, user_id)
        if not row:
            await self._send_ctx(
                ctx,
                "No Xiaomi webhook configured yet.\nUse `/xiaomi webhook` to create your personal URL.",
                ephemeral=True,
            )
            return

        enabled = bool(row.get("enabled"))
        last_seen = row.get("last_seen_at") or "never"
        await self._send_ctx(
            ctx,
            f"**Enabled:** `{enabled}`\n**Last received:** `{last_seen}`\nUse `/xiaomi webhook` to DM yourself the URL.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(XiaomiNotifyCog(bot, db_manager=bot.db_manager))
    logger.info("XiaomiNotifyCog has been loaded.")

