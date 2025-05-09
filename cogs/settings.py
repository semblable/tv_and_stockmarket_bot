# cogs/settings.py
import discord
from discord.ext import commands
import re # For DND time validation
from datetime import datetime, time # For DND time checking (though not used in this file directly yet)
import logging # Import logging

import data_manager

logger = logging.getLogger(__name__)

# Helper to create a consistent embed for settings
def create_settings_embed(ctx, user_preferences):
    """Creates a Discord embed to display user settings."""
    embed = discord.Embed(
        title=f"⚙️ Notification Settings for {ctx.author.display_name}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)

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

    embed.set_footer(text=f"Use '{ctx.prefix}settings <command> <value>' to change a setting.")
    return embed

class SettingsCog(commands.Cog, name="Settings"):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="settings", aliases=["prefs"], invoke_without_command=True)
    async def settings_group(self, ctx: commands.Context):
        """Manage your notification preferences."""
        if ctx.invoked_subcommand is None:
            await self.view_settings(ctx)

    @settings_group.command(name="view", aliases=["show"])
    async def view_settings(self, ctx: commands.Context):
        """Displays your current notification settings."""
        user_id = ctx.author.id
        preferences = {
            "tv_show_dm_overview": data_manager.get_user_preference(user_id, "tv_show_dm_overview", default=True),
            "dnd_enabled": data_manager.get_user_preference(user_id, "dnd_enabled", default=False),
            "dnd_start_time": data_manager.get_user_preference(user_id, "dnd_start_time", default="22:00"),
            "dnd_end_time": data_manager.get_user_preference(user_id, "dnd_end_time", default="07:00"),
        }
        embed = create_settings_embed(ctx, preferences)
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
        data_manager.set_user_preference(user_id, "tv_show_dm_overview", preference_value)
        
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
            data_manager.set_user_preference(user_id, "dnd_enabled", True)
            # Ensure times exist if enabling DND globally
            _ = data_manager.get_user_preference(user_id, "dnd_start_time", "22:00")
            _ = data_manager.get_user_preference(user_id, "dnd_end_time", "07:00")
            await ctx.send("🌙 Do Not Disturb (DND) is now **Active**.")
        elif setting_lower == "off":
            data_manager.set_user_preference(user_id, "dnd_enabled", False)
            await ctx.send("☀️ Do Not Disturb (DND) is now **Inactive**.")
        elif time_match:
            start_time_str = f"{time_match.group(1)}:{time_match.group(2)}"
            end_time_str = f"{time_match.group(3)}:{time_match.group(4)}"
            
            # Basic validation: Ensure start and end times are valid HH:MM format (already done by regex)
            # More complex validation (e.g. end time after start time, crossing midnight) can be added
            # For now, we store them as strings.
            
            data_manager.set_user_preference(user_id, "dnd_start_time", start_time_str)
            data_manager.set_user_preference(user_id, "dnd_end_time", end_time_str)
            data_manager.set_user_preference(user_id, "dnd_enabled", True) # Enable DND when times are set
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

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingsCog(bot))
    logger.info("SettingsCog has been loaded.")