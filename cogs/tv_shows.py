# cogs/tv_shows.py

import discord
from discord.ext import commands, tasks
from api_clients import tmdb_client
from data_manager import DataManager # Import DataManager class
from datetime import datetime, date, timedelta, time
import requests
import asyncio
import logging # Import logging
import json # Added for parsing JSON strings from DB

logger = logging.getLogger(__name__)

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"] # Unicode: \u0031\uFE0F\u20E3, etc.

ITEMS_PER_PAGE_DEFAULT = 5

class MyTVShowsPaginatorView(discord.ui.View):
    message: discord.Message | None = None

    def __init__(self, *, timeout=300, user_id: int, all_subs: list, bot_instance, items_per_page: int = ITEMS_PER_PAGE_DEFAULT):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.all_subs = all_subs
        self.items_per_page = items_per_page if items_per_page > 0 else ITEMS_PER_PAGE_DEFAULT
        self.bot = bot_instance

        self.current_page = 0
        if not self.all_subs:
            self.total_pages = 0
        else:
            # Ceiling division for total pages
            self.total_pages = (len(self.all_subs) + self.items_per_page - 1) // self.items_per_page
        
        # Initial button states are set by _update_button_states() before the first send.

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't for you!", ephemeral=True)
            return False
        return True

    def _update_button_states(self):
        # This method is called before creating/editing the embed to set button states.
        is_first_page = self.current_page == 0
        # Ensure buttons exist (they are created by decorators)
        if hasattr(self, 'first_page_button'): self.first_page_button.disabled = is_first_page
        if hasattr(self, 'prev_page_button'): self.prev_page_button.disabled = is_first_page

        is_last_page = self.current_page >= self.total_pages - 1
        if hasattr(self, 'next_page_button'): self.next_page_button.disabled = is_last_page
        if hasattr(self, 'last_page_button'): self.last_page_button.disabled = is_last_page
        
        # If only one page, disable all navigation buttons
        if self.total_pages <= 1:
            if hasattr(self, 'first_page_button'): self.first_page_button.disabled = True
            if hasattr(self, 'prev_page_button'): self.prev_page_button.disabled = True
            if hasattr(self, 'next_page_button'): self.next_page_button.disabled = True
            if hasattr(self, 'last_page_button'): self.last_page_button.disabled = True

    async def _get_embed_for_current_page(self) -> discord.Embed:
        self._update_button_states()

        start_index = self.current_page * self.items_per_page
        end_index = start_index + self.items_per_page
        page_subs = self.all_subs[start_index:end_index]

        embed_title = "📺 Your TV Show Subscriptions"
        if self.total_pages > 1:
            embed_title += f" (Page {self.current_page + 1}/{self.total_pages})"
        
        embed = discord.Embed(title=embed_title, color=discord.Color.purple())
        
        footer_parts = []
        if self.all_subs:
            footer_parts.append(f"Showing {len(page_subs)} of {len(self.all_subs)} total.")
        footer_parts.append("Data from TMDB.")
        embed.set_footer(text=" ".join(footer_parts))

        if not self.all_subs:
            embed.description = "You have no TV show subscriptions."
            return embed
        if not page_subs and self.total_pages > 0 :
            embed.description = "No subscriptions to display on this page."
            return embed

        shows_with_errors = 0
        for sub in page_subs:
            show_id = sub['show_tmdb_id']
            show_name = sub['show_name']
            
            next_episode_str = "🗓️ Next: Loading..."
            last_notified_str = "🔔 Notified: Never"

            try:
                show_details_tmdb = await self.bot.loop.run_in_executor(None, tmdb_client.get_show_details, show_id)
                if show_details_tmdb and show_details_tmdb.get('next_episode_to_air'):
                    next_ep = show_details_tmdb['next_episode_to_air']
                    ep_name = next_ep.get('name', 'TBA')
                    ep_season = next_ep.get('season_number', 'S?')
                    ep_num = next_ep.get('episode_number', 'E?')
                    ep_air_date_str = next_ep.get('air_date', 'Unknown date')
                    if ep_air_date_str and ep_air_date_str != 'Unknown date':
                        try:
                            date_obj = datetime.strptime(ep_air_date_str, '%Y-%m-%d').date()
                            ep_air_date_str = date_obj.strftime('%b %d, %Y')
                        except ValueError: pass
                    next_episode_str = f"🗓️ Next: S{ep_season:02d}E{ep_num:02d} - {ep_name} ({ep_air_date_str})"
                else:
                    next_episode_str = "🗓️ Next: No upcoming episode data"
            except Exception as e:
                logger.error(f"PaginatorView: TMDB API error for show {show_id} ('{show_name}'): {e}")
                next_episode_str = "🗓️ Next: ⚠️ Error loading data"
                shows_with_errors += 1

            last_notified_info = sub.get('last_notified_episode_details')
            if isinstance(last_notified_info, str): # Potentially JSON string from DB
                try:
                    last_notified_info = json.loads(last_notified_info)
                except json.JSONDecodeError:
                    logger.warning(f"PaginatorView: Could not parse last_notified_episode_details JSON for show {show_id}: {last_notified_info}")
                    last_notified_info = None
            
            if isinstance(last_notified_info, dict):
                ln_name = last_notified_info.get('name', 'TBA')
                ln_season = last_notified_info.get('season_number', 'S?')
                ln_episode = last_notified_info.get('episode_number', 'E?')
                last_notified_str = f"🔔 Notified: S{ln_season:02d}E{ln_episode:02d} - {ln_name}"
            
            field_value = f"{next_episode_str}\n{last_notified_str}"
            tmdb_link = f"https://www.themoviedb.org/tv/{show_id}"
            field_value_with_link = f"{field_value}\n[View on TMDB]({tmdb_link})"
            embed.add_field(name=f"📺 {show_name}", value=field_value_with_link, inline=False)
        
        current_description = embed.description if embed.description else ""
        if shows_with_errors > 0:
            error_msg = f"⚠️ Encountered errors loading data for {shows_with_errors} show(s) on this page."
            current_description = f"{current_description}\n{error_msg}".strip()
        
        if not embed.fields and page_subs: # If all shows on page had errors or failed to create fields
            current_description = (current_description or "") + "\nCould not display subscription details for this page."
        
        if current_description: # Only set description if it has content
            embed.description = current_description
            
        return embed

    async def start(self, ctx: commands.Context, ephemeral: bool = True):
        self._update_button_states()
        initial_embed = await self._get_embed_for_current_page()

        if ctx.interaction:
            self.message = await ctx.followup.send(embed=initial_embed, view=self, ephemeral=ephemeral)
        else:
            self.message = await ctx.send(embed=initial_embed, view=self)

    async def _edit_message(self, interaction: discord.Interaction):
        """Helper to edit the message with the current page."""
        embed = await self._get_embed_for_current_page()
        await interaction.response.edit_message(embed=embed, view=self)
        self.message = interaction.message # Update message reference

    @discord.ui.button(label="⏪ First", style=discord.ButtonStyle.grey, row=1)
    async def first_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        await self._edit_message(interaction)

    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.blurple, row=1)
    async def prev_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
        await self._edit_message(interaction)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.blurple, row=1)
    async def next_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        await self._edit_message(interaction)

    @discord.ui.button(label="Last ⏩", style=discord.ButtonStyle.grey, row=1)
    async def last_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages - 1
        await self._edit_message(interaction)

    async def on_timeout(self):
        if self.message:
            try:
                # Get the last embed state if possible, or create a simple one
                timed_out_embed = self.message.embeds[0] if self.message.embeds else discord.Embed(title="📺 Your TV Show Subscriptions")
                
                # Update footer to indicate timeout
                current_footer = timed_out_embed.footer.text if timed_out_embed.footer else "Controls timed out."
                if "(Controls timed out)" not in current_footer: # Avoid appending multiple times
                    timed_out_embed.set_footer(text=f"{current_footer} (Controls timed out)")
                
                await self.message.edit(embed=timed_out_embed, view=None) # view=None removes buttons
            except discord.HTTPException as e:
                logger.warning(f"PaginatorView: Failed to edit message on timeout: {e}")
            except IndexError: # No embeds found on the message
                 logger.warning(f"PaginatorView: No embeds found on message {self.message.id} during timeout.")
                 try:
                     await self.message.edit(content="Subscription list timed out.", view=None)
                 except discord.HTTPException as e_fallback:
                     logger.warning(f"PaginatorView: Fallback edit message on timeout also failed: {e_fallback}")
        # Fallback for safety, though view=None should handle disabling.
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


