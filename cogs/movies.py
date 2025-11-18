# cogs/movies.py

import discord
from discord.ext import commands, tasks
from api_clients import tmdb_client
from api_clients.tmdb_client import TMDBError, TMDBConnectionError, TMDBAPIError
from data_manager import DataManager
from datetime import datetime, date, timedelta, time
import asyncio
import re
import logging
import typing
from utils.paginator import BasePaginatorView

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

class MyMoviesPaginatorView(BasePaginatorView):
    def __init__(self, *, timeout=300, user_id: int, items: list, items_per_page: int = 10):
        super().__init__(timeout=timeout, user_id=user_id, items=items, items_per_page=items_per_page)

    async def _get_embed_for_current_page(self) -> discord.Embed:
        self._update_button_states()

        start_index = self.current_page * self.items_per_page
        end_index = start_index + self.items_per_page
        page_subs = self.items[start_index:end_index]

        embed_title = f"🎬 Your Movie Subscriptions ({len(self.items)})"
        if self.total_pages > 1:
            embed_title += f" (Page {self.current_page + 1}/{self.total_pages})"
        
        embed = discord.Embed(title=embed_title, color=discord.Color.dark_orange())
        embed.set_footer(text="Release dates from TMDB at time of subscription.")

        description_lines = []
        for sub in page_subs:
            movie_title = sub.get('movie_title', 'Unknown Title')
            release_date = sub.get('release_date', 'Unknown Release Date')
            tmdb_id = sub.get('movie_tmdb_id') # Ensure this key matches DB result
            if not tmdb_id and 'tmdb_id' in sub: tmdb_id = sub['tmdb_id'] # Fallback if key varies

            try:
                date_obj = datetime.strptime(release_date, '%Y-%m-%d')
                formatted_date = date_obj.strftime('%b %d, %Y')
            except (ValueError, TypeError):
                formatted_date = release_date

            line = f"• **{movie_title}** (Releasing: {formatted_date})"
            if tmdb_id:
                line = f"• **[{movie_title}](https://www.themoviedb.org/movie/{tmdb_id})** (Releasing: {formatted_date})"
            description_lines.append(line)

        if description_lines:
            embed.description = "\n".join(description_lines)
        else:
            embed.description = "No movie subscriptions to display on this page."
            
        return embed

