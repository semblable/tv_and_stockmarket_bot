# cogs/settings.py
import discord
from discord.ext import commands
from discord import app_commands
import re # For DND time validation
from datetime import datetime, time # For DND time checking (though not used in this file directly yet)
import logging # Import logging
from typing import Optional, List
import secrets

from data_manager import DataManager # Import DataManager class#
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

    embed.set_footer(text=f"Use '{ctx.prefix}settings <command> <value>' to change a setting.")
    return embed

class SettingsCog(commands.Cog, name="Settings"):
    def __init__(self, bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager # Injected dependency

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
        # await self.view_settings(ctx)

    @settings_group.command(name="timezone", aliases=["tz"])
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
    @app_commands.describe(reset="Generate a new link (invalidates the old one).")
    async def webhook_link(self, ctx: commands.Context, reset: bool = False):
        """
        Generate a per-user webhook link for receiving reports.
        """
        user_id = ctx.author.id
        token = await self.bot.loop.run_in_executor(
            None, self.db_manager.get_user_preference, user_id, "report_webhook_token", None
        )
        if reset or not token:
            token = secrets.token_urlsafe(32)
            await self.bot.loop.run_in_executor(
                None, self.db_manager.set_user_preference, user_id, "report_webhook_token", token
            )

        base_url = str(getattr(config, "WEBHOOK_BASE_URL", "http://localhost:5000") or "http://localhost:5000")
        base_url = base_url.rstrip("/")
        link = f"{base_url}/webhook/report/{token}"

        message = (
            "✅ Your report webhook link is ready:\n"
            f"{link}\n\n"
            "Send a JSON POST like:\n"
            "```\n"
            f"POST {link}\n"
            "Content-Type: application/json\n"
            "{ \"content\": \"Hello from another app\" }\n"
            "```\n"
            "Use `/settings webhook_link reset:true` to rotate the link."
        )
        await self._send_ctx(ctx, message, ephemeral=True)

    @settings_group.command(name="webhook_reset", aliases=["webhook_rotate", "report_webhook_reset"])
    async def webhook_reset(self, ctx: commands.Context):
        """
        Generate a new per-user webhook link (invalidates the old one).
        """
        await self.webhook_link(ctx, reset=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingsCog(bot, db_manager=bot.db_manager))
    logger.info("SettingsCog has been loaded.")