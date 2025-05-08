# cogs/tv_shows.py

import discord
from discord.ext import commands, tasks
# from api_clients import tmdb_client # Will be used later
# import data_manager # Will be used later

class TVShows(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # self.check_new_episodes.start() # Start the background task

    def cog_unload(self):
        # self.check_new_episodes.cancel() # Stop the background task when cog is unloaded
        pass

    @commands.Cog.listener()
    async def on_ready(self):
        print("TVShows Cog is ready.")

    @commands.hybrid_command(name="tv_subscribe", description="Subscribe to TV show notifications.")
    @discord.app_commands.describe(show_name="The name of the TV show to subscribe to")
    async def tv_subscribe(self, ctx: commands.Context, *, show_name: str):
        """Allows a user to subscribe to notifications for a specific TV show."""
        await ctx.send(f"Subscription command for '{show_name}' received. (Not implemented yet)", ephemeral=True)
        # Placeholder:
        # 1. Search for show using tmdb_client
        # 2. If multiple, ask user to clarify
        # 3. Save subscription using data_manager

    @commands.hybrid_command(name="tv_unsubscribe", description="Unsubscribe from TV show notifications.")
    @discord.app_commands.describe(show_name="The name of the TV show to unsubscribe from")
    async def tv_unsubscribe(self, ctx: commands.Context, *, show_name: str):
        """Allows a user to unsubscribe from notifications for a specific TV show."""
        await ctx.send(f"Unsubscription command for '{show_name}' received. (Not implemented yet)", ephemeral=True)
        # Placeholder:
        # 1. Find show in user's subscriptions (data_manager)
        # 2. Remove subscription

    @commands.hybrid_command(name="my_tv_shows", description="Lists your subscribed TV shows.")
    async def my_tv_shows(self, ctx: commands.Context):
        """Lists all TV shows the invoking user is currently subscribed to."""
        await ctx.send("Listing your subscribed TV shows. (Not implemented yet)", ephemeral=True)
        # Placeholder:
        # 1. Get user's subscriptions from data_manager
        # 2. Display them

    # @tasks.loop(hours=6) # Example: check every 6 hours
    # async def check_new_episodes(self):
    #     """Background task to check for new episodes of subscribed shows."""
    #     # This task will run after the bot is ready
    #     print("Checking for new TV show episodes... (Not implemented yet)")
        # Placeholder:
        # 1. Get all unique subscribed shows (data_manager)
        # 2. For each show, get latest episode info (tmdb_client)
        # 3. Compare with last_notified_episode_id
        # 4. If new, notify relevant users and update last_notified_episode_id (data_manager)

    # @check_new_episodes.before_loop
    # async def before_check_new_episodes(self):
    #     await self.bot.wait_until_ready() # Wait until the bot is fully ready before starting the loop
    #     print("TVShows Cog: Background task for checking new episodes is starting.")


async def setup(bot):
    await bot.add_cog(TVShows(bot))
    print("TVShows Cog has been loaded.")