class TVShows(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_manager = bot.db_manager # Get the DataManager instance from the bot
        logger.info("TVShows Cog: Initializing and starting check_new_episodes task.")
        self.check_new_episodes.start() # Start the background task

    async def send_response(self, ctx, content=None, embed=None, embeds=None, ephemeral=True, wait=False):
        """Helper method to send responses that work with both slash commands and prefix commands"""
        if ctx.interaction:
            # Use followup for slash commands
            if embeds:
                return await ctx.followup.send(content=content, embeds=embeds, ephemeral=ephemeral, wait=wait)
            elif content and embed:
                return await ctx.followup.send(content=content, embed=embed, ephemeral=ephemeral, wait=wait)
            elif embed:
                return await ctx.followup.send(embed=embed, ephemeral=ephemeral, wait=wait)
            else:
                return await ctx.followup.send(content, ephemeral=ephemeral, wait=wait)
        else:
            # Use regular send for prefix commands (ignore ephemeral)
            if embeds:
                return await ctx.send(content=content, embeds=embeds)
            elif content and embed:
                return await ctx.send(content=content, embed=embed)
            elif embed:
                return await ctx.send(embed=embed)
            else:
                return await ctx.send(content)

    def cog_unload(self):
        logger.info("TVShows Cog: Unloading and cancelling check_new_episodes task.")
        self.check_new_episodes.cancel() # Stop the background task when cog is unloaded

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("TVShows Cog is ready and listener has been triggered.")

    @commands.hybrid_command(name="tv_subscribe", description="Subscribe to TV show notifications.")
    @discord.app_commands.describe(show_name="The name of the TV show to subscribe to")
    async def tv_subscribe(self, ctx: commands.Context, *, show_name: str):
        """
Allows a user to subscribe to notifications for a specific TV show.
If multiple shows match the name, you'll be prompted to select the correct one.

Usage examples:
`!tv_subscribe The Witcher`
`/tv_subscribe show_name:Loki`
        """
        await ctx.defer(ephemeral=True)
        
        try:
            search_results = await self.bot.loop.run_in_executor(None, tmdb_client.search_tv_shows, show_name)
        except Exception as e:
            print(f"Error searching for TV show '{show_name}': {e}")
            await self.send_response(ctx, f"Sorry, there was an error searching for '{show_name}'. Please try again later.", ephemeral=True)
            return

        if not search_results:
            await self.send_response(ctx, f"No shows found for '{show_name}'.", ephemeral=True)
            return

        selected_show = None # Initialize selected_show

        if len(search_results) > 1:
            display_results = search_results[:5] # Limit to top 5 results

            embeds_list = []
            message_content = "Multiple shows found. Please react with the number of the show you want to subscribe to:"

            for i, show_data_item in enumerate(display_results):
                year_str = show_data_item.get('first_air_date')
                year = year_str[:4] if year_str and len(year_str) >= 4 else 'N/A'
                
                show_embed = discord.Embed(
                    description=f"{NUMBER_EMOJIS[i]} **{show_data_item['name']} ({year})**",
                    color=discord.Color.blue()
                )
                
                poster_path = show_data_item.get('poster_path')
                if poster_path:
                    poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                    if poster_url:
                        show_embed.set_thumbnail(url=poster_url)
                
                embeds_list.append(show_embed)

            prompt_msg_obj = await self.send_response(ctx, content=message_content, embeds=embeds_list, ephemeral=False, wait=True) # Send and wait for message object

            for i in range(len(display_results)):
                if i < len(NUMBER_EMOJIS): # Ensure we don't run out of emojis
                    await prompt_msg_obj.add_reaction(NUMBER_EMOJIS[i])
            
            def check(reaction, user):
                return user == ctx.author and \
                       reaction.message.id == prompt_msg_obj.id and \
                       str(reaction.emoji) in NUMBER_EMOJIS[:len(display_results)]

            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
                
                # Determine choice from reaction
                choice_idx = -1
                for i, emoji_str in enumerate(NUMBER_EMOJIS[:len(display_results)]):
                    if str(reaction.emoji) == emoji_str:
                        choice_idx = i
                        break
                
                if 0 <= choice_idx < len(display_results):
                    selected_show = display_results[choice_idx]
                    # Optionally, edit the original message to confirm selection or remove reactions
                    # await prompt_msg_obj.edit(content=f"You selected: {selected_show['name']}", embed=None, view=None)
                    # await prompt_msg_obj.clear_reactions() # Requires manage_messages permission
                else:
                    # This case should ideally not be reached if the check function is correct
                    await self.send_response(ctx, "Invalid reaction. Subscription cancelled.", ephemeral=True)
                    try:
                        await prompt_msg_obj.delete()
                    except discord.HTTPException:
                        pass # Ignore if already deleted or no perms
                    return
            except asyncio.TimeoutError:
                await self.send_response(ctx, "Selection timed out. Subscription cancelled.", ephemeral=True)
                try:
                    # await prompt_msg_obj.edit(content="Selection timed out.", embed=None, view=None)
                    await prompt_msg_obj.delete() # Clean up the prompt message
                except discord.HTTPException:
                    pass # Ignore if already deleted or no perms
                return
            except Exception as e:
                print(f"Error during reaction-based show selection for '{show_name}' by {ctx.author.id}: {e}")
                await self.send_response(ctx, "An error occurred during selection. Subscription cancelled.", ephemeral=True)
                try:
                    await prompt_msg_obj.delete()
                except discord.HTTPException:
                    pass
                return
            finally:
                # Attempt to remove reactions or delete the prompt message after selection/timeout/error
                # This might fail if the message was already deleted or bot lacks permissions.
                try:
                    if 'prompt_msg_obj' in locals() and prompt_msg_obj:
                        # Check if we are not in a timeout or error case where we already deleted
                        # Or just try to clear reactions if the message should persist briefly
                        # For simplicity, if a selection was made, we might leave the message or edit it.
                        # If timeout/error, we deleted it.
                        # If a valid selection was made, we might want to clear reactions.
                        if selected_show: # A choice was made
                             await prompt_msg_obj.clear_reactions() # Clear reactions on successful choice
                        # If we didn't delete it in timeout/error, and no choice was made (should not happen with current logic)
                        # else:
                        #    await prompt_msg_obj.delete()
                except discord.Forbidden:
                    print(f"Bot lacks 'Manage Messages' permission to clear reactions on message {prompt_msg_obj.id if 'prompt_msg_obj' in locals() else 'N/A'}.")
                except discord.HTTPException as e:
                    print(f"HTTPException while trying to manage reactions/message: {e}")
                except Exception as e: # Catch any other errors during cleanup
                    print(f"Generic error during reaction/message cleanup: {e}")


            if selected_show is None: # Should be caught by earlier returns if selection failed
                await self.send_response(ctx, "Failed to make a selection. Subscription cancelled.", ephemeral=True)
                return

        elif len(search_results) == 1:
            selected_show = search_results[0]
        # If search_results was empty, it's handled by the 'if not search_results:' block earlier.
        # If selected_show is still None at this point (e.g. if len(search_results) > 1 and selection failed without returning),
        # the subsequent code would fail. The returns in the selection logic are crucial.
        show_id = selected_show['id']
        actual_show_name = selected_show['name'] # Use the name from TMDB for consistency

        poster_path = selected_show.get('poster_path', "") # Get poster_path

        try:
            # Use the new method signature which includes poster_path
            success = await self.bot.loop.run_in_executor(None, self.db_manager.add_tv_show_subscription, ctx.author.id, show_id, actual_show_name, poster_path)
            if success: # This now reflects DB operation success (MERGE)
                await self.send_response(ctx, f"Successfully subscribed to {actual_show_name}!", ephemeral=True)
            else:
                # If MERGE fails, it's a DB issue. "Already subscribed" is handled by MERGE.
                await self.send_response(ctx, f"Could not subscribe to {actual_show_name} due to a database error. Please try again later.", ephemeral=True)
        except Exception as e:
            logger.exception(f"Error adding TV subscription for user {ctx.author.id} to show {show_id} ('{actual_show_name}')")
            await self.send_response(ctx, f"Sorry, there was an error subscribing to '{actual_show_name}'. Please try again later.", ephemeral=True)

    @commands.hybrid_command(name="tv_unsubscribe", description="Unsubscribe from TV show notifications.")
    @discord.app_commands.describe(show_name="The name of the TV show to unsubscribe from")
    async def tv_unsubscribe(self, ctx: commands.Context, *, show_name: str):
        """
Allows a user to unsubscribe from notifications for a specific TV show.
If multiple subscribed shows match the name, you'll be prompted.

Usage examples:
`!tv_unsubscribe The Witcher`
`/tv_unsubscribe show_name:Loki`
        """
        await ctx.defer(ephemeral=True)
        user_id = ctx.author.id

        try:
            subscriptions = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tv_subscriptions, user_id)
        except Exception as e:
            print(f"Error getting subscriptions for user {user_id}: {e}")
            await self.send_response(ctx, "Sorry, there was an error fetching your subscriptions. Please try again later.", ephemeral=True)
            return

        if not subscriptions:
            await self.send_response(ctx, "You are not subscribed to any TV shows.", ephemeral=True)
            return

        # subscriptions is a list of dicts, each having keys: {'show_tmdb_id': ..., 'show_name': ..., 'poster_path': ...}
        matching_subscriptions = [
            sub for sub in subscriptions if show_name.lower() in sub['show_name'].lower()
        ]

        if not matching_subscriptions:
            await self.send_response(ctx, f"No show matching '{show_name}' found in your subscriptions. Use `/my_tv_shows` to see them.", ephemeral=True)
            return

        selected_show_to_unsubscribe = None

        if len(matching_subscriptions) == 1:
            selected_show_to_unsubscribe = matching_subscriptions[0]
        else:
            display_results = matching_subscriptions[:5]

            embeds_list = []
            message_content = "Multiple subscribed shows match. React with the number to unsubscribe:"

            for i, sub_data_item in enumerate(display_results):
                show_embed = discord.Embed(
                    description=f"{NUMBER_EMOJIS[i]} **{sub_data_item['show_name']}**",
                    color=discord.Color.red() # Red for unsubscribe
                )
                
                poster_path = sub_data_item.get('poster_path')
                if poster_path:
                    poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                    if poster_url:
                        show_embed.set_thumbnail(url=poster_url)
                
                embeds_list.append(show_embed)

            prompt_msg_obj = await self.send_response(ctx, content=message_content, embeds=embeds_list, ephemeral=False, wait=True)

            for i in range(len(display_results)):
                if i < len(NUMBER_EMOJIS):
                    await prompt_msg_obj.add_reaction(NUMBER_EMOJIS[i])
            
            def check(reaction, user):
                return user == ctx.author and \
                       reaction.message.id == prompt_msg_obj.id and \
                       str(reaction.emoji) in NUMBER_EMOJIS[:len(display_results)]

            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
                choice_idx = -1
                for i, emoji_str in enumerate(NUMBER_EMOJIS[:len(display_results)]):
                    if str(reaction.emoji) == emoji_str:
                        choice_idx = i
                        break
                
                if 0 <= choice_idx < len(display_results):
                    selected_show_to_unsubscribe = display_results[choice_idx]
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                else:
                    await self.send_response(ctx, "Invalid selection. Unsubscription cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
            except asyncio.TimeoutError:
                await self.send_response(ctx, "Selection timed out. Unsubscription cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
            except Exception as e:
                print(f"Error during reaction-based show unsubscription selection for '{show_name}' by {ctx.author.id}: {e}")
                await self.send_response(ctx, "An error occurred during selection. Unsubscription cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return

        if not selected_show_to_unsubscribe:
            await self.send_response(ctx, "Could not identify show to unsubscribe from. Please try again.", ephemeral=True)
            return

        show_id_to_remove = selected_show_to_unsubscribe['show_tmdb_id']
        name_of_show_unsubscribed = selected_show_to_unsubscribe['show_name']

        try:
            success = await self.bot.loop.run_in_executor(None, self.db_manager.remove_tv_show_subscription, user_id, show_id_to_remove)
            if success:
                await self.send_response(ctx, f"Successfully unsubscribed from **{name_of_show_unsubscribed}**.", ephemeral=True)
            else:
                await self.send_response(ctx, f"Could not unsubscribe from **{name_of_show_unsubscribed}** due to a database error. Please try again later.", ephemeral=True)
        except Exception as e:
            print(f"Error removing TV subscription for user {user_id} from show {show_id_to_remove} ('{name_of_show_unsubscribed}'): {e}")
            await self.send_response(ctx, f"Sorry, there was an error unsubscribing from '{name_of_show_unsubscribed}'. Please try again later.", ephemeral=True)

    @commands.hybrid_command(name="my_tv_shows", description="Lists your subscribed TV shows.")
    async def my_tv_shows(self, ctx: commands.Context):
        """
Lists all TV shows you are currently subscribed to.
Includes details on the next upcoming episode and the last episode you were notified about.

Usage examples:
`!my_tv_shows`
`/my_tv_shows`
        """
        user_id = ctx.author.id
        # Ephemeral is True by default for defer, and for the view's start method.
        await ctx.defer(ephemeral=True)

        try:
            subscriptions = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tv_subscriptions, user_id)
        except Exception as e:
            logger.error(f"Error getting subscriptions for user {user_id} in my_tv_shows: {e}")
            # self.send_response can be used for simple followups if not using a view
            # For slash commands, ctx.followup.send is appropriate after defer.
            if ctx.interaction:
                await ctx.followup.send("Sorry, there was an error fetching your subscriptions. Please try again later.", ephemeral=True)
            else:
                await ctx.send("Sorry, there was an error fetching your subscriptions. Please try again later.")
            return

        if not subscriptions:
            no_subs_message = "You are not subscribed to any TV shows. Use `/tv_subscribe` to add some!"
            if ctx.interaction:
                await ctx.followup.send(no_subs_message, ephemeral=True)
            else:
                await ctx.send(no_subs_message)
            return

        # Initialize and start the paginator view
        # Pass self.bot (which is bot_instance for the view)
        view = MyTVShowsPaginatorView(user_id=user_id, all_subs=subscriptions, bot_instance=self.bot)
        try:
            await view.start(ctx, ephemeral=True) # Pass ctx to handle initial message send
        except Exception as e:
            logger.error(f"Error starting MyTVShowsPaginatorView for user {user_id}: {e}")
            fallback_msg = "Sorry, an unexpected error occurred while displaying your shows."
            if ctx.interaction:
                # Check if followup has already been used (e.g. by defer)
                try:
                    await ctx.followup.send(fallback_msg, ephemeral=True)
                except discord.InteractionResponded: # If defer was used, followup is fine. If send_message was used, this might happen.
                                                     # However, defer is always called.
                    await ctx.edit_original_response(content=fallback_msg, view=None, embed=None) # Fallback if followup fails
            else:
                await ctx.send(fallback_msg)

    @commands.hybrid_command(name="tv_info", description="Get detailed information about a TV show.")
    @discord.app_commands.describe(show_name="The name of the TV show to get information for")
    async def tv_info(self, ctx: commands.Context, *, show_name: str):
        """
Fetches and displays detailed information about a specific TV show from TMDB.
This includes overview, status, number of seasons/episodes, genres, and more.

Usage examples:
`!tv_info Arcane`
`/tv_info show_name:Stranger Things`
        """
        await ctx.defer(ephemeral=True) # Defer for potentially long-running API calls & selection

        try:
            search_results = await self.bot.loop.run_in_executor(None, tmdb_client.search_tv_shows, show_name)
        except Exception as e:
            print(f"Error searching for TV show '{show_name}' in tv_info: {e}")
            await self.send_response(ctx, f"Sorry, there was an error searching for '{show_name}'. Please try again later.", ephemeral=True)
            return

        if not search_results:
            await self.send_response(ctx, f"No shows found for '{show_name}'.", ephemeral=True)
            return

        selected_show_tmdb_search_data = None # This will store the basic show data from search results

        if len(search_results) == 1:
            selected_show_tmdb_search_data = search_results[0]
        elif len(search_results) > 1:
            display_results = search_results[:5] # Limit to top 5 results

            embeds_list = []
            message_content = "Multiple shows found. Please react with the number of the show you want info for:"

            for i, show_data_item in enumerate(display_results):
                year_str = show_data_item.get('first_air_date')
                year = year_str[:4] if year_str and len(year_str) >= 4 else 'N/A'
                
                show_embed = discord.Embed(
                    description=f"{NUMBER_EMOJIS[i]} **{show_data_item['name']} ({year})**",
                    color=discord.Color.green() # Using green for info
                )
                
                poster_path = show_data_item.get('poster_path')
                if poster_path:
                    poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                    if poster_url:
                        show_embed.set_thumbnail(url=poster_url)
                
                embeds_list.append(show_embed)

            prompt_msg_obj = await self.send_response(ctx, content=message_content, embeds=embeds_list, ephemeral=False, wait=True) # Send and wait for message object

            for i in range(len(display_results)):
                if i < len(NUMBER_EMOJIS):
                    await prompt_msg_obj.add_reaction(NUMBER_EMOJIS[i])
            
            def check(reaction, user):
                return user == ctx.author and \
                       reaction.message.id == prompt_msg_obj.id and \
                       str(reaction.emoji) in NUMBER_EMOJIS[:len(display_results)]

            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
                
                choice_idx = -1
                for i, emoji_str in enumerate(NUMBER_EMOJIS[:len(display_results)]):
                    if str(reaction.emoji) == emoji_str:
                        choice_idx = i
                        break
                
                if 0 <= choice_idx < len(display_results):
                    selected_show_tmdb_search_data = display_results[choice_idx]
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                else:
                    await self.send_response(ctx, "Invalid reaction. TV info cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
            except asyncio.TimeoutError:
                await self.send_response(ctx, "Selection timed out. TV info cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
            except Exception as e:
                print(f"Error during reaction-based show selection for TV info '{show_name}' by {ctx.author.id}: {e}")
                await self.send_response(ctx, "An error occurred during selection. TV info cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
        
        if not selected_show_tmdb_search_data or 'id' not in selected_show_tmdb_search_data:
            await self.send_response(ctx, "Could not determine the show to fetch details for. Please try again.", ephemeral=True)
            return

        show_id = selected_show_tmdb_search_data['id']

        try:
            full_show_details = await self.bot.loop.run_in_executor(None, tmdb_client.get_show_details, show_id, "credits,keywords,external_ids,content_ratings")
        except Exception as e:
            print(f"Error fetching full details for show ID {show_id} in tv_info: {e}")
            await self.send_response(ctx, f"Sorry, there was an error fetching detailed information for '{selected_show_tmdb_search_data['name']}'. Please try again later.", ephemeral=True)
            return

        if not full_show_details:
            await self.send_response(ctx, f"Could not retrieve detailed information for '{selected_show_tmdb_search_data['name']}'.", ephemeral=True)
            return

        # Construct the embed using full_show_details
        embed = discord.Embed(
            title=f"📺 {full_show_details.get('name', 'N/A')}",
            description=full_show_details.get('overview', 'No overview available.'),
            color=discord.Color.teal() # Or another suitable color
        )

        if full_show_details.get('poster_path'):
            embed.set_thumbnail(url=tmdb_client.get_poster_url(full_show_details['poster_path']))

        # Basic Info
        status = full_show_details.get('status', 'N/A')
        show_type = full_show_details.get('type', 'N/A')
        first_air = full_show_details.get('first_air_date', 'N/A')
        last_air = full_show_details.get('last_air_date', 'N/A')
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Type", value=show_type, inline=True)
        embed.add_field(name="First Aired", value=first_air, inline=True)
        embed.add_field(name="Last Aired", value=last_air, inline=True)
        
        num_seasons = full_show_details.get('number_of_seasons', 'N/A')
        num_episodes = full_show_details.get('number_of_episodes', 'N/A')
        embed.add_field(name="Seasons", value=str(num_seasons), inline=True)
        embed.add_field(name="Episodes", value=str(num_episodes), inline=True)

        # Genres
        genres = [genre['name'] for genre in full_show_details.get('genres', [])]
        if genres:
            embed.add_field(name="Genres", value=", ".join(genres), inline=False)

        # Networks
        networks = [net['name'] for net in full_show_details.get('networks', [])]
        if networks:
            embed.add_field(name="Networks", value=", ".join(networks), inline=False)

        # Rating (Vote Average)
        vote_avg = full_show_details.get('vote_average')
        vote_count = full_show_details.get('vote_count')
        if vote_avg is not None and vote_count is not None:
            embed.add_field(name="Rating (TMDB)", value=f"{vote_avg:.1f}/10 ({vote_count:,} votes)", inline=True)

        # Homepage
        if full_show_details.get('homepage'):
            embed.add_field(name="Homepage", value=f"[Link]({full_show_details['homepage']})", inline=True)
        
        # TMDB Link
        tmdb_id = full_show_details.get('id')
        if tmdb_id:
            embed.add_field(name="TMDB Page", value=f"[Link](https://www.themoviedb.org/tv/{tmdb_id})", inline=True)

        # Next Episode (if available)
        next_ep_data = full_show_details.get('next_episode_to_air')
        if next_ep_data:
            ep_name = next_ep_data.get('name', 'TBA')
            ep_season = next_ep_data.get('season_number', 'S?')
            ep_num = next_ep_data.get('episode_number', 'E?')
            ep_air_date = next_ep_data.get('air_date', 'Unknown date')
            next_ep_str = f"S{ep_season:02d}E{ep_num:02d} - {ep_name} (Airs: {ep_air_date})"
            embed.add_field(name="Next Episode", value=next_ep_str, inline=False)
        
        # Last Episode Aired (if available and different from next)
        last_ep_data = full_show_details.get('last_episode_to_air')
        if last_ep_data and (not next_ep_data or last_ep_data['id'] != next_ep_data.get('id')):
            ep_name = last_ep_data.get('name', 'TBA')
            ep_season = last_ep_data.get('season_number', 'S?')
            ep_num = last_ep_data.get('episode_number', 'E?')
            ep_air_date = last_ep_data.get('air_date', 'Unknown date')
            last_ep_str = f"S{ep_season:02d}E{ep_num:02d} - {ep_name} (Aired: {ep_air_date})"
            embed.add_field(name="Last Episode Aired", value=last_ep_str, inline=False)

        embed.set_footer(text="Data provided by TMDB.")
        
        try:
            await self.send_response(ctx, embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            print(f"Error sending tv_info embed for {show_id}: {e}")
            await self.send_response(ctx, "Failed to send the detailed information embed. It might be too large.", ephemeral=True)
        except Exception as e:
            print(f"Unexpected error sending tv_info for {show_id}: {e}")
            await self.send_response(ctx, "An unexpected error occurred while displaying TV show info.", ephemeral=True)

    @commands.hybrid_command(name="tv_schedule", description="Displays your upcoming TV show episode schedule for the next 7 days.")
    async def tv_schedule(self, ctx: commands.Context):
        """
        Displays a personalized schedule of upcoming TV episodes for the shows
        a user is subscribed to, within the next 7 days.
        """
        user_id = ctx.author.id
        logger.info(f"tv_schedule: Generating schedule for user_id: {user_id}")
        await ctx.defer(ephemeral=True)

        try:
            subscriptions = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tv_subscriptions, user_id)
            logger.info(f"tv_schedule: Fetched {len(subscriptions)} subscriptions for user {user_id}: {subscriptions}")
        except Exception as e:
            logger.error(f"Error getting subscriptions for user {user_id} in tv_schedule: {e}")
            await self.send_response(ctx, "Sorry, there was an error fetching your subscriptions. Please try again later.", ephemeral=True)
            return

        if not subscriptions:
            logger.info(f"tv_schedule: No subscriptions found for user {user_id}.")
            await self.send_response(ctx, "You are not subscribed to any TV shows. Use `/tv_subscribe` to add some!", ephemeral=True)
            return

        today = date.today()
        seven_days_later = today + timedelta(days=7) # We want today + 6 more days, so up to < 7 days from today

        upcoming_episodes_by_date = {} # Key: air_date (date object), Value: list of episode dicts

        # Inform user if they have many subscriptions
        if len(subscriptions) > 10: # Arbitrary threshold
            # Send a preliminary message if the main one will take time.
            # Since we already deferred, this message will be separate if the user has many subs.
            # If not many subs, this won't be sent, and the final result will come faster.
            await self.send_response(ctx, "You have many subscriptions! Generating your schedule might take a moment...", ephemeral=True)

        for sub_idx, sub in enumerate(subscriptions):
            show_id = sub['show_tmdb_id']
            show_name_stored = sub['show_name']
            logger.debug(f"tv_schedule: Processing subscription user {user_id}, show_id: {show_id}, name: {show_name_stored}")

            try:
                show_details_tmdb = await self.bot.loop.run_in_executor(None, tmdb_client.get_show_details, show_id)
                logger.debug(f"tv_schedule: TMDB details for show_id {show_id} (user {user_id}): {show_details_tmdb}")

                if show_details_tmdb and show_details_tmdb.get('next_episode_to_air'):
                    next_ep = show_details_tmdb['next_episode_to_air']
                    logger.debug(f"tv_schedule: Found next_episode_to_air for show_id {show_id} (user {user_id}): {next_ep.get('air_date')}")
                    air_date_str = next_ep.get('air_date')

                    if air_date_str:
                        try:
                            ep_air_date = datetime.strptime(air_date_str, '%Y-%m-%d').date()
                            if today <= ep_air_date < seven_days_later:
                                ep_name = next_ep.get('name', 'TBA')
                                ep_season = next_ep.get('season_number', 0) 
                                ep_num = next_ep.get('episode_number', 0) 
                                current_show_name = show_details_tmdb.get('name', show_name_stored)

                                episode_info = {
                                    'show_name': current_show_name,
                                    'season_number': ep_season,
                                    'episode_number': ep_num,
                                    'episode_name': ep_name,
                                    'air_date_obj': ep_air_date 
                                }
                                
                                if ep_air_date not in upcoming_episodes_by_date:
                                    upcoming_episodes_by_date[ep_air_date] = []
                                upcoming_episodes_by_date[ep_air_date].append(episode_info)
                                logger.debug(f"tv_schedule: Added episode {episode_info.get('episode_name')} for show {show_id} (user {user_id}) on {ep_air_date} to schedule.")
                        except ValueError:
                            logger.warning(f"Could not parse air_date '{air_date_str}' for show ID {show_id} (user {user_id})")
                        except Exception as e_inner:
                            logger.error(f"Error processing episode for show ID {show_id} (user {user_id}): {e_inner}")
                elif show_details_tmdb:
                    logger.debug(f"tv_schedule: No 'next_episode_to_air' data in TMDB details for show_id {show_id} (user {user_id}). Details: {show_details_tmdb}")
                else: # show_details_tmdb is None
                    logger.warning(f"tv_schedule: Received no TMDB details (None) for show_id {show_id} (user {user_id}).")
            
            except requests.exceptions.HTTPError as e_http:
                logger.error(f"HTTP error fetching TMDB details for show ID {show_id} (user {user_id}) in tv_schedule: {e_http.response.status_code if e_http.response else 'N/A'} - {e_http.response.text if e_http.response else 'N/A'}")
            except requests.exceptions.RequestException as e_req:
                logger.error(f"Request error fetching TMDB details for show ID {show_id} (user {user_id}) in tv_schedule: {e_req}")
            except Exception as e:
                logger.error(f"Generic error fetching/processing show ID {show_id} (user {user_id}) in tv_schedule: {e}")

        logger.info(f"tv_schedule: Final upcoming_episodes_by_date for user {user_id}: {upcoming_episodes_by_date}")
        if not upcoming_episodes_by_date:
            # If a preliminary message was sent, we need to edit it or send a new followup.
            # For simplicity, always send a new followup. ctx.followup can be called multiple times.
            logger.info(f"tv_schedule: No upcoming episodes found for user {user_id}. Sending corresponding message.")
            await self.send_response(ctx, "✨ No episodes for your subscribed shows are scheduled to air in the next 7 days.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🗓️ Your TV Schedule - Next 7 Days",
            color=discord.Color.teal()
        )
        embed.set_footer(text="All times are based on original air dates from TMDB.")

        sorted_dates = sorted(upcoming_episodes_by_date.keys())

        for air_date_obj in sorted_dates:
            episodes_on_this_date = sorted(upcoming_episodes_by_date[air_date_obj], key=lambda x: x['show_name']) # Sort episodes by show name
            
            date_header = ""
            if air_date_obj == today:
                date_header = f"Today, {air_date_obj.strftime('%B %d')}"
            elif air_date_obj == today + timedelta(days=1):
                date_header = f"Tomorrow, {air_date_obj.strftime('%B %d')}"
            else:
                date_header = air_date_obj.strftime('%A, %B %d') 

            episodes_text_list = []
            for ep_info in episodes_on_this_date:
                episode_display = f"**{ep_info['show_name']}** - S{ep_info['season_number']:02d}E{ep_info['episode_number']:02d} \"{ep_info['episode_name']}\""
                episodes_text_list.append(episode_display)

            field_value = "\n".join(episodes_text_list)
            if len(field_value) > 1024: # Discord field value limit
                field_value = field_value[:1021] + "..."

            embed.add_field(name=date_header, value=field_value, inline=False)

        try:
            await self.send_response(ctx, embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            print(f"Error sending schedule embed for user {user_id}: {e}")
            await self.send_response(ctx, "There was an issue displaying your schedule. The embed might be too large.", ephemeral=True)
        except Exception as e:
            print(f"Unexpected error sending schedule for user {user_id}: {e}")
            await self.send_response(ctx, "An unexpected error occurred while displaying your schedule.", ephemeral=True)

    @commands.hybrid_command(name="tv_trending", description="Shows trending TV shows from TMDB.")
    @discord.app_commands.describe(time_window="Time window for trending: 'day' or 'week'. Defaults to 'week'.")
    async def tv_trending(self, ctx: commands.Context, time_window: str = 'week'):
        """
        Displays a list of currently trending TV shows from TMDB.
        You can specify a time window of 'day' or 'week'.

        Usage examples:
        `!tv_trending` (shows weekly trending)
        `!tv_trending day`
        `/tv_trending time_window:week`
        """
        await ctx.defer(ephemeral=False) # Not ephemeral as it's a general info command

        if time_window.lower() not in ['day', 'week']:
            await self.send_response(ctx, "Invalid time window. Please use 'day' or 'week'.", ephemeral=True)
            return

        try:
            trending_shows = await self.bot.loop.run_in_executor(None, tmdb_client.get_trending_tv_shows, time_window.lower())
        except Exception as e:
            print(f"Error fetching trending TV shows (window: {time_window}): {e}")
            await self.send_response(ctx, f"Sorry, there was an error fetching trending shows. Please try again later.", ephemeral=True)
            return

        if not trending_shows:
            await self.send_response(ctx, f"No trending shows found for the '{time_window}' window at the moment.", ephemeral=True)
            return

        title_time_window = "Day" if time_window.lower() == 'day' else "Week"
        embed = discord.Embed(
            title=f"🔥 Trending TV Shows This {title_time_window}",
            color=discord.Color.orange() # A fiery color for trending
        )

        if trending_shows[0].get('poster_path'):
            thumbnail_url = tmdb_client.get_poster_url(trending_shows[0]['poster_path'], size="w185")
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)
        
        embed.set_footer(text=f"Top {len(trending_shows[:10])} shows from TMDB | Data for {time_window.capitalize()}")

        for i, show in enumerate(trending_shows[:10]): # Display top 10
            show_id = show.get('id')
            name = show.get('name', 'N/A')
            first_air_date = show.get('first_air_date', '')
            year = first_air_date[:4] if first_air_date and len(first_air_date) >=4 else 'N/A'
            overview = show.get('overview', 'No overview available.')
            rating = show.get('vote_average', 0.0)
            
            # Truncate overview to a reasonable length
            max_overview_length = 150 # Max length for overview in field
            if len(overview) > max_overview_length:
                overview = overview[:max_overview_length-3] + "..."

            tmdb_url = f"https://www.themoviedb.org/tv/{show_id}" if show_id else "#"
            
            field_name = f"{i+1}. {name} ({year}) ⭐ {rating:.1f}/10"
            field_value = f"[View on TMDB]({tmdb_url})\n{overview}"
            
            embed.add_field(name=field_name, value=field_value, inline=False)

        try:
            await self.send_response(ctx, embed=embed)
        except discord.HTTPException as e:
            print(f"Error sending trending shows embed: {e}")
            await self.send_response(ctx, "Sorry, there was an error displaying the trending shows.", ephemeral=True)
        except Exception as e:
            print(f"Unexpected error sending trending shows: {e}")
            await self.send_response(ctx, "An unexpected error occurred.", ephemeral=True)

    @tasks.loop(minutes=30) # Check every 30 minutes for testing, change to hours=6 or hours=12 for production
    async def check_new_episodes(self):
        """Background task to check for new episodes of subscribed shows."""
        print(f"[{datetime.now()}] Running check_new_episodes task...")
        all_subscriptions = await self.bot.loop.run_in_executor(None, self.db_manager.get_all_tv_subscriptions) # {user_id: [subs]}
        
        if not all_subscriptions:
            print("No active TV subscriptions to check.")
            return

        today = date.today()

        for user_id_str, user_subs in all_subscriptions.items():
            try:
                user_id = int(user_id_str) # Ensure user_id is int for discord.py
                user = await self.bot.fetch_user(user_id)
                if not user:
                    print(f"Could not fetch user {user_id}. Skipping their subscriptions.")
                    continue
            except ValueError:
                print(f"Invalid user_id format '{user_id_str}' in subscriptions. Skipping.")
                continue
            except discord.NotFound:
                print(f"User {user_id} not found. Removing their subscriptions or marking as inactive might be needed.")
                # Consider adding logic here to handle users who left servers or deleted accounts.
                # For now, we just skip.
                continue
            except discord.HTTPException as e:
                print(f"HTTP error fetching user {user_id}: {e}. Skipping their subscriptions for this cycle.")
                continue
            except Exception as e: # Catch any other unexpected error during user fetch
                print(f"Unexpected error fetching user {user_id}: {e}. Skipping.")
                continue

            for sub in user_subs:
                if 'show_tmdb_id' not in sub or 'show_name' not in sub:
                    user_id_for_log = sub.get('user_id', 'Unknown User')
                    malformed_sub_info = {k: v for k, v in sub.items() if k != 'user_id'} # Avoid logging user_id directly if not needed
                    logger.warning(f"Skipping malformed TV show subscription for user {user_id_for_log}: {malformed_sub_info}. Missing 'show_tmdb_id' or 'show_name'.")
                    continue

                show_id = sub['show_tmdb_id']
                show_name_stored = sub['show_name'] # Name as stored by user
                last_notified_ep_details = sub.get('last_notified_episode_details') # dict or None

                try:
                    # Fetch full show details, which includes 'next_episode_to_air'
                    # and 'last_episode_to_air'
                    show_details_tmdb = await self.bot.loop.run_in_executor(None, tmdb_client.get_show_details, show_id)

                    if not show_details_tmdb:
                        print(f"Could not fetch details for show ID {show_id} ({show_name_stored}). Skipping.")
                        continue
                    
                    actual_show_name_tmdb = show_details_tmdb.get('name', show_name_stored) # Prefer TMDB name

                    # NEW: Collect all episodes to notify about (can be multiple)
                    episodes_to_notify = []

                    # Check 'next_episode_to_air' first
                    next_ep = show_details_tmdb.get('next_episode_to_air')
                    if next_ep and next_ep.get('air_date') and next_ep.get('id'):
                        try:
                            next_air_date_obj = datetime.strptime(next_ep['air_date'], '%Y-%m-%d').date()
                            # Notify if episode airs today or in the past (and hasn't been notified)
                            if next_air_date_obj <= today:
                                # Check if not already notified
                                already_notified = await self.bot.loop.run_in_executor(
                                    None, 
                                    self.db_manager.has_user_been_notified_for_episode, 
                                    user_id, 
                                    show_id, 
                                    next_ep.get('id')
                                )
                                if not already_notified:
                                    episodes_to_notify.append(next_ep)
                        except ValueError:
                            print(f"Invalid air_date format for next_episode_to_air for show {show_id}: {next_ep.get('air_date')}")
                    
                    # Check 'last_episode_to_air' separately (might be different from next_episode_to_air)
                    last_aired_ep = show_details_tmdb.get('last_episode_to_air')
                    if last_aired_ep and last_aired_ep.get('air_date') and last_aired_ep.get('id'):
                        try:
                            last_aired_date_obj = datetime.strptime(last_aired_ep['air_date'], '%Y-%m-%d').date()
                            # Notify if it aired recently (within last 7 days) and hasn't been notified
                            if (today - timedelta(days=7)) <= last_aired_date_obj <= today:
                                # Check if not already notified and not already in episodes_to_notify
                                already_notified = await self.bot.loop.run_in_executor(
                                    None, 
                                    self.db_manager.has_user_been_notified_for_episode, 
                                    user_id, 
                                    show_id, 
                                    last_aired_ep.get('id')
                                )
                                if not already_notified:
                                    # Avoid duplicate if next_ep and last_aired_ep are the same episode
                                    if not any(ep.get('id') == last_aired_ep.get('id') for ep in episodes_to_notify):
                                        episodes_to_notify.append(last_aired_ep)
                        except ValueError:
                            print(f"Invalid air_date format for last_episode_to_air for show {show_id}: {last_aired_ep.get('air_date')}")

                    # Send notifications for all qualifying episodes
                    for episode_to_notify in episodes_to_notify:
                        ep_id = episode_to_notify.get('id')
                        ep_name = episode_to_notify.get('name', 'Episode Name TBA')
                        ep_season = episode_to_notify.get('season_number', 'S?')
                        ep_num = episode_to_notify.get('episode_number', 'E?')
                        ep_air_date_str = episode_to_notify.get('air_date', 'Unknown Air Date')
                        
                        # Format air date nicely
                        try:
                            date_obj = datetime.strptime(ep_air_date_str, '%Y-%m-%d').date()
                            ep_air_date_str = date_obj.strftime('%Y-%m-%d')  # Keep YYYY-MM-DD format as requested
                        except ValueError:
                            pass  # Keep original format if parsing fails

                        # Create simplified embed
                        embed = discord.Embed(
                            title=f"📺 New Episode Alert: {actual_show_name_tmdb}",
                            description=f"**S{ep_season:02d}E{ep_num:02d} - \"{ep_name}\"** has aired!",
                            color=discord.Color.green()
                        )
                        embed.add_field(name="Air Date", value=ep_air_date_str, inline=True)
                        
                        # Add episode rating if available
                        if episode_to_notify.get('vote_average') and episode_to_notify.get('vote_average') > 0:
                            embed.add_field(name="Episode Rating", value=f"{episode_to_notify['vote_average']:.1f}/10", inline=True)
                        
                        # Use show poster as thumbnail (cleaner than episode stills)
                        if show_details_tmdb.get('poster_path'):
                            poster_url = tmdb_client.get_poster_url(show_details_tmdb['poster_path'], size="w185")
                            if poster_url:
                                embed.set_thumbnail(url=poster_url)

                        try:
                            await user.send(embed=embed)
                            logger.info(f"Sent new episode notification for '{actual_show_name_tmdb}' S{ep_season:02d}E{ep_num:02d} to user {user_id}.")
                            
                            # Add to sent_episode_notifications table
                            await self.bot.loop.run_in_executor(
                                None, 
                                self.db_manager.add_sent_episode_notification,
                                user_id,
                                show_id,
                                ep_id,
                                ep_season,
                                ep_num
                            )
                            logger.info(f"Logged sent notification for User {user_id}, Show {show_id}, Episode {ep_id}.")

                        except discord.Forbidden:
                            print(f"Could not send DM to user {user_id} (DM disabled or bot blocked).")
                        except discord.HTTPException as e:
                            print(f"HTTP error sending episode DM to user {user_id}: {e}")
                        except Exception as e:
                            print(f"Error sending episode notification to user {user_id}: {e}")

                    # Update last_notified_episode_details to the most recent episode we notified about
                    if episodes_to_notify:
                        # Find the most recent episode by air date
                        most_recent_episode = max(episodes_to_notify, key=lambda ep: ep.get('air_date', '1900-01-01'))
                        await self.bot.loop.run_in_executor(None, self.db_manager.update_last_notified_episode_details, user_id, show_id, most_recent_episode)
                        logger.info(f"Updated last notified episode for user {user_id}, show {show_id} to episode ID {most_recent_episode.get('id')}.")

                except Exception as e:
                    print(f"Error fetching or processing show {show_id} for user {user_id}: {e}")

    @check_new_episodes.before_loop
    async def before_check_new_episodes(self):
        await self.bot.wait_until_ready()
        print("TVShows check_new_episodes task is waiting for bot to be ready...")

async def setup(bot):
    await bot.add_cog(TVShows(bot))