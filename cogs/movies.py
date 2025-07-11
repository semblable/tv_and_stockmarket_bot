# cogs/movies.py

import discord
from discord.ext import commands, tasks
from api_clients import tmdb_client
from data_manager import DataManager # Will be used for subscription commands
from datetime import datetime, date, timedelta, time
import asyncio
import re # For parsing director from credits
import logging # Import logging

logger = logging.getLogger(__name__)

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

def format_runtime(minutes):
    if minutes is None:
        return "N/A"
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"

class MoviesCog(commands.Cog, name="Movies"):
    def __init__(self, bot):
        self.bot = bot
        self.db_manager = bot.db_manager # Get the DataManager instance from the bot
        logger.info("MoviesCog: Initializing and starting check_movie_releases task.")
        self.check_movie_releases.start()

    def cog_unload(self):
        logger.info("MoviesCog: Unloading and cancelling check_movie_releases task.")
        self.check_movie_releases.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("MoviesCog is ready and listener has been triggered.")

    @commands.hybrid_command(name="movie_info", description="Get detailed information about a movie.")
    @discord.app_commands.describe(movie_name="The name of the movie to get information for")
    async def movie_info(self, ctx: commands.Context, *, movie_name: str):
        """
Fetches and displays detailed information about a specific movie from TMDB.
This includes overview, genres, release date, runtime, director, cast, and more.

Usage examples:
`!movie_info Inception`
`/movie_info movie_name:The Matrix`
        """
        await ctx.defer(ephemeral=True)

        try:
            search_results = await self.bot.loop.run_in_executor(None, tmdb_client.search_movie, movie_name)
        except Exception as e:
            print(f"Error searching for movie '{movie_name}' in movie_info: {e}")
            await ctx.followup.send(f"Sorry, there was an error searching for '{movie_name}'. Please try again later.", ephemeral=True)
            return

        if not search_results:
            await ctx.followup.send(f"No movies found for '{movie_name}'.", ephemeral=True)
            return

        selected_movie_tmdb_search_data = None

        if len(search_results) == 1:
            selected_movie_tmdb_search_data = search_results[0]
        elif len(search_results) > 1:
            display_results = search_results[:5] # Limit to top 5 results

            embeds_list = []
            message_content = "Multiple movies found. Please react with the number of the movie you want info for:"

            for i, movie_data_item in enumerate(display_results):
                year_str = movie_data_item.get('release_date')
                year = year_str[:4] if year_str and len(year_str) >= 4 else 'N/A'
                
                movie_embed = discord.Embed(
                    description=f"{NUMBER_EMOJIS[i]} **{movie_data_item['title']} ({year})**",
                    color=discord.Color.blurple() # Using blurple for info
                )
                
                poster_path = movie_data_item.get('poster_path')
                if poster_path:
                    poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                    if poster_url:
                        movie_embed.set_thumbnail(url=poster_url)
                
                embeds_list.append(movie_embed)

            prompt_msg_obj = await ctx.followup.send(content=message_content, embeds=embeds_list, ephemeral=False, wait=True) # Send non-ephemeral for reactions

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
                    selected_movie_tmdb_search_data = display_results[choice_idx]
                    try:
                        await prompt_msg_obj.delete() # Clean up prompt
                    except discord.HTTPException:
                        pass # Ignore if already deleted or no perms
                else:
                    await ctx.followup.send("Invalid reaction. Movie info cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
            except asyncio.TimeoutError:
                await ctx.followup.send("Selection timed out. Movie info cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
            except Exception as e:
                print(f"Error during reaction-based movie selection for '{movie_name}' by {ctx.author.id}: {e}")
                await ctx.followup.send("An error occurred during selection. Movie info cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
        
        if not selected_movie_tmdb_search_data or 'id' not in selected_movie_tmdb_search_data:
            await ctx.followup.send("Could not determine the movie to fetch details for. Please try again.", ephemeral=True)
            return

        movie_id = selected_movie_tmdb_search_data['id']

        try:
            # Fetch full details including credits and keywords
            movie_details = await self.bot.loop.run_in_executor(None, tmdb_client.get_movie_details, movie_id, 'credits,keywords')
        except Exception as e:
            print(f"Error fetching details for movie ID {movie_id} in movie_info: {e}")
            await ctx.followup.send(f"Sorry, there was an error fetching details for '{selected_movie_tmdb_search_data.get('title', 'the selected movie')}'.", ephemeral=True)
            return

        if not movie_details:
            await ctx.followup.send(f"Could not retrieve details for '{selected_movie_tmdb_search_data.get('title', 'the selected movie')}' (ID: {movie_id}).", ephemeral=True)
            return

        # Construct the embed
        embed_title = movie_details.get('title', 'N/A')
        if movie_details.get('release_date'):
            try:
                release_year = datetime.strptime(movie_details['release_date'], '%Y-%m-%d').year
                embed_title += f" ({release_year})"
            except ValueError:
                pass # Keep title as is if date parsing fails

        embed = discord.Embed(
            title=embed_title,
            description=movie_details.get('overview', 'No overview available.'),
            color=discord.Color.gold(), # Gold for movie info
            url=f"https://www.themoviedb.org/movie/{movie_id}"
        )

        if movie_details.get('poster_path'):
            embed.set_thumbnail(url=tmdb_client.get_poster_url(movie_details['poster_path'], size="w342"))

        # Release Date
        release_date_str = "N/A"
        if movie_details.get('release_date'):
            try:
                release_date_obj = datetime.strptime(movie_details['release_date'], '%Y-%m-%d')
                release_date_str = release_date_obj.strftime('%B %d, %Y')
            except ValueError:
                release_date_str = movie_details['release_date'] # Fallback to raw string
        embed.add_field(name="🎬 Release Date", value=release_date_str, inline=True)

        # Runtime
        runtime_str = format_runtime(movie_details.get('runtime'))
        embed.add_field(name="⏱️ Runtime", value=runtime_str, inline=True)
        
        # TMDB Rating
        rating = movie_details.get('vote_average')
        vote_count = movie_details.get('vote_count')
        rating_str = "N/A"
        if rating is not None:
            rating_str = f"{rating:.1f}/10 ({vote_count:,} votes)"
        embed.add_field(name="⭐ TMDB Rating", value=rating_str, inline=True)

        # Genres
        genres = movie_details.get('genres')
        if genres:
            genre_names = [genre['name'] for genre in genres]
            embed.add_field(name="🎭 Genres", value=", ".join(genre_names) if genre_names else "N/A", inline=False)

        # Director
        director_str = "N/A"
        if movie_details.get('credits') and movie_details['credits'].get('crew'):
            directors = [person['name'] for person in movie_details['credits']['crew'] if person.get('job') == 'Director']
            if directors:
                director_str = ", ".join(directors)
        embed.add_field(name="🎥 Director(s)", value=director_str, inline=False)

        # Main Cast (e.g., top 5-7 actors)
        cast_str = "N/A"
        if movie_details.get('credits') and movie_details['credits'].get('cast'):
            main_cast = [actor['name'] for actor in movie_details['credits']['cast'][:7]] # Get top 7 actors
            if main_cast:
                cast_str = ", ".join(main_cast)
        embed.add_field(name="🌟 Main Cast", value=cast_str, inline=False)
        
        embed.set_footer(text=f"Movie ID: {movie_id} | Data from TMDB")

        try:
            await ctx.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            print(f"Error sending movie_info embed for {movie_id}: {e}")
            await ctx.followup.send("There was an issue displaying the movie information. The embed might be too large.", ephemeral=True)

    @commands.hybrid_command(name="movie_subscribe", description="Subscribe to movie release notifications.")
    @discord.app_commands.describe(movie_name="The name of the movie to subscribe to")
    async def movie_subscribe(self, ctx: commands.Context, *, movie_name: str):
        """
Allows a user to subscribe to notifications for a specific movie's release.
If multiple movies match the name, you'll be prompted to select the correct one.

Usage examples:
`!movie_subscribe Dune Part Two`
`/movie_subscribe movie_name:Oppenheimer`
        """
        await ctx.defer(ephemeral=True)

        try:
            search_results = await self.bot.loop.run_in_executor(None, tmdb_client.search_movie, movie_name)
        except Exception as e:
            print(f"Error searching for movie '{movie_name}' in movie_subscribe: {e}")
            await ctx.followup.send(f"Sorry, there was an error searching for '{movie_name}'. Please try again later.", ephemeral=True)
            return

        if not search_results:
            await ctx.followup.send(f"No movies found for '{movie_name}'.", ephemeral=True)
            return

        selected_movie_data = None # This will store the chosen movie's data from search results

        if len(search_results) == 1:
            selected_movie_data = search_results[0]
        elif len(search_results) > 1:
            display_results = search_results[:5]

            embeds_list = []
            message_content = "Multiple movies found. Please react with the number of the movie you want to subscribe to:"

            for i, movie_data_item in enumerate(display_results):
                year_str = movie_data_item.get('release_date')
                year = year_str[:4] if year_str and len(year_str) >= 4 else 'N/A'
                
                movie_embed = discord.Embed(
                    description=f"{NUMBER_EMOJIS[i]} **{movie_data_item['title']} ({year})**",
                    color=discord.Color.green() # Green for subscribe action
                )
                
                poster_path = movie_data_item.get('poster_path')
                if poster_path:
                    poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                    if poster_url:
                        movie_embed.set_thumbnail(url=poster_url)
                
                embeds_list.append(movie_embed)

            prompt_msg_obj = await ctx.followup.send(content=message_content, embeds=embeds_list, ephemeral=False, wait=True)

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
                    selected_movie_data = display_results[choice_idx]
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                else:
                    await ctx.followup.send("Invalid reaction. Subscription cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
            except asyncio.TimeoutError:
                await ctx.followup.send("Selection timed out. Subscription cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
            except Exception as e:
                print(f"Error during reaction-based movie subscription selection for '{movie_name}' by {ctx.author.id}: {e}")
                await ctx.followup.send("An error occurred during selection. Subscription cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
        
        if not selected_movie_data or 'id' not in selected_movie_data or 'title' not in selected_movie_data or 'release_date' not in selected_movie_data:
            await ctx.followup.send("Could not get necessary movie details (ID, title, release date) for subscription. Please try again.", ephemeral=True)
            return

        movie_id = selected_movie_data['id']
        actual_movie_title = selected_movie_data['title']
        release_date = selected_movie_data['release_date'] # YYYY-MM-DD format from TMDB

        if not release_date: # Ensure release date is present
            await ctx.followup.send(f"Cannot subscribe to '{actual_movie_title}' as its release date is not available.", ephemeral=True)
            return

        # The add_movie_subscription in DataManager now takes poster_path as well.
        # We have selected_movie_data which should contain 'poster_path'.
        poster_path = selected_movie_data.get('poster_path')
        if poster_path is None: # Should ideally have a fallback or default
            logger.warning(f"Poster path not found for movie {movie_id} during subscription for user {ctx.author.id}. Using empty string.")
            poster_path = ""

        try:
            success = await self.bot.loop.run_in_executor(None, self.db_manager.add_movie_subscription, ctx.author.id, movie_id, actual_movie_title, poster_path)
            if success: # This now directly reflects DB operation success (MERGE)
                await ctx.followup.send(f"Successfully subscribed to **{actual_movie_title}** (Release: {release_date})!", ephemeral=True)
            else:
                # If MERGE fails, it's a DB issue, not "already subscribed" as MERGE handles that.
                await ctx.followup.send(f"Could not subscribe to **{actual_movie_title}** due to a database error. Please try again later.", ephemeral=True)
        except Exception as e:
            print(f"Error adding movie subscription for user {ctx.author.id} to movie {movie_id} ('{actual_movie_title}'): {e}")
            await ctx.followup.send(f"Sorry, there was an error subscribing to '{actual_movie_title}'. Please try again later.", ephemeral=True)

    @commands.hybrid_command(name="movie_unsubscribe", description="Unsubscribe from movie release notifications.")
    @discord.app_commands.describe(movie_name="The name of the movie to unsubscribe from")
    async def movie_unsubscribe(self, ctx: commands.Context, *, movie_name: str):
        """
Allows a user to unsubscribe from notifications for a specific movie.
If multiple subscribed movies match the name, you'll be prompted.

Usage examples:
`!movie_unsubscribe Dune Part Two`
`/movie_unsubscribe movie_name:Oppenheimer`
        """
        await ctx.defer(ephemeral=True)
        user_id = ctx.author.id

        try:
            subscriptions = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_movie_subscriptions, user_id)
        except Exception as e:
            print(f"Error getting movie subscriptions for user {user_id}: {e}")
            await ctx.followup.send("Sorry, there was an error fetching your movie subscriptions.", ephemeral=True)
            return

        if not subscriptions:
            await ctx.followup.send("You are not subscribed to any movies.", ephemeral=True)
            return

        movie_to_unsubscribe = None
        
        # The new get_user_movie_subscriptions returns a list of dicts, each having 'title' and 'tmdb_id'
        matching_subscriptions = [
            sub for sub in subscriptions if movie_name.lower() in sub['title'].lower()
        ]

        if not matching_subscriptions:
            await ctx.followup.send(f"No movie matching '{movie_name}' found in your subscriptions. Use `/my_movies` to see them.", ephemeral=True)
            return

        if len(matching_subscriptions) == 1:
            movie_to_unsubscribe = matching_subscriptions[0]
        else:
            display_results = matching_subscriptions[:5]

            embeds_list = []
            message_content = "Multiple subscribed movies match. React with the number to unsubscribe:"

            for i, sub_data_item in enumerate(display_results):
                movie_embed = discord.Embed(
                    description=f"{NUMBER_EMOJIS[i]} **{sub_data_item['title']}**", # Removed year for simplicity
                    color=discord.Color.red() # Red for unsubscribe
                )
                
                poster_path = sub_data_item.get('poster_path')
                if poster_path:
                    poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                    if poster_url:
                        movie_embed.set_thumbnail(url=poster_url)

                embeds_list.append(movie_embed)
            
            if not embeds_list:
                 await ctx.followup.send("Could not prepare selection list. Please try again.", ephemeral=True)
                 return

            prompt_msg_obj = await ctx.followup.send(content=message_content, embeds=embeds_list, ephemeral=False, wait=True)

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
                    movie_to_unsubscribe = display_results[choice_idx]
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                else:
                    await ctx.followup.send("Invalid selection. Unsubscription cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
            except asyncio.TimeoutError:
                await ctx.followup.send("Selection timed out. Unsubscription cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
            except Exception as e:
                print(f"Error during reaction-based movie unsubscription selection for '{movie_name}' by {ctx.author.id}: {e}")
                await ctx.followup.send("An error occurred during selection. Unsubscription cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return

        if not movie_to_unsubscribe:
            await ctx.followup.send("Could not identify movie to unsubscribe from. Please try again.", ephemeral=True)
            return

        title_of_movie_unsubscribed = movie_to_unsubscribe['title']
        movie_tmdb_id_to_remove = movie_to_unsubscribe['tmdb_id']

        try:
            success = await self.bot.loop.run_in_executor(None, self.db_manager.remove_movie_subscription, user_id, movie_tmdb_id_to_remove)
            if success:
                await ctx.followup.send(f"Successfully unsubscribed from **{title_of_movie_unsubscribed}**.", ephemeral=True)
            else:
                await ctx.followup.send(f"Could not unsubscribe from **{title_of_movie_unsubscribed}** due to a database error. Please try again later.", ephemeral=True)
        except Exception as e:
            print(f"Error removing movie subscription for user {user_id} from movie {movie_tmdb_id_to_remove} ('{title_of_movie_unsubscribed}'): {e}")
            await ctx.followup.send(f"Sorry, there was an error unsubscribing from '{title_of_movie_unsubscribed}'.", ephemeral=True)

    @commands.hybrid_command(name="my_movies", description="Lists your subscribed movies.")
    async def my_movies(self, ctx: commands.Context):
        """
Lists all movies you are currently subscribed to for release notifications.

Usage examples:
`!my_movies`
`/my_movies`
        """
        await ctx.defer(ephemeral=True)
        user_id = ctx.author.id

        try:
            subscriptions = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_movie_subscriptions, user_id)
        except Exception as e:
            print(f"Error getting movie subscriptions for user {user_id} in my_movies: {e}")
            await ctx.followup.send("Sorry, there was an error fetching your movie subscriptions.", ephemeral=True)
            return

        if not subscriptions:
            await ctx.followup.send("You are not subscribed to any movies. Use `/movie_subscribe` to add some!", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🎬 Your Movie Subscriptions ({len(subscriptions)})",
            color=discord.Color.dark_orange()
        )
        embed.set_footer(text="Release dates from TMDB at time of subscription.")

        description_lines = []
        for sub in subscriptions:
            movie_title = sub.get('movie_title', 'Unknown Title')
            release_date = sub.get('release_date', 'Unknown Release Date')
            
            # Format release date for display
            try:
                date_obj = datetime.strptime(release_date, '%Y-%m-%d')
                formatted_date = date_obj.strftime('%b %d, %Y') # e.g., Oct 15, 1999
            except (ValueError, TypeError):
                formatted_date = release_date # Fallback to stored string

            description_lines.append(f"• **{movie_title}** (Releasing: {formatted_date})")

        if description_lines:
            # Discord embed description limit is 4096. Field value limit 1024.
            # For simplicity, putting all in description. If too long, pagination or fields would be needed.
            full_description = "\n".join(description_lines)
            if len(full_description) > 4000: # A bit less than 4096 for safety
                # Truncate and add a note
                # A more robust solution would use pagination or fields.
                num_shown = 0
                temp_desc = ""
                for line in description_lines:
                    if len(temp_desc) + len(line) +1 < 3900: # Check before adding
                        temp_desc += line + "\n"
                        num_shown +=1
                    else:
                        break
                full_description = temp_desc
                full_description += f"\n...and {len(subscriptions) - num_shown} more movies not shown."
            embed.description = full_description
        else: # Should not happen if subscriptions is not empty
            embed.description = "You are not subscribed to any movies."
            
        try:
            await ctx.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            print(f"Error sending my_movies embed for user {user_id}: {e}")
            await ctx.followup.send("There was an issue displaying your movie subscriptions.", ephemeral=True)

    @tasks.loop(hours=24) # Run once a day
    async def check_movie_releases(self):
        """Checks for movie releases and notifies subscribed users."""
        if not self.db_manager:
            logger.error("MoviesCog: DataManager (db_manager) not available. Cannot check movie releases.")
            return

        logger.info("MoviesCog: check_movie_releases task is running.")
        all_subscriptions_by_user = await self.bot.loop.run_in_executor(None, self.db_manager.get_all_movie_subscriptions) # Uses DB
        if not all_subscriptions_by_user: # This is a dict {user_id_str: [subs_list]}
            logger.info("MoviesCog: No movie subscriptions found to check.")
            return

        today = date.today()
        logger.info(f"MoviesCog: Today's date for release check: {today}")

        for user_id_str, user_subs_list in all_subscriptions_by_user.items():
            try:
                user_id = int(user_id_str)
                discord_user_obj = await self.bot.fetch_user(user_id) # Renamed for clarity
                if not discord_user_obj:
                    logger.warning(f"MoviesCog: Could not fetch user {user_id}. Skipping their movie notifications.")
                    continue

                dnd_enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, 'dnd_enabled', False)
                dnd_start_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, 'dnd_start_time', "00:00")
                dnd_end_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, 'dnd_end_time', "00:00")
                
                try:
                    dnd_start_time_obj = datetime.strptime(dnd_start_str, '%H:%M').time() # Renamed
                    dnd_end_time_obj = datetime.strptime(dnd_end_str, '%H:%M').time() # Renamed
                except ValueError:
                    logger.warning(f"MoviesCog: Invalid DND time format for user {user_id}. Using defaults.")
                    dnd_start_time_obj = time(0,0)
                    dnd_end_time_obj = time(0,0)

                for sub_item_dict in user_subs_list: # Renamed for clarity
                    movie_tmdb_id = sub_item_dict.get('tmdb_id')
                    # Title from subscription is a fallback if TMDB fetch fails for title
                    movie_title_from_sub = sub_item_dict.get('title', 'Unknown Movie')
                    notified_status_bool = bool(sub_item_dict.get('notified_status', 0))

                    if notified_status_bool: # Already notified
                        continue

                    if not movie_tmdb_id: # Should not happen if data is clean
                        logger.warning(f"MoviesCog: Subscription item for user {user_id} is missing tmdb_id. Sub: {sub_item_dict}")
                        continue
                        
                    # Fetch fresh movie details from TMDB for release date and current title.
                    try:
                        fresh_movie_details = await self.bot.loop.run_in_executor(None, tmdb_client.get_movie_details, movie_tmdb_id)
                        if not fresh_movie_details or not fresh_movie_details.get('release_date'):
                            logger.warning(f"MoviesCog: Could not fetch release date for movie '{movie_title_from_sub}' (ID: {movie_tmdb_id}) for user {user_id} from TMDB. Skipping.")
                            continue
                        release_date_str_from_tmdb = fresh_movie_details['release_date']
                        # Use fresh title from TMDB if available, otherwise fallback to subscribed title
                        actual_movie_title_to_display = fresh_movie_details.get('title', movie_title_from_sub)
                    except Exception as tmdb_err:
                        logger.error(f"MoviesCog: Error fetching TMDB details for movie ID {movie_tmdb_id} during release check: {tmdb_err}")
                        continue # Skip this movie for this user if TMDB fails
                    
                    try:
                        release_date_obj_from_tmdb = datetime.strptime(release_date_str_from_tmdb, '%Y-%m-%d').date() # Renamed
                    except ValueError:
                        logger.error(f"MoviesCog: Invalid release date format '{release_date_str_from_tmdb}' from TMDB for movie '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}).")
                        continue # Skip if date is malformed
                    
                    # Notify if release date is today or in the past (and not yet notified)
                    if release_date_obj_from_tmdb <= today:
                        logger.info(f"MoviesCog: Movie '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) released on or before {today} for user {user_id}. Preparing notification.")
                        
                        current_time_obj = datetime.now().time() # Renamed
                        is_dnd_active_now = False # Renamed
                        if dnd_enabled:
                            if dnd_start_time_obj <= dnd_end_time_obj: # Normal DND period (e.g., 10:00 - 18:00)
                                if dnd_start_time_obj <= current_time_obj <= dnd_end_time_obj: is_dnd_active_now = True
                            else: # Overnight DND period (e.g., 22:00 - 06:00 next day)
                                if current_time_obj >= dnd_start_time_obj or current_time_obj <= dnd_end_time_obj: is_dnd_active_now = True
                        
                        if is_dnd_active_now:
                            logger.info(f"MoviesCog: DND active for user {user_id}. Skipping notification for '{actual_movie_title_to_display}'.")
                            continue

                        # Construct embed using fresh_movie_details
                        embed_title = f"🎬 Movie Released: {actual_movie_title_to_display}"
                        embed_description = (
                            f"The movie **{actual_movie_title_to_display}** has been released!\n\n"
                            f"**Release Date:** {release_date_obj_from_tmdb.strftime('%B %d, %Y')}\n"
                            f"**Overview:** {fresh_movie_details.get('overview', 'No overview available.')[:500]}" # Truncate overview
                        )
                        embed_color = discord.Color.blue() # Consistent color for release notifications
                        
                        notification_embed = discord.Embed(title=embed_title, description=embed_description, color=embed_color) # Renamed
                        if fresh_movie_details.get('poster_path'):
                            poster_url = tmdb_client.get_poster_url(fresh_movie_details['poster_path'])
                            if poster_url: # Ensure URL is valid
                                notification_embed.set_thumbnail(url=poster_url)
                        
                        notification_embed.set_footer(text=f"Movie ID: {movie_tmdb_id} | Data from TMDB")
                        
                        try:
                            await discord_user_obj.send(embed=notification_embed)
                            logger.info(f"MoviesCog: Sent release notification for '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) to user {user_id}.")
                            
                            # Update notified status in DB
                            update_success = await self.bot.loop.run_in_executor(None, self.db_manager.update_movie_notified_status, user_id, movie_tmdb_id, True)
                            if update_success:
                                logger.info(f"MoviesCog: Updated notified status for '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) for user {user_id}.")
                            else:
                                logger.error(f"MoviesCog: FAILED to update notified status for '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) for user {user_id}.")

                        except discord.Forbidden:
                            logger.warning(f"MoviesCog: Cannot send DM to user {user_id} (Forbidden). They might have DMs disabled or blocked the bot.")
                        except discord.HTTPException as ehttp: # More specific exception
                            logger.error(f"MoviesCog: HTTP error sending DM for movie '{actual_movie_title_to_display}' to user {user_id}: {ehttp}")
                        except Exception as e_send_dm: # Catch other potential errors during send
                            logger.error(f"MoviesCog: Error sending movie release DM for '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) to user {user_id}: {e_send_dm}", exc_info=True)
            except Exception as e_user_loop: # More specific exception name
                logger.error(f"MoviesCog: Error processing subscriptions for user ID string '{user_id_str}': {e_user_loop}", exc_info=True)

    @check_movie_releases.before_loop
    async def before_check_movie_releases(self):
        print("Task: check_movie_releases waiting for bot to be ready...")
        await self.bot.wait_until_ready()
        print("Task: check_movie_releases bot is ready. Loop starting.")

async def setup(bot: commands.Bot):
    await bot.add_cog(MoviesCog(bot))
    logger.info("MoviesCog has been loaded.")