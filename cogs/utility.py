import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from typing import Optional, List
import datetime
import aiohttp

from api_clients.openweathermap_client import get_weather_data, ICON_TO_EMOJI
from config import OPENWEATHERMAP_API_KEY, TMDB_API_KEY # To check if they're configured
from api_clients.tmdb_client import get_upcoming_movies, get_tv_on_the_air, get_poster_url

logger = logging.getLogger(__name__)

class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self):
        """Initialize the AIOHTTP session when the cog is loaded."""
        self._session = aiohttp.ClientSession()
        self.check_weather_notifications.start()
        logger.info("Utility Cog loaded, AIOHTTP session created, and weather task started.")

    async def cog_unload(self):
        """Close the AIOHTTP session when the cog is unloaded."""
        self.check_weather_notifications.cancel()
        if self._session:
            await self._session.close()
            logger.info("Utility Cog unloaded and AIOHTTP session closed.")

    @property
    def session(self) -> aiohttp.ClientSession:
        """Getter for the AIOHTTP session, ensuring it's initialized."""
        if self._session is None or self._session.closed:
            # This case should ideally be handled by cog_load,
            # but as a fallback, we can re-create it.
            # However, for simplicity in a cog, ensure cog_load is called.
            logger.warning("AIOHTTP session was not initialized or was closed. Re-creating.")
            self._session = aiohttp.ClientSession()
        return self._session

    def get_temperature_color(self, temp_celsius: Optional[float]) -> discord.Color:
        """Returns a Discord color based on temperature."""
        if temp_celsius is None:
            return discord.Color.light_grey()
        if temp_celsius <= 0:
            return discord.Color.blue()
        elif temp_celsius <= 10:
            return discord.Color.teal()
        elif temp_celsius <= 20:
            return discord.Color.green()
        elif temp_celsius <= 25:
            return discord.Color.gold()
        elif temp_celsius <= 30:
            return discord.Color.orange()
        else:
            return discord.Color.red()

    def format_timestamp(self, timestamp: Optional[int], timezone_offset: Optional[int]) -> str:
        """Formats a UTC timestamp into a human-readable local time string."""
        if timestamp is None or timezone_offset is None:
            return "N/A"
        try:
            utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
            local_dt = utc_dt + datetime.timedelta(seconds=timezone_offset)
            return local_dt.strftime('%I:%M %p %Z') # e.g., 05:30 PM EST
        except Exception as e:
            logger.error(f"Error formatting timestamp {timestamp} with offset {timezone_offset}: {e}")
            # Fallback to UTC if conversion fails
            if timestamp:
                return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%I:%M %p UTC')
            return "N/A"

    @app_commands.command(name="weather", description="Fetches current weather and a forecast.")
    @app_commands.describe(
        location="The city name or zip code. Defaults to your saved location if set.",
        hours="Forecast duration in hours (default 9, max 120)."
    )
    async def weather(self, interaction: discord.Interaction, location: Optional[str] = None, hours: int = 9):
        """
        Displays the current weather and a forecast for the specified location.
        """
        await interaction.response.defer(thinking=True)

        if not OPENWEATHERMAP_API_KEY:
            await interaction.followup.send("Sorry, the weather service is not configured by the bot owner. (Missing API Key)")
            return

        user_id = interaction.user.id
        if not location:
            location = await self.bot.loop.run_in_executor(None, self.bot.db_manager.get_user_preference, user_id, "weather_default_location")
            if not location:
                await interaction.followup.send("Please provide a location or set a default one using the settings command.")
                return

        if not self.session: # Should be initialized by cog_load
            logger.error("AIOHTTP session not available in weather command.")
            await interaction.followup.send("An unexpected error occurred with the bot's internal setup. Please try again later.")
            return

        # Calculate forecast_limit based on hours (3-hour intervals)
        # Ensure at least 1 item if hours > 0, max 40 (5 days)
        forecast_limit = max(1, min(40, (hours + 2) // 3)) # Round up roughly

        weather_info = await get_weather_data(location, self.session, forecast_limit=forecast_limit)

        if not weather_info:
            await interaction.followup.send("Sorry, I couldn't fetch weather data at this time. Please try again later.")
            return

        if "error" in weather_info:
            error_message = weather_info["error"]
            cod = weather_info.get("cod")
            if cod == 404:
                await interaction.followup.send(f"Sorry, I couldn't find the location: `{location}`. Please check the spelling or try a different format (e.g., 'City,CountryCode').")
            elif cod == 401:
                 await interaction.followup.send("Sorry, there's an issue with the weather service API key. The bot owner has been notified (implicitly).")
            else:
                await interaction.followup.send(f"An error occurred: {error_message}")
            return

        current = weather_info.get("current")
        forecast_items = weather_info.get("forecast", [])

        if not current:
            await interaction.followup.send("Sorry, I received incomplete weather data. Please try again.")
            return

        loc_name = current.get('location_name', 'N/A')
        country = current.get('country', '')
        title_location = f"{loc_name}, {country}" if country else loc_name

        embed_color = self.get_temperature_color(current.get("temp"))
        embed = discord.Embed(
            title=f"{current.get('emoji', '❓')} Weather in {title_location}",
            color=embed_color,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        temp_c = current.get('temp')
        feels_like_c = current.get('feels_like')
        temp_f = (temp_c * 9/5) + 32 if temp_c is not None else None
        feels_like_f = (feels_like_c * 9/5) + 32 if feels_like_c is not None else None

        temp_str = f"{temp_c}°C" if temp_c is not None else "N/A"
        if temp_f is not None:
            temp_str += f" / {temp_f:.1f}°F"

        feels_like_str = f"{feels_like_c}°C" if feels_like_c is not None else "N/A"
        if feels_like_f is not None:
            feels_like_str += f" / {feels_like_f:.1f}°F"

        embed.add_field(name="🌡️ Temperature", value=temp_str, inline=True)
        embed.add_field(name="🤔 Feels Like", value=feels_like_str, inline=True)
        embed.add_field(name="📝 Condition", value=f"{current.get('condition', 'N/A')} ({current.get('description', 'N/A')})", inline=False)

        humidity = current.get('humidity')
        embed.add_field(name="💧 Humidity", value=f"{humidity}%" if humidity is not None else "N/A", inline=True)

        wind_speed_ms = current.get('wind_speed')
        wind_speed_kmh = (wind_speed_ms * 3.6) if wind_speed_ms is not None else None
        wind_speed_mph = (wind_speed_ms * 2.237) if wind_speed_ms is not None else None

        wind_str = f"{wind_speed_ms} m/s" if wind_speed_ms is not None else "N/A"
        if wind_speed_kmh is not None:
            wind_str += f" ({wind_speed_kmh:.1f} km/h)"
        ## if wind_speed_mph is not None: # Optional: add mph
        #     wind_str += f" / {wind_speed_mph:.1f} mph"
        embed.add_field(name="💨 Wind", value=wind_str, inline=True)

        pressure = current.get('pressure')
        embed.add_field(name="📊 Pressure", value=f"{pressure} hPa" if pressure is not None else "N/A", inline=True)


        sunrise_ts = current.get('sunrise')
        sunset_ts = current.get('sunset')
        tz_offset = current.get('timezone')

        embed.add_field(name="🌅 Sunrise", value=self.format_timestamp(sunrise_ts, tz_offset), inline=True)
        embed.add_field(name="🌇 Sunset", value=self.format_timestamp(sunset_ts, tz_offset), inline=True)

        if forecast_items:
            forecast_str = ""
            for item in forecast_items:
                dt_object = datetime.datetime.fromtimestamp(item['dt'], tz=datetime.timezone.utc)
                if tz_offset is not None:
                    local_dt = dt_object + datetime.timedelta(seconds=tz_offset)
                    time_str = local_dt.strftime('%a %I:%M %p') # Added Day of week
                else:
                    time_str = dt_object.strftime('%a %I:%M %p UTC')

                item_temp_c = item.get('temp')
                item_temp_str = f"{item_temp_c}°C" if item_temp_c is not None else "N/A"
                forecast_str += f"**{time_str}**: {item_temp_str}, {item.get('condition', 'N/A')} {item.get('emoji', '')}\n"
            
            # Truncate if too long (Discord field limit is 1024)
            if len(forecast_str) > 1024:
                forecast_str = forecast_str[:1000] + "\n... (truncated)"

            embed.add_field(name=f"🌦️ Forecast (next ~{hours} hours)", value=forecast_str.strip(), inline=False)
        else:
             # Add today's high/low if full forecast isn't available or not processed
            temp_min_c = current.get('temp_min')
            temp_max_c = current.get('temp_max')
            if temp_min_c is not None and temp_max_c is not None:
                temp_min_f = (temp_min_c * 9/5) + 32
                temp_max_f = (temp_max_c * 9/5) + 32
                today_high_low = f"Low: {temp_min_c}°C / {temp_min_f:.1f}°F\nHigh: {temp_max_c}°C / {temp_max_f:.1f}°F"
                embed.add_field(name="📈 Today's High/Low", value=today_high_low, inline=False)


        embed.set_footer(text=f"Weather data provided by OpenWeatherMap | Queried for: {location}")
        await interaction.followup.send(embed=embed)

    @tasks.loop(minutes=1)
    async def check_weather_notifications(self):
        """Checks for scheduled weather notifications."""
        now = datetime.datetime.now(datetime.timezone.utc)
        current_time = now.strftime("%H:%M") # UTC time
        
        schedules = await self.bot.loop.run_in_executor(None, self.bot.db_manager.get_weather_schedules_for_time, current_time)
        
        if not schedules:
            return

        logger.info(f"Sending weather notifications for {current_time} UTC to {len(schedules)} users.")

        for schedule in schedules:
            user_id = int(schedule['user_id'])
            location = schedule['location']

            # Respect DND (best-effort) for consistency with other reminder systems.
            try:
                dnd_enabled = await self.bot.loop.run_in_executor(
                    None, self.bot.db_manager.get_user_preference, user_id, "dnd_enabled", False
                )
                if dnd_enabled:
                    dnd_start_str = await self.bot.loop.run_in_executor(
                        None, self.bot.db_manager.get_user_preference, user_id, "dnd_start_time", "00:00"
                    )
                    dnd_end_str = await self.bot.loop.run_in_executor(
                        None, self.bot.db_manager.get_user_preference, user_id, "dnd_end_time", "00:00"
                    )
                    try:
                        start_t = datetime.datetime.strptime(str(dnd_start_str), "%H:%M").time()
                        end_t = datetime.datetime.strptime(str(dnd_end_str), "%H:%M").time()
                        now_t = datetime.datetime.now().time()
                        in_dnd = False
                        # Half-open interval [start, end)
                        if start_t != end_t:
                            if start_t < end_t:
                                in_dnd = start_t <= now_t < end_t
                            else:
                                in_dnd = now_t >= start_t or now_t < end_t
                        if in_dnd:
                            continue
                    except Exception:
                        # If parsing fails, don't suppress.
                        pass
            except Exception:
                pass
            
            # If location is not in schedule, check default preference
            if not location:
                location = await self.bot.loop.run_in_executor(None, self.bot.db_manager.get_user_preference, user_id, "weather_default_location")
            
            if not location:
                # logger.warning(f"User {user_id} has scheduled weather at {current_time} but no location set.")
                continue
            
            # Use the bot's fetch_user/get_user
            user = self.bot.get_user(user_id)
            if not user:
                try:
                    user = await self.bot.fetch_user(user_id)
                except discord.NotFound:
                    logger.warning(f"User {user_id} not found for weather notification.")
                    continue
                except Exception as e:
                    logger.error(f"Error fetching user {user_id}: {e}")
                    continue

            if not self.session:
                 continue

            weather_info = await get_weather_data(location, self.session)
            if not weather_info or "error" in weather_info:
                logger.error(f"Failed to fetch weather for user {user_id} at {location}")
                continue
            
            current = weather_info.get("current")
            if not current: continue

            temp_c = current.get("temp")
            condition = current.get("condition")
            emoji = current.get("emoji", "")
            
            embed = discord.Embed(
                title=f"{emoji} Daily Weather: {current.get('location_name')}",
                description=f"It's **{temp_c}°C** and **{condition}**.",
                color=self.get_temperature_color(temp_c),
                timestamp=now
            )
            embed.add_field(name="Feels Like", value=f"{current.get('feels_like')}°C")
            embed.add_field(name="Humidity", value=f"{current.get('humidity')}%")
            embed.add_field(name="Wind", value=f"{current.get('wind_speed')} m/s")
            embed.set_footer(text="You can manage this schedule in settings.")

            try:
                await user.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Could not send DM to user {user_id} (Forbidden).")
            except Exception as e:
                logger.error(f"Error sending weather DM to {user_id}: {e}")

    @check_weather_notifications.before_loop
    async def before_check_weather_notifications(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="poll", description="Creates a simple poll with emoji reactions.")
    @app_commands.describe(
        question="The question for the poll.",
        option1="The first poll option.",
        option2="The second poll option.",
        option3="The third poll option (optional).",
        option4="The fourth poll option (optional).",
        option5="The fifth poll option (optional).",
        option6="The sixth poll option (optional).",
        option7="The seventh poll option (optional).",
        option8="The eighth poll option (optional).",
        option9="The ninth poll option (optional).",
        option10="The tenth poll option (optional)."
    )
    async def poll(self, ctx: commands.Context,
                   question: str,
                   option1: str,
                   option2: str,
                   option3: Optional[str] = None,
                   option4: Optional[str] = None,
                   option5: Optional[str] = None,
                   option6: Optional[str] = None,
                   option7: Optional[str] = None,
                   option8: Optional[str] = None,
                   option9: Optional[str] = None,
                   option10: Optional[str] = None):
        """
        Creates a simple poll with up to 10 options.
        Example: !poll "What's your favorite color?" "Red" "Blue" "Green"
        """
        if ctx.interaction: # For slash commands, defer thinking
            await ctx.interaction.response.defer(thinking=True)

        options = [opt for opt in [option1, option2, option3, option4, option5, option6, option7, option8, option9, option10] if opt is not None]

        # Input validation for number of options is implicitly handled by the command signature
        # (option1 and option2 are required). Max options are limited by parameters.

        numbered_emojis = [
            "\u0031\uFE0F\u20E3",  # 1️⃣
            "\u0032\uFE0F\u20E3",  # 2️⃣
            "\u0033\uFE0F\u20E3",  # 3️⃣
            "\u0034\uFE0F\u20E3",  # 4️⃣
            "\u0035\uFE0F\u20E3",  # 5️⃣
            "\u0036\uFE0F\u20E3",  # 6️⃣
            "\u0037\uFE0F\u20E3",  # 7️⃣
            "\u0038\uFE0F\u20E3",  # 8️⃣
            "\u0039\uFE0F\u20E3",  # 9️⃣
            "\U0001F51F"         # 🔟
        ]

        embed = discord.Embed(
            title=f"📊 Poll by {ctx.author.display_name}",
            description=f"**{question}**\n\nReact with the corresponding emoji to vote!",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        options_text_display = []
        for i, option_text_item in enumerate(options):
            if i < len(numbered_emojis):
                options_text_display.append(f"{numbered_emojis[i]} {option_text_item}")
            else:
                options_text_display.append(f"{i+1}. {option_text_item}") # Fallback, should not be hit

        embed.add_field(name="Options", value="\n".join(options_text_display), inline=False)
        embed.set_footer(text="Vote by reacting below.")

        poll_message_to_react = None
        if ctx.interaction:
            # followup.send returns an InteractionMessage which can be used for reactions
            await ctx.interaction.followup.send(embed=embed)
            poll_message_to_react = await ctx.original_response() # Get the message object
        else:
            poll_message_to_react = await ctx.send(embed=embed)

        if poll_message_to_react:
            for i in range(len(options)):
                if i < len(numbered_emojis):
                    try:
                        await poll_message_to_react.add_reaction(numbered_emojis[i])
                    except discord.HTTPException as e:
                        logger.error(f"Failed to add reaction {numbered_emojis[i]} to poll: {e}")
                else:
                    break
        else:
            logger.error("Could not obtain poll message to add reactions.")
            # Optionally inform user if message sending failed critically earlier
            if ctx.interaction:
                await ctx.interaction.followup.send("Error: Could not create poll message for reactions.", ephemeral=True)
            else:
                await ctx.send("Error: Could not create poll message for reactions.")

    @commands.hybrid_command(name="upcoming_releases", description="Shows upcoming movies and TV shows.")
    @app_commands.describe(category="Filter by 'movies', 'tv', or 'all' (default).")
    async def upcoming_releases(self, ctx: commands.Context, category: str = "all"):
        """
        Displays upcoming movie releases and TV shows currently on the air.
        Category can be 'movies', 'tv', or 'all'.
        """
        await ctx.defer(thinking=True)

        if not TMDB_API_KEY:
            await ctx.send("Sorry, the TMDB API key is not configured. Please contact the bot owner.")
            return

        category = category.lower()
        if category not in ["movies", "tv", "all"]:
            await ctx.send("Invalid category. Please use 'movies', 'tv', or 'all'.")
            return

        embeds_to_send = []
        max_items_per_category = 7 # Max items to show for movies/TV

        # --- Fetch Movies ---
        if category in ["movies", "all"]:
            movies_data = await self.bot.loop.run_in_executor(None, get_upcoming_movies)
            if movies_data:
                embed_movies = discord.Embed(
                    title="🎬 Upcoming Movie Releases",
                    color=discord.Color.blue(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                description_movies = []
                # Filter out movies with release dates in the past (TMDB might sometimes return them)
                # And ensure release_date is present
                today_date = datetime.date.today()
                valid_movies = []
                for m in movies_data:
                    if m.get('release_date'):
                        try:
                            release_date_obj = datetime.datetime.strptime(m['release_date'], '%Y-%m-%d').date()
                            if release_date_obj >= today_date:
                                valid_movies.append(m)
                        except ValueError:
                            # Optionally, log the error or the movie that was skipped
                            # print(f"Skipping movie due to invalid date format: {m.get('title', 'N/A')} - {m['release_date']}")
                            pass # Movie with invalid date format is skipped
                # Sort by release date (already done in client, but good to ensure)
                valid_movies.sort(key=lambda x: x['release_date'])


                if not valid_movies:
                    embed_movies.description = "No upcoming movies found or API error."
                else:
                    if valid_movies[0].get('poster_path'):
                        embed_movies.set_thumbnail(url=get_poster_url(valid_movies[0]['poster_path'], size="w185"))

                    for i, movie in enumerate(valid_movies[:max_items_per_category]):
                        title = movie.get('title', 'N/A')
                        tmdb_id = movie.get('id')
                        release_date_str = movie.get('release_date', 'N/A')
                        overview = movie.get('overview', 'No overview available.')
                        rating = movie.get('vote_average', 0)
                        
                        # Format release date
                        try:
                            release_dt = datetime.datetime.strptime(release_date_str, '%Y-%m-%d')
                            formatted_date = release_dt.strftime('%B %d, %Y')
                        except ValueError:
                            formatted_date = release_date_str

                        entry = f"**[{title}](https://www.themoviedb.org/movie/{tmdb_id})**\n"
                        entry += f"🗓️ Release: {formatted_date}\n"
                        if rating > 0:
                            entry += f"⭐ Rating: {rating}/10\n"
                        entry += f"```{overview[:150]}{'...' if len(overview) > 150 else ''}```\n"
                        description_movies.append(entry)
                    
                    embed_movies.description = "\n".join(description_movies) if description_movies else "No upcoming movies found."
                embeds_to_send.append(embed_movies)
            
            elif category == "movies": # Only movies were requested and none found
                 embed_movies_error = discord.Embed(title="🎬 Upcoming Movie Releases", description="Could not fetch upcoming movies at this time or none were found.", color=discord.Color.red())
                 embeds_to_send.append(embed_movies_error)


        # --- Fetch TV Shows ---
        if category in ["tv", "all"]:
            tv_data = await self.bot.loop.run_in_executor(None, get_tv_on_the_air)
            if tv_data:
                embed_tv = discord.Embed(
                    title="📺 TV Shows On The Air",
                    color=discord.Color.green(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                description_tv = []
                # Filter out shows with first_air_date in the past if "on the air" means new episodes for existing shows
                # For this endpoint, 'on_the_air' implies recent/current airing, so sorting by first_air_date is a proxy.
                # We might want to check 'next_episode_to_air' if we fetched full details, but for a list, this is simpler.
                today_date = datetime.date.today()
                # The API /tv/on_the_air should ideally give current shows.
                # We sort by first_air_date as a general proxy for recency or premiere.
                valid_tv_shows = [
                    s for s in tv_data
                    if s.get('first_air_date') # Ensure date exists for sorting
                ]
                # Sort by first_air_date (already done in client, but good to ensure)
                valid_tv_shows.sort(key=lambda x: x['first_air_date'])


                if not valid_tv_shows:
                    embed_tv.description = "No TV shows currently on the air found or API error."
                else:
                    if valid_tv_shows[0].get('poster_path'):
                         embed_tv.set_thumbnail(url=get_poster_url(valid_tv_shows[0]['poster_path'], size="w185"))
                    
                    for i, show in enumerate(valid_tv_shows[:max_items_per_category]):
                        name = show.get('name', 'N/A')
                        tmdb_id = show.get('id')
                        first_air_date_str = show.get('first_air_date', 'N/A')
                        overview = show.get('overview', 'No overview available.')
                        rating = show.get('vote_average', 0)

                        try:
                            air_date_dt = datetime.datetime.strptime(first_air_date_str, '%Y-%m-%d')
                            formatted_date = air_date_dt.strftime('%B %d, %Y')
                        except ValueError:
                            formatted_date = first_air_date_str
                        
                        entry = f"**[{name}](https://www.themoviedb.org/tv/{tmdb_id})**\n"
                        entry += f"🗓️ First Aired: {formatted_date}\n" # Or "Next Air Date" if available
                        if rating > 0:
                            entry += f"⭐ Rating: {rating}/10\n"
                        entry += f"```{overview[:150]}{'...' if len(overview) > 150 else ''}```\n"
                        description_tv.append(entry)

                    embed_tv.description = "\n".join(description_tv) if description_tv else "No TV shows on the air found."
                embeds_to_send.append(embed_tv)

            elif category == "tv": # Only TV was requested and none found
                embed_tv_error = discord.Embed(title="📺 TV Shows On The Air", description="Could not fetch TV shows on the air at this time or none were found.", color=discord.Color.red())
                embeds_to_send.append(embed_tv_error)

        if not embeds_to_send:
            # This case might happen if category was 'all' but both API calls failed or returned no data
            # and we didn't create error embeds for 'all' specifically above.
            await ctx.send("Could not find any upcoming movies or TV shows on the air at this moment.")
            return

        # Send the embeds
        # For slash commands, followup.send can only be used once after defer.
        # If we have multiple embeds, we need to send them in a way that works for both hybrid command types.
        if ctx.interaction:
            # For slash commands, send the first embed with followup, then subsequent with send.
            # However, followup.send can take a list of embeds.
            await ctx.interaction.followup.send(embeds=embeds_to_send)
        else:
            # For prefix commands, send each embed.
            for embed in embeds_to_send:
                await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
    logger.info("Utility cog has been loaded.")