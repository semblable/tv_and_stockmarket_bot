# cogs/tv_shows.py

import discord
from discord.ext import commands, tasks
from api_clients import tmdb_client
import data_manager
from datetime import datetime, date, timedelta
import asyncio # Added for potential sleep/retry logic, though not explicitly in plan yet

class TVShows(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_new_episodes.start() # Start the background task

    def cog_unload(self):
        self.check_new_episodes.cancel() # Stop the background task when cog is unloaded

    @commands.Cog.listener()
    async def on_ready(self):
        print("TVShows Cog is ready.")

    @commands.hybrid_command(name="tv_subscribe", description="Subscribe to TV show notifications.")
    @discord.app_commands.describe(show_name="The name of the TV show to subscribe to")
    async def tv_subscribe(self, ctx: commands.Context, *, show_name: str):
        """Allows a user to subscribe to notifications for a specific TV show."""
        try:
            search_results = await tmdb_client.search_tv_show(show_name)
        except Exception as e:
            print(f"Error searching for TV show '{show_name}': {e}")
            await ctx.send(f"Sorry, there was an error searching for '{show_name}'. Please try again later.", ephemeral=True)
            return

        if not search_results:
            await ctx.send(f"No shows found for '{show_name}'.", ephemeral=True)
            return

        if len(search_results) > 1:
            await ctx.send(f"Multiple shows found for '{show_name}'. Please be more specific.", ephemeral=True)
            # For now, we'll just ask them to be more specific.
            # Later, we could implement a selection mechanism here.
            # Example:
            # options = []
            # for i, show in enumerate(search_results[:5]): # Show top 5
            #     options.append(f"{i+1}. {show['name']} ({show.get('first_air_date', 'N/A')[:4]})")
            # await ctx.send("Multiple shows found. Please choose one by replying with the number:\n" + "\n".join(options), ephemeral=True)
            # try:
            #     def check(m):
            #         return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()
            #     msg = await self.bot.wait_for('message', check=check, timeout=30.0)
            #     choice = int(msg.content) -1
            #     if 0 <= choice < len(search_results[:5]):
            #         selected_show = search_results[choice]
            #     else:
            #         await ctx.send("Invalid choice.", ephemeral=True)
            #         return
            # except asyncio.TimeoutError:
            #     await ctx.send("Selection timed out.", ephemeral=True)
            #     return
            return

        # Exactly one result
        selected_show = search_results[0]
        show_id = selected_show['id']
        actual_show_name = selected_show['name'] # Use the name from TMDB for consistency

        try:
            success = data_manager.add_tv_subscription(ctx.author.id, show_id, actual_show_name)
            if success:
                await ctx.send(f"Successfully subscribed to {actual_show_name}!", ephemeral=True)
            else:
                await ctx.send(f"You are already subscribed to {actual_show_name}.", ephemeral=True)
        except Exception as e:
            print(f"Error adding TV subscription for user {ctx.author.id} to show {show_id} ('{actual_show_name}'): {e}")
            await ctx.send(f"Sorry, there was an error subscribing to '{actual_show_name}'. Please try again later.", ephemeral=True)

    @commands.hybrid_command(name="tv_unsubscribe", description="Unsubscribe from TV show notifications.")
    @discord.app_commands.describe(show_name="The name of the TV show to unsubscribe from")
    async def tv_unsubscribe(self, ctx: commands.Context, *, show_name: str):
        """Allows a user to unsubscribe from notifications for a specific TV show."""
        user_id = ctx.author.id
        try:
            subscriptions = data_manager.get_user_tv_subscriptions(user_id)
        except Exception as e:
            print(f"Error getting subscriptions for user {user_id}: {e}")
            await ctx.send("Sorry, there was an error fetching your subscriptions. Please try again later.", ephemeral=True)
            return

        if not subscriptions:
            await ctx.send("You are not subscribed to any TV shows.", ephemeral=True)
            return

        show_to_unsubscribe = None
        # Try to find an exact (case-insensitive) match first
        for sub in subscriptions:
            if sub['show_name'].lower() == show_name.lower():
                show_to_unsubscribe = sub
                break
        
        # If no exact match, try a partial (case-insensitive) match
        if not show_to_unsubscribe:
            found_partial_matches = []
            for sub in subscriptions:
                if show_name.lower() in sub['show_name'].lower():
                    found_partial_matches.append(sub)
            
            if len(found_partial_matches) == 1:
                show_to_unsubscribe = found_partial_matches[0]
            elif len(found_partial_matches) > 1:
                await ctx.send(f"Multiple subscribed shows match '{show_name}'. Please provide the exact name to unsubscribe. Matches: {', '.join([s['show_name'] for s in found_partial_matches])}", ephemeral=True)
                return
            
        if not show_to_unsubscribe:
            await ctx.send(f"'{show_name}' not found in your subscriptions. Use `/my_tv_shows` to see your current subscriptions.", ephemeral=True)
            return

        show_id_to_remove = show_to_unsubscribe['show_id']
        actual_show_name = show_to_unsubscribe['show_name'] # Use the stored name for confirmation

        try:
            success = data_manager.remove_tv_subscription(user_id, show_id_to_remove)
            if success:
                await ctx.send(f"Successfully unsubscribed from {actual_show_name}.", ephemeral=True)
            else:
                # This case should ideally not happen if we found it in subscriptions,
                # but data_manager might have its own logic or an issue.
                await ctx.send(f"Could not unsubscribe from {actual_show_name}. It might have already been removed or an error occurred.", ephemeral=True)
        except Exception as e:
            print(f"Error removing TV subscription for user {user_id} from show {show_id_to_remove} ('{actual_show_name}'): {e}")
            await ctx.send(f"Sorry, there was an error unsubscribing from '{actual_show_name}'. Please try again later.", ephemeral=True)

    @commands.hybrid_command(name="my_tv_shows", description="Lists your subscribed TV shows.")
    async def my_tv_shows(self, ctx: commands.Context):
        """Lists all TV shows the invoking user is currently subscribed to."""
        user_id = ctx.author.id
        try:
            subscriptions = data_manager.get_user_tv_subscriptions(user_id)
        except Exception as e:
            print(f"Error getting subscriptions for user {user_id}: {e}")
            await ctx.send("Sorry, there was an error fetching your subscriptions. Please try again later.", ephemeral=True)
            return

        if not subscriptions:
            await ctx.send("You are not subscribed to any TV shows.", ephemeral=True)
            return

        embed = discord.Embed(title="Your Subscribed TV Shows", color=discord.Color.blue())
        
        show_list_text = []
        for sub in subscriptions:
            # Assuming subscription dict has 'show_name' and 'show_id'
            show_list_text.append(f"- {sub['show_name']} (ID: {sub['show_id']})")
            # We can add more details later if available, e.g., last notified episode

        if not show_list_text: # Should not happen if subscriptions is not empty, but as a safeguard
             await ctx.send("You are not subscribed to any TV shows.", ephemeral=True)
             return

        embed.description = "\n".join(show_list_text)
        
        try:
            await ctx.send(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            # Fallback if embed is too long or other HTTP issue
            print(f"Error sending embed for my_tv_shows for user {user_id}: {e}")
            if len("\n".join(show_list_text)) > 2000: # Discord message character limit
                 await ctx.send("You have too many subscriptions to display in a single message. Please try unsubscribing from some shows.", ephemeral=True)
            else:
                 await ctx.send("Your subscribed TV shows:\n" + "\n".join(show_list_text), ephemeral=True)
        except Exception as e:
            print(f"Unexpected error sending my_tv_shows for user {user_id}: {e}")
            await ctx.send("Sorry, an unexpected error occurred while displaying your shows.", ephemeral=True)

    @tasks.loop(minutes=30) # Check every 30 minutes for testing, change to hours=6 or hours=12 for production
    async def check_new_episodes(self):
        """Background task to check for new episodes of subscribed shows."""
        print(f"[{datetime.now()}] Starting check_new_episodes task...")

        try:
            all_subscriptions = data_manager.get_all_tv_subscriptions()
            if not all_subscriptions:
                print(f"[{datetime.now()}] No TV subscriptions found. Skipping check.")
                return
        except Exception as e:
            print(f"[{datetime.now()}] Error fetching all TV subscriptions: {e}")
            return

        unique_show_ids = set()
        for user_id_str, user_subs in all_subscriptions.items():
            for sub in user_subs:
                unique_show_ids.add(sub['show_id'])

        if not unique_show_ids:
            print(f"[{datetime.now()}] No unique show IDs to check. Skipping.")
            return

        print(f"[{datetime.now()}] Checking {len(unique_show_ids)} unique show(s): {unique_show_ids}")

        today = date.today()
        tomorrow = today + timedelta(days=1)

        for show_id in unique_show_ids:
            try:
                # Ensure append_to_response includes next_episode_to_air and last_episode_to_air
                # This should be handled in tmdb_client.get_show_details implementation
                show_details = await tmdb_client.get_show_details(show_id, append_to_response="next_episode_to_air,last_episode_to_air")
                if not show_details:
                    print(f"[{datetime.now()}] Could not fetch details for show_id: {show_id}")
                    continue
                
                show_name = show_details.get('name', f"Show ID {show_id}")
                episode_to_notify = None
                episode_data_for_notification = {}

                # Priority 1: Next episode to air
                next_episode = show_details.get('next_episode_to_air')
                if next_episode:
                    air_date_str = next_episode.get('air_date')
                    if air_date_str:
                        try:
                            episode_air_date = datetime.strptime(air_date_str, '%Y-%m-%d').date()
                            # Notify if airing today or tomorrow
                            if episode_air_date == today or episode_air_date == tomorrow:
                                episode_to_notify = next_episode
                                print(f"[{datetime.now()}] Found potential next_episode_to_air for '{show_name}' (ID: {show_id}): Ep {next_episode.get('episode_number')} on {air_date_str}")
                        except ValueError:
                            print(f"[{datetime.now()}] Invalid air_date format for next_episode_to_air for show {show_id}: {air_date_str}")
                
                # Priority 2: Last episode aired (if next_episode_to_air wasn't suitable or doesn't exist)
                # This part can be expanded if needed, for now focusing on next_episode_to_air
                # if not episode_to_notify:
                #     last_episode = show_details.get('last_episode_to_air')
                #     if last_episode:
                #         air_date_str = last_episode.get('air_date')
                #         if air_date_str:
                #             try:
                #                 episode_air_date = datetime.strptime(air_date_str, '%Y-%m-%d').date()
                #                 # Notify if aired yesterday or today (and not yet notified)
                #                 if episode_air_date == today or episode_air_date == (today - timedelta(days=1)):
                #                     # We'd need to ensure this isn't an old episode the user was already notified for
                #                     # This logic becomes more complex if we don't just rely on next_episode_to_air
                #                     # For now, we'll keep it simple and focus on next_episode_to_air
                #                     pass # Placeholder for more complex last_episode logic
                #             except ValueError:
                #                 print(f"[{datetime.now()}] Invalid air_date format for last_episode_to_air for show {show_id}: {air_date_str}")


                if episode_to_notify:
                    episode_id = episode_to_notify.get('id')
                    if not episode_id:
                        print(f"[{datetime.now()}] Episode for '{show_name}' has no ID. Skipping.")
                        continue

                    episode_data_for_notification = {
                        'id': episode_id,
                        'name': episode_to_notify.get('name', 'TBA'),
                        'episode_number': episode_to_notify.get('episode_number', 'N/A'),
                        'season_number': episode_to_notify.get('season_number', 'N/A'),
                        'air_date': episode_to_notify.get('air_date', 'Unknown date'),
                        'overview': episode_to_notify.get('overview', 'No overview available.')
                    }

                    for user_id_str, user_subs in all_subscriptions.items():
                        for sub in user_subs:
                            if sub['show_id'] == show_id:
                                if sub.get('last_notified_episode_id') != episode_id:
                                    try:
                                        user = await self.bot.fetch_user(int(user_id_str))
                                        if user:
                                            message = (
                                                f"📢 New Episode Alert for **{show_name}**!\n"
                                                f"📺 Season {episode_data_for_notification['season_number']} Episode {episode_data_for_notification['episode_number']}: **{episode_data_for_notification['name']}**\n"
                                                f"🗓️ Airing on {episode_data_for_notification['air_date']}."
                                                # f"\n🗒️ Overview: {episode_data_for_notification['overview']}" # Optional
                                            )
                                            await user.send(message)
                                            print(f"[{datetime.now()}] Sent notification to user {user_id_str} for {show_name} - Episode ID {episode_id}")
                                            data_manager.update_last_notified_episode(user_id_str, show_id, episode_id)
                                    except discord.Forbidden:
                                        print(f"[{datetime.now()}] Cannot send DM to user {user_id_str} for show {show_id} (DMs disabled or bot blocked).")
                                    except discord.NotFound:
                                        print(f"[{datetime.now()}] User {user_id_str} not found, cannot send DM for show {show_id}.")
                                    except Exception as e:
                                        print(f"[{datetime.now()}] Error sending DM to user {user_id_str} for show {show_id}: {e}")
                                else:
                                    print(f"[{datetime.now()}] User {user_id_str} already notified for episode {episode_id} of show '{show_name}'.")
                                break # Move to next user, as this user's subscription for this show is handled
            except tmdb_client.TMDBException as e: # Assuming tmdb_client raises a custom exception
                 print(f"[{datetime.now()}] TMDB API error for show_id {show_id}: {e}")
            except Exception as e:
                print(f"[{datetime.now()}] Unexpected error processing show_id {show_id}: {e}")
            
            await asyncio.sleep(1) # Small delay to be kind to the TMDB API

        print(f"[{datetime.now()}] Finished check_new_episodes task.")

    @check_new_episodes.before_loop
    async def before_check_new_episodes(self):
        await self.bot.wait_until_ready() # Wait until the bot is fully ready before starting the loop
        print(f"[{datetime.now()}] TVShows Cog: Background task for checking new episodes is starting after bot is ready.")


async def setup(bot):
    await bot.add_cog(TVShows(bot))
    print("TVShows Cog has been loaded.")