class MoviesCog(commands.Cog, name="Movies"):
    def __init__(self, bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager
        logger.info("MoviesCog: Initializing and starting check_movie_releases task.")
        self.check_movie_releases.start()

    async def send_response(self, ctx, content=None, embed=None, embeds=None, ephemeral=True, wait=False):
        kwargs = {}
        if content is not None: kwargs['content'] = content
        if embed is not None: kwargs['embed'] = embed
        if embeds is not None: kwargs['embeds'] = embeds
        
        if ctx.interaction:
            kwargs['ephemeral'] = ephemeral
            kwargs['wait'] = wait
            return await ctx.interaction.followup.send(**kwargs)
        else:
            return await ctx.send(**kwargs)

    def cog_unload(self):
        logger.info("MoviesCog: Unloading and cancelling check_movie_releases task.")
        self.check_movie_releases.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("MoviesCog is ready and listener has been triggered.")

    async def movie_autocomplete(self, interaction: discord.Interaction, current: str) -> typing.List[discord.app_commands.Choice[str]]:
        """
        Autocomplete for movies using TMDB search.
        """
        if not current:
             return []
        if len(current) < 3:
             return []
        
        # Search TMDB
        results = await self.bot.loop.run_in_executor(None, tmdb_client.search_movie, current)
        
        choices = []
        if results:
            for movie in results[:25]:
                name = movie.get('title') or movie.get('original_title')
                if not name: continue
                
                year_str = movie.get('release_date', '')
                year = year_str[:4] if year_str else ''
                label = f"{name} ({year})" if year else name
                
                # Truncate to fit Discord limits (100 chars)
                choices.append(discord.app_commands.Choice(name=label[:100], value=name[:100]))
        return choices

    async def movie_subscription_autocomplete(self, interaction: discord.Interaction, current: str) -> typing.List[discord.app_commands.Choice[str]]:
        """
        Autocomplete for movies the user is subscribed to.
        """
        user_id = interaction.user.id
        try:
            subs = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_movie_subscriptions, user_id)
        except Exception:
            return []
        
        if not subs:
            return []

        choices = []
        current_lower = current.lower()
        
        # subs is list of dicts with 'movie_title' key (based on DataManager.get_user_movie_subscriptions)
        # Note: in get_user_movie_subscriptions it selects 'title' as 'movie_title'
        for sub in subs:
             name = sub.get('movie_title')
             if not name: continue
             
             if current_lower in name.lower():
                 choices.append(discord.app_commands.Choice(name=name[:100], value=name[:100]))
        
        return choices[:25]

    @commands.hybrid_command(name="movie_info", description="Get detailed information about a movie.")
    @discord.app_commands.describe(movie_name="The name of the movie to get information for")
    @discord.app_commands.autocomplete(movie_name=movie_autocomplete)
    async def movie_info(self, ctx: commands.Context, *, movie_name: str):
        """
        Fetches and displays detailed information about a specific movie from TMDB.
        """
        await ctx.defer(ephemeral=True)

        try:
            search_results = await self.bot.loop.run_in_executor(None, tmdb_client.search_movie, movie_name)
        except TMDBConnectionError:
            await self.send_response(ctx,"Could not connect to TMDB service. Please try again later.", ephemeral=True)
            return
        except TMDBAPIError:
            await self.send_response(ctx,"TMDB service is currently experiencing issues. Please try again later.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Error searching for movie '{movie_name}' in movie_info: {e}")
            await self.send_response(ctx,f"Sorry, there was an unexpected error searching for '{movie_name}'.", ephemeral=True)
            return

        if not search_results:
            await self.send_response(ctx,f"No movies found for '{movie_name}'.", ephemeral=True)
            return

        selected_movie_tmdb_search_data = None

        if len(search_results) == 1:
            selected_movie_tmdb_search_data = search_results[0]
        elif len(search_results) > 1:
            # If exact match found (case insensitive), prioritize it
            exact_matches = [m for m in search_results if m.get('title', '').lower() == movie_name.lower()]
            if len(exact_matches) == 1:
                 selected_movie_tmdb_search_data = exact_matches[0]
            else:
                display_results = search_results[:5]
                embeds_list = []
                message_content = "Multiple movies found. Please react with the number of the movie you want info for:"

                for i, movie_data_item in enumerate(display_results):
                    year_str = movie_data_item.get('release_date')
                    year = year_str[:4] if year_str and len(year_str) >= 4 else 'N/A'
                    
                    movie_embed = discord.Embed(
                        description=f"{NUMBER_EMOJIS[i]} **{movie_data_item['title']} ({year})**",
                        color=discord.Color.blurple()
                    )
                    
                    poster_path = movie_data_item.get('poster_path')
                    if poster_path:
                        poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                        if poster_url:
                            movie_embed.set_thumbnail(url=poster_url)
                    
                    embeds_list.append(movie_embed)

                prompt_msg_obj = await self.send_response(ctx,content=message_content, embeds=embeds_list, ephemeral=False, wait=True)

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
                            await prompt_msg_obj.delete()
                        except discord.HTTPException:
                            pass
                    else:
                        await self.send_response(ctx,"Invalid reaction. Movie info cancelled.", ephemeral=True)
                        try: await prompt_msg_obj.delete()
                        except discord.HTTPException: pass
                        return
                except asyncio.TimeoutError:
                    await self.send_response(ctx,"Selection timed out. Movie info cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
                except Exception as e:
                    logger.error(f"Error during reaction-based movie selection for '{movie_name}' by {ctx.author.id}: {e}")
                    await self.send_response(ctx,"An error occurred during selection. Movie info cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
        
        if not selected_movie_tmdb_search_data or 'id' not in selected_movie_tmdb_search_data:
            await self.send_response(ctx,"Could not determine the movie to fetch details for. Please try again.", ephemeral=True)
            return

        movie_id = selected_movie_tmdb_search_data['id']

        try:
            movie_details = await self.bot.loop.run_in_executor(None, tmdb_client.get_movie_details, movie_id, 'credits,keywords')
        except TMDBConnectionError:
            await self.send_response(ctx,"Could not connect to TMDB to fetch details. Please try again later.", ephemeral=True)
            return
        except TMDBAPIError:
            await self.send_response(ctx,"TMDB service error while fetching details. Please try again later.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Error fetching details for movie ID {movie_id} in movie_info: {e}")
            await self.send_response(ctx,f"Sorry, there was an error fetching details for '{selected_movie_tmdb_search_data.get('title', 'the selected movie')}'.", ephemeral=True)
            return

        if not movie_details:
            await self.send_response(ctx,f"Could not retrieve details for '{selected_movie_tmdb_search_data.get('title', 'the selected movie')}' (ID: {movie_id}).", ephemeral=True)
            return

        embed_title = movie_details.get('title', 'N/A')
        if movie_details.get('release_date'):
            try:
                release_year = datetime.strptime(movie_details['release_date'], '%Y-%m-%d').year
                embed_title += f" ({release_year})"
            except ValueError:
                pass

        embed = discord.Embed(
            title=embed_title,
            description=movie_details.get('overview', 'No overview available.'),
            color=discord.Color.gold(),
            url=f"https://www.themoviedb.org/movie/{movie_id}"
        )

        if movie_details.get('poster_path'):
            embed.set_thumbnail(url=tmdb_client.get_poster_url(movie_details['poster_path'], size="w342"))

        release_date_str = "N/A"
        if movie_details.get('release_date'):
            try:
                release_date_obj = datetime.strptime(movie_details['release_date'], '%Y-%m-%d')
                release_date_str = release_date_obj.strftime('%B %d, %Y')
            except ValueError:
                release_date_str = movie_details['release_date']
        embed.add_field(name="🎬 Release Date", value=release_date_str, inline=True)

        runtime_str = format_runtime(movie_details.get('runtime'))
        embed.add_field(name="⏱️ Runtime", value=runtime_str, inline=True)
        
        rating = movie_details.get('vote_average')
        vote_count = movie_details.get('vote_count')
        rating_str = "N/A"
        if rating is not None:
            rating_str = f"{rating:.1f}/10 ({vote_count:,} votes)"
        embed.add_field(name="⭐ TMDB Rating", value=rating_str, inline=True)

        genres = movie_details.get('genres')
        if genres:
            genre_names = [genre['name'] for genre in genres]
            embed.add_field(name="🎭 Genres", value=", ".join(genre_names) if genre_names else "N/A", inline=False)

        director_str = "N/A"
        if movie_details.get('credits') and movie_details['credits'].get('crew'):
            directors = [person['name'] for person in movie_details['credits']['crew'] if person.get('job') == 'Director']
            if directors:
                director_str = ", ".join(directors)
        embed.add_field(name="🎥 Director(s)", value=director_str, inline=False)

        cast_str = "N/A"
        if movie_details.get('credits') and movie_details['credits'].get('cast'):
            main_cast = [actor['name'] for actor in movie_details['credits']['cast'][:7]]
            if main_cast:
                cast_str = ", ".join(main_cast)
        embed.add_field(name="🌟 Main Cast", value=cast_str, inline=False)
        
        embed.set_footer(text=f"Movie ID: {movie_id} | Data from TMDB")

        try:
            await self.send_response(ctx,embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Error sending movie_info embed for {movie_id}: {e}")
            await self.send_response(ctx,"There was an issue displaying the movie information. The embed might be too large.", ephemeral=True)

    @commands.hybrid_command(name="movie_subscribe", description="Subscribe to movie release notifications.")
    @discord.app_commands.describe(movie_name="The name of the movie to subscribe to")
    @discord.app_commands.autocomplete(movie_name=movie_autocomplete)
    async def movie_subscribe(self, ctx: commands.Context, *, movie_name: str):
        """
        Allows a user to subscribe to notifications for a specific movie's release.
        If multiple movies match the name, you'll be prompted to select the correct one.
        """
        await ctx.defer(ephemeral=True)

        try:
            search_results = await self.bot.loop.run_in_executor(None, tmdb_client.search_movie, movie_name)
        except TMDBConnectionError:
            await self.send_response(ctx,"Could not connect to TMDB. Please check your internet connection.", ephemeral=True)
            return
        except TMDBAPIError:
            await self.send_response(ctx,"TMDB service error. Please try again later.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Error searching for movie '{movie_name}' in movie_subscribe: {e}")
            await self.send_response(ctx,f"Sorry, there was an error searching for '{movie_name}'. Please try again later.", ephemeral=True)
            return

        if not search_results:
            await self.send_response(ctx,f"No movies found for '{movie_name}'.", ephemeral=True)
            return

        selected_movie_data = None

        if len(search_results) == 1:
            selected_movie_data = search_results[0]
        elif len(search_results) > 1:
            exact_matches = [m for m in search_results if m.get('title', '').lower() == movie_name.lower()]
            if len(exact_matches) == 1:
                 selected_movie_data = exact_matches[0]
            else:
                display_results = search_results[:5]
                embeds_list = []
                message_content = "Multiple movies found. Please react with the number of the movie you want to subscribe to:"

                for i, movie_data_item in enumerate(display_results):
                    year_str = movie_data_item.get('release_date')
                    year = year_str[:4] if year_str and len(year_str) >= 4 else 'N/A'
                    
                    movie_embed = discord.Embed(
                        description=f"{NUMBER_EMOJIS[i]} **{movie_data_item['title']} ({year})**",
                        color=discord.Color.green()
                    )
                    
                    poster_path = movie_data_item.get('poster_path')
                    if poster_path:
                        poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                        if poster_url:
                            movie_embed.set_thumbnail(url=poster_url)
                    
                    embeds_list.append(movie_embed)

                prompt_msg_obj = await self.send_response(ctx,content=message_content, embeds=embeds_list, ephemeral=False, wait=True)

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
                        await self.send_response(ctx,"Invalid reaction. Subscription cancelled.", ephemeral=True)
                        try: await prompt_msg_obj.delete()
                        except discord.HTTPException: pass
                        return
                except asyncio.TimeoutError:
                    await self.send_response(ctx,"Selection timed out. Subscription cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
                except Exception as e:
                    logger.error(f"Error during reaction-based movie subscription selection for '{movie_name}' by {ctx.author.id}: {e}")
                    await self.send_response(ctx,"An error occurred during selection. Subscription cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
        
        if not selected_movie_data or 'id' not in selected_movie_data or 'title' not in selected_movie_data or 'release_date' not in selected_movie_data:
            await self.send_response(ctx,"Could not get necessary movie details (ID, title, release date) for subscription. Please try again.", ephemeral=True)
            return

        movie_id = selected_movie_data['id']
        actual_movie_title = selected_movie_data['title']
        release_date = selected_movie_data['release_date']

        if not release_date:
            await self.send_response(ctx,f"Cannot subscribe to '{actual_movie_title}' as its release date is not available.", ephemeral=True)
            return

        poster_path = selected_movie_data.get('poster_path')
        if poster_path is None:
            logger.warning(f"Poster path not found for movie {movie_id} during subscription for user {ctx.author.id}. Using empty string.")
            poster_path = ""

        try:
            success = await self.bot.loop.run_in_executor(None, self.db_manager.add_movie_subscription, ctx.author.id, movie_id, actual_movie_title, poster_path)
            if success:
                await self.send_response(ctx,f"Successfully subscribed to **{actual_movie_title}** (Release: {release_date})!", ephemeral=True)
            else:
                await self.send_response(ctx,f"Could not subscribe to **{actual_movie_title}** due to a database error. Please try again later.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error adding movie subscription for user {ctx.author.id} to movie {movie_id} ('{actual_movie_title}'): {e}")
            await self.send_response(ctx,f"Sorry, there was an error subscribing to '{actual_movie_title}'. Please try again later.", ephemeral=True)

    @commands.hybrid_command(name="movie_unsubscribe", description="Unsubscribe from movie release notifications.")
    @discord.app_commands.describe(movie_name="The name of the movie to unsubscribe from")
    @discord.app_commands.autocomplete(movie_name=movie_subscription_autocomplete)
    async def movie_unsubscribe(self, ctx: commands.Context, *, movie_name: str):
        """
        Allows a user to unsubscribe from notifications for a specific movie.
        """
        await ctx.defer(ephemeral=True)
        user_id = ctx.author.id

        try:
            subscriptions = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_movie_subscriptions, user_id)
        except Exception as e:
            logger.error(f"Error getting movie subscriptions for user {user_id}: {e}")
            await self.send_response(ctx,"Sorry, there was an error fetching your movie subscriptions.", ephemeral=True)
            return

        if not subscriptions:
            await self.send_response(ctx,"You are not subscribed to any movies.", ephemeral=True)
            return

        movie_to_unsubscribe = None
        
        matching_subscriptions = [
            sub for sub in subscriptions if movie_name.lower() in sub['title'].lower()
        ]

        if not matching_subscriptions:
            await self.send_response(ctx,f"No movie matching '{movie_name}' found in your subscriptions. Use `/my_movies` to see them.", ephemeral=True)
            return

        if len(matching_subscriptions) == 1:
            movie_to_unsubscribe = matching_subscriptions[0]
        else:
            display_results = matching_subscriptions[:5]

            embeds_list = []
            message_content = "Multiple subscribed movies match. React with the number to unsubscribe:"

            for i, sub_data_item in enumerate(display_results):
                movie_embed = discord.Embed(
                    description=f"{NUMBER_EMOJIS[i]} **{sub_data_item['title']}**",
                    color=discord.Color.red()
                )
                
                poster_path = sub_data_item.get('poster_path')
                if poster_path:
                    poster_url = tmdb_client.get_poster_url(poster_path, size="w154")
                    if poster_url:
                        movie_embed.set_thumbnail(url=poster_url)

                embeds_list.append(movie_embed)
            
            if not embeds_list:
                 await self.send_response(ctx,"Could not prepare selection list. Please try again.", ephemeral=True)
                 return

            prompt_msg_obj = await self.send_response(ctx,content=message_content, embeds=embeds_list, ephemeral=False, wait=True)

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
                    await self.send_response(ctx,"Invalid selection. Unsubscription cancelled.", ephemeral=True)
                    try: await prompt_msg_obj.delete()
                    except discord.HTTPException: pass
                    return
            except asyncio.TimeoutError:
                await self.send_response(ctx,"Selection timed out. Unsubscription cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return
            except Exception as e:
                logger.error(f"Error during reaction-based movie unsubscription selection for '{movie_name}' by {ctx.author.id}: {e}")
                await self.send_response(ctx,"An error occurred during selection. Unsubscription cancelled.", ephemeral=True)
                try: await prompt_msg_obj.delete()
                except discord.HTTPException: pass
                return

        if not movie_to_unsubscribe:
            await self.send_response(ctx,"Could not identify movie to unsubscribe from. Please try again.", ephemeral=True)
            return

        title_of_movie_unsubscribed = movie_to_unsubscribe['title']
        movie_tmdb_id_to_remove = movie_to_unsubscribe['tmdb_id']

        try:
            success = await self.bot.loop.run_in_executor(None, self.db_manager.remove_movie_subscription, user_id, movie_tmdb_id_to_remove)
            if success:
                await self.send_response(ctx,f"Successfully unsubscribed from **{title_of_movie_unsubscribed}**.", ephemeral=True)
            else:
                await self.send_response(ctx,f"Could not unsubscribe from **{title_of_movie_unsubscribed}** due to a database error. Please try again later.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error removing movie subscription for user {user_id} from movie {movie_tmdb_id_to_remove} ('{title_of_movie_unsubscribed}'): {e}")
            await self.send_response(ctx,f"Sorry, there was an error unsubscribing from '{title_of_movie_unsubscribed}'.", ephemeral=True)

    @commands.hybrid_command(name="my_movies", description="Lists your subscribed movies.")
    async def my_movies(self, ctx: commands.Context):
        """
        Lists all movies you are currently subscribed to for release notifications.
        """
        await ctx.defer(ephemeral=True)
        user_id = ctx.author.id

        try:
            subscriptions = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_movie_subscriptions, user_id)
        except Exception as e:
            logger.error(f"Error getting movie subscriptions for user {user_id} in my_movies: {e}")
            await self.send_response(ctx,"Sorry, there was an error fetching your movie subscriptions.", ephemeral=True)
            return

        if not subscriptions:
            await self.send_response(ctx,"You are not subscribed to any movies. Use `/movie_subscribe` to add some!", ephemeral=True)
            return

        view = MyMoviesPaginatorView(user_id=user_id, items=subscriptions)
        await view.start(ctx, ephemeral=True)

    @tasks.loop(hours=24)
    async def check_movie_releases(self):
        """Checks for movie releases and notifies subscribed users."""
        if not self.db_manager:
            logger.error("MoviesCog: DataManager (db_manager) not available. Cannot check movie releases.")
            return

        logger.info("MoviesCog: check_movie_releases task is running.")
        all_subscriptions_by_user = await self.bot.loop.run_in_executor(None, self.db_manager.get_all_movie_subscriptions)
        if not all_subscriptions_by_user:
            logger.info("MoviesCog: No movie subscriptions found to check.")
            return

        today = date.today()
        logger.info(f"MoviesCog: Today's date for release check: {today}")

        for user_id_str, user_subs_list in all_subscriptions_by_user.items():
            try:
                user_id = int(user_id_str)
                discord_user_obj = await self.bot.fetch_user(user_id)
                if not discord_user_obj:
                    logger.warning(f"MoviesCog: Could not fetch user {user_id}. Skipping their movie notifications.")
                    continue

                dnd_enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, 'dnd_enabled', False)
                dnd_start_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, 'dnd_start_time', "00:00")
                dnd_end_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, 'dnd_end_time', "00:00")
                
                try:
                    dnd_start_time_obj = datetime.strptime(dnd_start_str, '%H:%M').time()
                    dnd_end_time_obj = datetime.strptime(dnd_end_str, '%H:%M').time()
                except ValueError:
                    logger.warning(f"MoviesCog: Invalid DND time format for user {user_id}. Using defaults.")
                    dnd_start_time_obj = time(0,0)
                    dnd_end_time_obj = time(0,0)

                for sub_item_dict in user_subs_list:
                    movie_tmdb_id = sub_item_dict.get('tmdb_id')
                    movie_title_from_sub = sub_item_dict.get('title', 'Unknown Movie')
                    notified_status_bool = bool(sub_item_dict.get('notified_status', 0))

                    if notified_status_bool:
                        continue

                    if not movie_tmdb_id:
                        logger.warning(f"MoviesCog: Subscription item for user {user_id} is missing tmdb_id. Sub: {sub_item_dict}")
                        continue
                        
                    try:
                        fresh_movie_details = await self.bot.loop.run_in_executor(None, tmdb_client.get_movie_details, movie_tmdb_id)
                        if not fresh_movie_details or not fresh_movie_details.get('release_date'):
                            logger.warning(f"MoviesCog: Could not fetch release date for movie '{movie_title_from_sub}' (ID: {movie_tmdb_id}) for user {user_id} from TMDB. Skipping.")
                            continue
                        release_date_str_from_tmdb = fresh_movie_details['release_date']
                        actual_movie_title_to_display = fresh_movie_details.get('title', movie_title_from_sub)
                    except TMDBError as e:
                        logger.error(f"MoviesCog: TMDB error fetching details for movie ID {movie_tmdb_id} during release check: {e}")
                        continue
                    except Exception as tmdb_err:
                        logger.error(f"MoviesCog: Unexpected error fetching TMDB details for movie ID {movie_tmdb_id} during release check: {tmdb_err}")
                        continue
                    
                    try:
                        release_date_obj_from_tmdb = datetime.strptime(release_date_str_from_tmdb, '%Y-%m-%d').date()
                    except ValueError:
                        logger.error(f"MoviesCog: Invalid release date format '{release_date_str_from_tmdb}' from TMDB for movie '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}).")
                        continue
                    
                    if release_date_obj_from_tmdb <= today:
                        logger.info(f"MoviesCog: Movie '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) released on or before {today} for user {user_id}. Preparing notification.")
                        
                        current_time_obj = datetime.now().time()
                        is_dnd_active_now = False
                        if dnd_enabled:
                            if dnd_start_time_obj <= dnd_end_time_obj:
                                if dnd_start_time_obj <= current_time_obj <= dnd_end_time_obj: is_dnd_active_now = True
                            else:
                                if current_time_obj >= dnd_start_time_obj or current_time_obj <= dnd_end_time_obj: is_dnd_active_now = True
                        
                        if is_dnd_active_now:
                            logger.info(f"MoviesCog: DND active for user {user_id}. Skipping notification for '{actual_movie_title_to_display}'.")
                            continue

                        embed_title = f"🎬 Movie Released: {actual_movie_title_to_display}"
                        embed_description = (
                            f"The movie **{actual_movie_title_to_display}** has been released!\n\n"
                            f"**Release Date:** {release_date_obj_from_tmdb.strftime('%B %d, %Y')}\n"
                            f"**Overview:** {fresh_movie_details.get('overview', 'No overview available.')[:500]}"
                        )
                        embed_color = discord.Color.blue()
                        
                        notification_embed = discord.Embed(title=embed_title, description=embed_description, color=embed_color)
                        if fresh_movie_details.get('poster_path'):
                            poster_url = tmdb_client.get_poster_url(fresh_movie_details['poster_path'])
                            if poster_url:
                                notification_embed.set_thumbnail(url=poster_url)
                        
                        notification_embed.set_footer(text=f"Movie ID: {movie_tmdb_id} | Data from TMDB")
                        
                        try:
                            await discord_user_obj.send(embed=notification_embed)
                            logger.info(f"MoviesCog: Sent release notification for '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) to user {user_id}.")
                            
                            update_success = await self.bot.loop.run_in_executor(None, self.db_manager.update_movie_notified_status, user_id, movie_tmdb_id, True)
                            if update_success:
                                logger.info(f"MoviesCog: Updated notified status for '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) for user {user_id}.")
                            else:
                                logger.error(f"MoviesCog: FAILED to update notified status for '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) for user {user_id}.")

                        except discord.Forbidden:
                            logger.warning(f"MoviesCog: Cannot send DM to user {user_id} (Forbidden). They might have DMs disabled or blocked the bot.")
                        except discord.HTTPException as ehttp:
                            logger.error(f"MoviesCog: HTTP error sending DM for movie '{actual_movie_title_to_display}' to user {user_id}: {ehttp}")
                        except Exception as e_send_dm:
                            logger.error(f"MoviesCog: Error sending movie release DM for '{actual_movie_title_to_display}' (ID: {movie_tmdb_id}) to user {user_id}: {e_send_dm}", exc_info=True)
            except Exception as e_user_loop:
                logger.error(f"MoviesCog: Error processing subscriptions for user ID string '{user_id_str}': {e_user_loop}", exc_info=True)

    @check_movie_releases.before_loop
    async def before_check_movie_releases(self):
        print("Task: check_movie_releases waiting for bot to be ready...")
        await self.bot.wait_until_ready()
        print("Task: check_movie_releases bot is ready. Loop starting.")

async def setup(bot: commands.Bot):
    await bot.add_cog(MoviesCog(bot, db_manager=bot.db_manager))
    logger.info("MoviesCog has been loaded.")
