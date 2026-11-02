# cogs/settings.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import re # For DND time validation
import logging # Import logging
from typing import Optional
import secrets
import io
import gzip
import os
import tempfile
import datetime

import config

logger = logging.getLogger(__name__)

# Helper to create a consistent embed for settings
def create_settings_embed(ctx, user_preferences, weather_schedules):
    """Creates a Discord embed to display user settings."""
    embed = discord.Embed(
        title=f"⚙️ Notification Settings for {ctx.author.display_name}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)

    # Weather Settings
    weather_loc = user_preferences.get('weather_default_location', 'Not Set')
    schedules_count = len(weather_schedules)
    
    embed.add_field(
        name="🌤️ Weather Settings",
        value=f"Default Location: **{weather_loc}**\n"
              f"Active Schedules: **{schedules_count}**\n"
              f"`{ctx.prefix}settings weather_default <location>`\n"
              f"`{ctx.prefix}settings weather_schedule add <HH:MM> [location]`\n"
              f"`{ctx.prefix}settings weather_schedule remove <HH:MM|all>`\n"
              f"`{ctx.prefix}settings weather_schedule list`",
        inline=False
    )

    # TV Show DM Overview
    tv_overview_status = "✅ On" if user_preferences.get('tv_show_dm_overview', True) else "❌ Off"
    embed.add_field(
        name="📺 TV Show DM Overview",
        value=f"Include episode plot summary in notification DMs.\nStatus: **{tv_overview_status}**\n"
              f"`{ctx.prefix}settings tv_overview <on|off>`",
        inline=False
    )

    # TV Monthly Digest
    tv_digest_status = "✅ On" if user_preferences.get('tv_monthly_digest', True) else "❌ Off"
    embed.add_field(
        name="📅 Monthly TV Digest",
        value=f"Automated release calendar sent on the 1st of every month.\nStatus: **{tv_digest_status}**\n"
              f"`{ctx.prefix}settings tv_digest <on|off>`",
        inline=False
    )

    # Do Not Disturb (DND)
    dnd_enabled = user_preferences.get('dnd_enabled', False)
    dnd_status = "🌙 Active" if dnd_enabled else "☀️ Inactive"
    dnd_start = user_preferences.get('dnd_start_time', "22:00")
    dnd_end = user_preferences.get('dnd_end_time', "07:00")

    dnd_value = (
        f"Suppress notifications during specified times.\n"
        f"Status: **{dnd_status}**\n"
        f"Period: `{dnd_start}` - `{dnd_end}`\n"
        f"`{ctx.prefix}settings dnd <on|off>`\n"
        f"`{ctx.prefix}settings dnd <HH:MM-HH:MM>` (e.g., `22:00-07:00`)"
    )
    embed.add_field(
        name="🌙 Do Not Disturb (DND)",
        value=dnd_value,
        inline=False
    )

    # Timezone (used by /remind_at)
    tz_name = user_preferences.get("timezone", "Europe/Warsaw")
    tz_display = str(tz_name or "Europe/Warsaw")
    if tz_display.strip() in ("Europe/Warsaw", "CET"):
        tz_display = "CET/CEST"
    embed.add_field(
        name="🕒 Timezone",
        value=(
            f"Used for time-based reminders (e.g. `/remind_at 18:00`).\n"
            f"Current: `{tz_display}`\n"
            f"`{ctx.prefix}settings timezone <IANA TZ or UTC>` (e.g. `Europe/Warsaw`, `UTC`)"
        ),
        inline=False,
    )

    # Report Webhook Link
    report_base = user_preferences.get("report_webhook_base_url")
    report_base_display = report_base if report_base else "Default (server-configured)"
    embed.add_field(
        name="🔗 Report Webhook Link",
        value=(
            f"Base URL: `{report_base_display}`\n"
            f"`{ctx.prefix}settings webhook_link [name]`\n"
            f"`{ctx.prefix}settings webhook_list`\n"
            f"`{ctx.prefix}settings webhook_base <url|clear>`"
        ),
        inline=False,
    )

POPULAR_TIMEZONES = [
    "Europe/Warsaw",
    "UTC",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Toronto",
    "Asia/Tokyo",
    "Asia/Singapore",
    "Asia/Dubai",
    "Australia/Sydney",
]

async def timezone_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current_lower = (current or "").lower()
    return [
        app_commands.Choice(name=tz, value=tz)
        for tz in POPULAR_TIMEZONES
        if current_lower in tz.lower()
    ][:25]

def _generate_backup_archive(db_manager) -> bytes:
    """
    Creates a hot SQLite backup, compresses it with gzip, and returns the bytes.
    """
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        ok = db_manager.backup_db(tmp_path)
        if not ok:
            return b""
        with open(tmp_path, "rb") as f_in:
            raw_bytes = f_in.read()
        return gzip.compress(raw_bytes)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class SettingsCog(commands.Cog, name="Settings"):
    def __init__(self, bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager # Injected dependency

    def cog_load(self):
        self.monthly_backup_task.start()

    def cog_unload(self):
        self.monthly_backup_task.cancel()

    async def _send_ctx(self, ctx: commands.Context, content: str, *, ephemeral: bool = True) -> None:
        """
        Send helper for hybrid commands:
        - Uses ephemeral only for interactions
        - Avoids passing ephemeral kwarg for prefix commands (would raise)
        """
        if getattr(ctx, "interaction", None):
            await ctx.send(content, ephemeral=ephemeral)
        else:
            await ctx.send(content)

    @commands.hybrid_group(name="settings", aliases=["prefs"], fallback="view")
    async def settings_group(self, ctx: commands.Context):
        """Manage your notification preferences."""
        if ctx.invoked_subcommand is None:
            await self.view_settings(ctx)

    @settings_group.command(name="current", aliases=["show", "view"])
    async def view_settings(self, ctx: commands.Context):
        """Displays your current notification settings."""
        user_id = ctx.author.id
        preferences = {
            "tv_show_dm_overview": await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "tv_show_dm_overview", True),
            "dnd_enabled": await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_enabled", False),
            "dnd_start_time": await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_start_time", "22:00"),
            "dnd_end_time": await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_end_time", "07:00"),
            "weather_default_location": await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "weather_default_location", "Not Set"),
            "timezone": await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "timezone", "Europe/Warsaw"),
            "report_webhook_base_url": await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "report_webhook_base_url", None),
        }
        weather_schedules = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_weather_schedules, user_id)
        
        embed = create_settings_embed(ctx, preferences, weather_schedules)
        await ctx.send(embed=embed)

    @settings_group.command(name="weather_default")
    async def set_weather_default(self, ctx: commands.Context, *, location: str):
        """Sets your default weather location."""
        user_id = ctx.author.id
        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "weather_default_location", location)
        await ctx.send(f"✅ Default weather location set to: **{location}**")

    @settings_group.group(name="weather_schedule", invoke_without_command=True)
    async def weather_schedule_group(self, ctx: commands.Context):
        """Manage scheduled weather notifications."""
        if ctx.invoked_subcommand is None:
            await self.list_weather_schedules(ctx)

    @weather_schedule_group.command(name="add")
    async def add_weather_schedule(self, ctx: commands.Context, time: str, *, location: Optional[str] = None):
        """
        Add a scheduled weather notification.
        Time must be in HH:MM format (UTC). Location is optional if default is set.
        """
        # Validate time
        time_pattern = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
        if not time_pattern.match(time):
            await self._send_ctx(ctx, "❌ Invalid time format. Please use HH:MM (24-hour format, e.g., 08:00).", ephemeral=True)
            return
        
        user_id = ctx.author.id
        await self.bot.loop.run_in_executor(None, self.db_manager.add_weather_schedule, user_id, time, location)
        
        msg = f"✅ Scheduled weather notification for **{time}** UTC."
        if location:
            msg += f" (Location: {location})"
        else:
            msg += " (Using default location)"
        await self._send_ctx(ctx, msg, ephemeral=True)

    @weather_schedule_group.command(name="remove")
    async def remove_weather_schedule(self, ctx: commands.Context, time: str):
        """Remove a scheduled weather notification."""
        time_in = str(time or "").strip().lower()
        user_id = ctx.author.id
        # Special: remove all schedules
        if time_in in {"all", "*", "clear"}:
            await self.bot.loop.run_in_executor(None, self.db_manager.clear_weather_schedules, user_id)
            await self._send_ctx(ctx, "✅ Removed **all** weather schedules.", ephemeral=True)
            return

        # Validate time
        time_pattern = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
        if not time_pattern.match(str(time or "").strip()):
            await self._send_ctx(ctx, "❌ Invalid time format. Use HH:MM (e.g., 08:00) or `all`.", ephemeral=True)
            return

        await self.bot.loop.run_in_executor(None, self.db_manager.remove_weather_schedule, user_id, str(time).strip())
        await self._send_ctx(ctx, f"✅ Removed weather schedule for **{str(time).strip()}**.", ephemeral=True)

    @weather_schedule_group.command(name="clear")
    async def clear_weather_schedule(self, ctx: commands.Context):
        """Remove all scheduled weather notifications."""
        user_id = ctx.author.id
        await self.bot.loop.run_in_executor(None, self.db_manager.clear_weather_schedules, user_id)
        await self._send_ctx(ctx, "✅ Removed **all** weather schedules.", ephemeral=True)

    @weather_schedule_group.command(name="list")
    async def list_weather_schedules(self, ctx: commands.Context):
        """List your scheduled weather notifications."""
        user_id = ctx.author.id
        schedules = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_weather_schedules, user_id)
        
        if not schedules:
            await self._send_ctx(ctx, "You have no scheduled weather notifications.", ephemeral=True)
            return
            
        embed = discord.Embed(title="📅 Your Weather Schedules", color=discord.Color.blue())
        description = ""
        for s in schedules:
            loc = s['location'] or "Default"
            description += f"• **{s['schedule_time']}** UTC - {loc}\n"
        
        embed.description = description
        if getattr(ctx, "interaction", None):
            await ctx.send(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

    @settings_group.command(name="tv_overview", aliases=["tvoverview"])
    async def set_tv_overview(self, ctx: commands.Context, new_status: str):
        """
        Toggle episode overview in TV show notification DMs.
        Usage: !settings tv_overview <on|off>
        """
        user_id = ctx.author.id
        new_status_lower = new_status.lower()

        if new_status_lower not in ["on", "off"]:
            await ctx.send(f"Invalid status: `{new_status}`. Please use `on` or `off`.", ephemeral=True)
            return

        preference_value = True if new_status_lower == "on" else False
        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "tv_show_dm_overview", preference_value)
        
        status_text = "✅ On" if preference_value else "❌ Off"
        await ctx.send(f"📺 TV Show DM Overview preference updated to: **{status_text}**.")
        # Optionally, show all settings again
        # await self.view_settings(ctx)

    @settings_group.command(name="tv_digest", aliases=["tvdigest", "monthly_digest", "tv_monthly_digest"])
    async def set_tv_digest(self, ctx: commands.Context, new_status: str):
        """
        Toggle automated monthly TV release calendar DMs on the 1st of each month.
        Usage: !settings tv_digest <on|off>
        """
        user_id = ctx.author.id
        new_status_lower = new_status.lower().strip()

        if new_status_lower in ["on", "enable", "enabled", "true", "yes", "1"]:
            preference_value = True
        elif new_status_lower in ["off", "disable", "disabled", "false", "no", "0"]:
            preference_value = False
        else:
            await ctx.send(f"Invalid status: `{new_status}`. Please use `on` or `off`.", ephemeral=True)
            return

        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "tv_monthly_digest", preference_value)
        status_text = "✅ On (1st of month at 09:00)" if preference_value else "❌ Off"
        await ctx.send(f"📅 Monthly TV Digest preference updated to: **{status_text}**.")

    @settings_group.command(name="dnd")
    async def set_dnd(self, ctx: commands.Context, *, dnd_setting: str):
        """
        Configure Do Not Disturb (DND) settings.
        Usage:
        !settings dnd <on|off>
        !settings dnd <HH:MM-HH:MM> (e.g., 22:00-07:00)
        """
        user_id = ctx.author.id
        setting_lower = dnd_setting.lower()

        time_pattern = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
        time_match = time_pattern.match(dnd_setting)

        if setting_lower == "on":
            await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "dnd_enabled", True)
            # Ensure times exist if enabling DND globally
            _ = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_start_time", "22:00")
            _ = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_end_time", "07:00")
            await ctx.send("🌙 Do Not Disturb (DND) is now **Active**.")
        elif setting_lower == "off":
            await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "dnd_enabled", False)
            await ctx.send("☀️ Do Not Disturb (DND) is now **Inactive**.")
        elif time_match:
            start_time_str = f"{time_match.group(1)}:{time_match.group(2)}"
            end_time_str = f"{time_match.group(3)}:{time_match.group(4)}"
            
            # Basic validation: Ensure start and end times are valid HH:MM format (already done by regex)
            # More complex validation (e.g. end time after start time, crossing midnight) can be added
            # For now, we store them as strings.
            
            await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "dnd_start_time", start_time_str)
            await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "dnd_end_time", end_time_str)
            await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "dnd_enabled", True) # Enable DND when times are set
            await ctx.send(f"🌙 DND period set to **{start_time_str} - {end_time_str}** and DND is **Active**.")
        else:
            await ctx.send(
                f"Invalid DND setting: `{dnd_setting}`.\n"
                f"Use `on`, `off`, or a time range like `HH:MM-HH:MM` (e.g., `22:00-07:00`).",
                ephemeral=True
            )
            return
        
        # Optionally, show all settings again
    @settings_group.command(name="timezone", aliases=["tz"])
    @app_commands.describe(tz_name="Your IANA timezone name (e.g. Europe/Warsaw, UTC, America/New_York)")
    @app_commands.autocomplete(tz_name=timezone_autocomplete)
    async def set_timezone(self, ctx: commands.Context, tz_name: str):
        """
        Set your timezone (used by /remind_at).
        Examples: UTC, Europe/Warsaw, America/New_York
        """
        name = (tz_name or "").strip()
        if not name:
            if getattr(ctx, "interaction", None):
                await ctx.send("❌ Please provide a timezone, e.g. `UTC` or `Europe/Warsaw`.", ephemeral=True)
            else:
                await ctx.send("❌ Please provide a timezone, e.g. `UTC` or `Europe/Warsaw`.")
            return

        # Normalize common aliases
        if name.upper() in ("CET", "CEST"):
            name = "Europe/Warsaw"

        # Validate if zoneinfo is available
        try:
            from zoneinfo import ZoneInfo  # type: ignore
            if name.upper() not in ("UTC", "ETC/UTC", "Z"):
                ZoneInfo(name)
        except Exception:
            # If zoneinfo missing or invalid tz, accept only UTC-like values.
            if name.upper() not in ("UTC", "ETC/UTC", "Z"):
                if getattr(ctx, "interaction", None):
                    await ctx.send("❌ Unknown timezone. Use `UTC` or a valid IANA name like `Europe/Warsaw`.", ephemeral=True)
                else:
                    await ctx.send("❌ Unknown timezone. Use `UTC` or a valid IANA name like `Europe/Warsaw`.")
                return
            name = "UTC"

        await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, ctx.author.id, "timezone", name)
        if name == "Europe/Warsaw":
            await ctx.send("✅ Timezone set to `CET/CEST`.")
        else:
            await ctx.send(f"✅ Timezone set to `{name}`.")

    @settings_group.command(name="webhook_link", aliases=["webhook", "report_link", "report_webhook"])
    @app_commands.describe(
        name="Webhook label/name (e.g. 'linguaread', 'server'). Defaults to 'default'.",
        reset="Generate a new link for this label (invalidates old link for this label only).",
    )
    async def webhook_link(self, ctx: commands.Context, name: str = "default", reset: bool = False):
        """
        Generate or view a per-user webhook link for receiving reports.
        Supports multiple named webhooks per account.
        """
        user_id = ctx.author.id
        webhook_name = (name or "default").strip().lower()
        if not re.match(r"^[a-zA-Z0-9_\-\.]{1,32}$", webhook_name):
            await self._send_ctx(
                ctx,
                "❌ Invalid webhook name. Use 1-32 alphanumeric characters, dashes, or underscores.",
                ephemeral=True,
            )
            return

        webhooks = await self.bot.loop.run_in_executor(
            None, self.db_manager.get_user_webhooks, user_id
        )
        token = webhooks.get(webhook_name)
        if reset or not token:
            token = secrets.token_urlsafe(32)
            await self.bot.loop.run_in_executor(
                None, self.db_manager.set_user_webhook, user_id, webhook_name, token
            )

        pref_base_url = await self.bot.loop.run_in_executor(
            None, self.db_manager.get_user_preference, user_id, "report_webhook_base_url", None
        )
        base_url = str(pref_base_url or getattr(config, "WEBHOOK_BASE_URL", "http://localhost:5000") or "http://localhost:5000")
        base_url = base_url.rstrip("/")
        link = f"{base_url}/webhook/report/{token}"

        message = (
            f"✅ Your report webhook link (`{webhook_name}`) is ready:\n"
            f"{link}\n\n"
            "Send a JSON POST like:\n"
            "```\n"
            f"POST {link}\n"
            "Content-Type: application/json\n"
            '{ "content": "Hello from another app" }\n'
            "```\n"
            "Or send files via multipart form:\n"
            "```bash\n"
            f'curl -F "file=@report.pdf" -F "content=Optional message" {link}\n'
            "```\n"
            f"• Use `/settings webhook_link name:{webhook_name} reset:true` to rotate this link.\n"
            "• Use `/settings webhook_list` to view all active webhooks."
        )
        await self._send_ctx(ctx, message, ephemeral=True)

    @settings_group.command(name="webhook_list", aliases=["webhooks", "report_webhooks"])
    async def webhook_list(self, ctx: commands.Context):
        """
        List all active report webhooks configured for your account.
        """
        user_id = ctx.author.id
        webhooks = await self.bot.loop.run_in_executor(
            None, self.db_manager.get_user_webhooks, user_id
        )
        if not webhooks:
            await self._send_ctx(
                ctx,
                "ℹ️ You do not have any active webhooks yet. Use `/settings webhook_link` to create one.",
                ephemeral=True,
            )
            return

        pref_base_url = await self.bot.loop.run_in_executor(
            None, self.db_manager.get_user_preference, user_id, "report_webhook_base_url", None
        )
        base_url = str(pref_base_url or getattr(config, "WEBHOOK_BASE_URL", "http://localhost:5000") or "http://localhost:5000").rstrip("/")

        lines = [f"📋 **Your Active Webhooks ({len(webhooks)})**:"]
        for w_name, token in sorted(webhooks.items()):
            link = f"{base_url}/webhook/report/{token}"
            lines.append(f"• **{w_name}**: `{link}`")

        lines.append("\n*Manage with `/settings webhook_link [name]` or `/settings webhook_delete <name>`.*")
        await self._send_ctx(ctx, "\n".join(lines), ephemeral=True)

    @settings_group.command(name="webhook_delete", aliases=["webhook_remove", "report_webhook_delete"])
    @app_commands.describe(name="Name of the webhook to delete.")
    async def webhook_delete(self, ctx: commands.Context, name: str):
        """
        Delete a specific report webhook.
        """
        user_id = ctx.author.id
        webhook_name = (name or "").strip().lower()
        if not webhook_name:
            await self._send_ctx(ctx, "❌ Please specify the webhook name to delete.", ephemeral=True)
            return

        deleted = await self.bot.loop.run_in_executor(
            None, self.db_manager.delete_user_webhook, user_id, webhook_name
        )
        if deleted:
            await self._send_ctx(ctx, f"✅ Webhook `{webhook_name}` has been deleted.", ephemeral=True)
        else:
            await self._send_ctx(
                ctx,
                f"❌ Webhook `{webhook_name}` was not found. Use `/settings webhook_list` to see existing webhooks.",
                ephemeral=True,
            )

    @settings_group.command(name="webhook_base", aliases=["webhook_base_url", "report_webhook_base"])
    @app_commands.describe(base_url="Base URL for your webhook link (e.g. https://mybot.example.com)")
    async def webhook_base(self, ctx: commands.Context, *, base_url: str):
        """
        Set or clear a custom base URL for your report webhook link.
        """
        raw = (base_url or "").strip()
        if not raw:
            await self._send_ctx(ctx, "❌ Please provide a base URL (or `clear`).", ephemeral=True)
            return

        if raw.lower() in {"clear", "reset", "default"}:
            await self.bot.loop.run_in_executor(
                None, self.db_manager.delete_user_preference, ctx.author.id, "report_webhook_base_url"
            )
            await self._send_ctx(ctx, "✅ Report webhook base URL cleared (using server default).", ephemeral=True)
            return

        # If user pastes full webhook link, strip to base.
        marker = "/webhook/report/"
        if marker in raw:
            raw = raw.split(marker, 1)[0]

        if not re.match(r"^https?://", raw, flags=re.IGNORECASE):
            raw = f"https://{raw}"

        try:
            from urllib.parse import urlparse

            parsed = urlparse(raw)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError("invalid_url")
        except Exception:
            await self._send_ctx(
                ctx,
                "❌ Invalid URL. Example: `https://mybot.example.com`",
                ephemeral=True,
            )
            return

        base = raw.rstrip("/")
        await self.bot.loop.run_in_executor(
            None, self.db_manager.set_user_preference, ctx.author.id, "report_webhook_base_url", base
        )
        await self._send_ctx(ctx, f"✅ Report webhook base URL set to: `{base}`", ephemeral=True)

    @settings_group.command(name="webhook_reset", aliases=["webhook_rotate", "report_webhook_reset"])
    @app_commands.describe(name="Name of the webhook to reset (defaults to 'default').")
    async def webhook_reset(self, ctx: commands.Context, name: str = "default"):
        """
        Generate a new per-user webhook link (invalidates the old one).
        """
        await self.webhook_link(ctx, name=name, reset=True)

    @settings_group.command(name="backup_download", aliases=["backup_now", "db_backup"])
    @app_commands.describe()
    async def backup_download(self, ctx: commands.Context):
        """Send a fresh database backup to your DMs (Owner only)."""
        owner_id = int(getattr(config, "TIMER_OWNER_ID", 0) or 0)
        if ctx.author.id != owner_id:
            await self._send_ctx(ctx, "❌ This command is restricted to the bot owner.", ephemeral=True)
            return

        if getattr(ctx, "interaction", None):
            await ctx.defer(ephemeral=True)

        gz_data = await self.bot.loop.run_in_executor(None, _generate_backup_archive, self.db_manager)
        if not gz_data:
            await self._send_ctx(ctx, "❌ Failed to generate database backup.", ephemeral=True)
            return

        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        file = discord.File(fp=io.BytesIO(gz_data), filename=f"app_backup_{timestamp}.sqlite.gz")
        try:
            await ctx.author.send(
                content=f"📦 **Database Backup — {timestamp}**\nHere is your requested database backup ({len(gz_data) / 1024:.1f} KB).",
                file=file,
            )
            await self._send_ctx(ctx, "✅ Backup has been sent to your DMs!", ephemeral=True)
        except discord.Forbidden:
            await self._send_ctx(ctx, "❌ Could not send you a DM. Please check your Discord privacy settings.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error delivering on-demand backup to owner: {e}", exc_info=True)
            await self._send_ctx(ctx, f"❌ Failed to send backup: {e}", ephemeral=True)

    @tasks.loop(hours=1)
    async def monthly_backup_task(self):
        """Sends a backup to the owner on the 1st of each month (once a month)."""
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.day != 1 or now.hour < 4:
            return

        owner_id = int(getattr(config, "TIMER_OWNER_ID", 0) or 0)
        if not owner_id:
            return

        month_key = now.strftime("%Y-%m")
        last_sent = await self.bot.loop.run_in_executor(
            None, self.db_manager.get_user_preference, owner_id, "last_monthly_backup_sent", None
        )
        if last_sent == month_key:
            return

        await self.bot.loop.run_in_executor(
            None, self.db_manager.set_user_preference, owner_id, "last_monthly_backup_sent", month_key
        )

        try:
            user = self.bot.get_user(owner_id) or await self.bot.fetch_user(owner_id)
            if not user:
                logger.warning(f"Monthly backup: Owner user {owner_id} not found.")
                return

            gz_data = await self.bot.loop.run_in_executor(None, _generate_backup_archive, self.db_manager)
            if not gz_data:
                logger.error("Monthly backup: Failed to generate backup archive.")
                return

            file = discord.File(fp=io.BytesIO(gz_data), filename=f"app_backup_{month_key}-01.sqlite.gz")
            await user.send(
                content=f"📦 **Monthly Database Backup — {month_key}-01**\nHere is your automated monthly database backup archive ({len(gz_data) / 1024:.1f} KB).",
                file=file,
            )
            logger.info(f"Monthly database backup sent to owner {owner_id} for {month_key}.")
        except Exception as e:
            logger.error(f"Failed to deliver monthly backup to owner: {e}", exc_info=True)

    @monthly_backup_task.before_loop
    async def before_monthly_backup_task(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingsCog(bot, db_manager=bot.db_manager))
    logger.info("SettingsCog has been loaded.")