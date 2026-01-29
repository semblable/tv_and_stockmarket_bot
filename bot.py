# Import logging setup first
import logger
logger.setup_logging()

# Import necessary modules
import discord
from discord.ext import commands
from discord import app_commands # Required for @app_commands.describe
import config  # For loading the bot token
import os
from cogs.help import MyCustomHelpCommand # Import the custom help command
import asyncio
import traceback # Added for detailed error logging
from flask import Flask, request, jsonify
from threading import Thread
from data_manager import DataManager # For API endpoints
import random # For placeholder chart data
from typing import Optional
import time
import hmac
import hashlib
from collections import deque
from utils.activity_report import (
    parse_activity_report_text,
    normalize_activity_report_payload,
    get_activity_report_chart_image,
)

# Get logger
log = logger.get_logger(__name__)
log.info("Bot script started. Logging configured via logger.py.")

# --- Webhook security helpers ---
_WEBHOOK_RATE_BUCKETS: dict[str, deque[float]] = {}

def _rate_limit(key: str, limit: int, window_s: int) -> bool:
    """
    Sliding window rate limiter.
    Returns True if request should be allowed, False if rate-limited.
    """
    if limit <= 0 or window_s <= 0:
        return True
    now = time.time()
    bucket = _WEBHOOK_RATE_BUCKETS.get(key)
    if bucket is None:
        bucket = deque()
        _WEBHOOK_RATE_BUCKETS[key] = bucket
    cutoff = now - window_s
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True

def _get_client_ip() -> str:
    # Best-effort client IP extraction (handles common proxy header).
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"

def _enable_dm_for_app_commands(bot: commands.Bot) -> None:
    """
    Ensure application (slash) commands are allowed in DMs/private channels and for user installs.

    Discord's newer command context/installation rules can cause otherwise-correct slash commands
    (including hybrid commands) to not appear in DMs unless contexts/installs are explicitly allowed.
    """
    def _patch(cmd: app_commands.AppCommand) -> None:
        # Allow usage in guilds + DMs + private channels.
        # Apply unconditionally: discord.py may set defaults that still exclude DMs.
        app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)(cmd)

        # Allow both guild installs and user installs.
        # User installs are required for most "use in DMs" setups.
        app_commands.allowed_installs(guilds=True, users=True)(cmd)

    try:
        # IMPORTANT:
        # Some Discord clients will not show a slash *group* (e.g. `/mood`) in DMs unless the
        # group itself is DM-enabled — even if its subcommands are.
        # CommandTree.walk_commands() can miss top-level groups in some discord.py versions,
        # so we patch groups explicitly and then their children.
        top_level = list(bot.tree.get_commands() or [])
        for top in top_level:
            try:
                _patch(top)
            except Exception:
                pass
            try:
                if isinstance(top, app_commands.Group):
                    for child in top.walk_commands():
                        try:
                            _patch(child)
                        except Exception:
                            continue
            except Exception:
                continue

        # Also patch anything else discoverable via tree walking.
        for cmd in bot.tree.walk_commands():
            try:
                _patch(cmd)
                # Log for debugging
                qn = getattr(cmd, "qualified_name", None) or getattr(cmd, "name", "")
                if "mood" in qn.lower():
                    log.info(f"Explicitly DM-enabled mood command: {qn}")
            except Exception:
                continue
    except Exception:
        return

# Check if the token was loaded correctly
if config.DISCORD_BOT_TOKEN is None:
    log.error("CRITICAL: DISCORD_BOT_TOKEN is not set in config.py. The bot cannot start.")
    exit() # Exit if the token is not found
log.info("DISCORD_BOT_TOKEN found in config.")

# Define intents
intents = discord.Intents.default()
intents.message_content = True # Enable message content intent for potential future use

# Create a Bot instance
bot = commands.Bot(command_prefix="!", intents=intents, help_command=MyCustomHelpCommand()) # Using "!" as prefix for traditional commands and custom help

## --- Cog Loading ---
INITIAL_EXTENSIONS = [
    "cogs.tv_shows",
    "cogs.stocks",
    "cogs.utility",
    "cogs.settings",
    "cogs.movies", # Added the new Movies Cog#
    "cogs.gemini", # New Gemini AI Cog
    "cogs.books",  # New Books (author subscriptions)
    "cogs.reading_progress",  # Reading progress tracking (pages/kindle/audiobook)
    "cogs.games",  # Games tracking + Steam/Wiki lookup (no API keys)
    "cogs.productivity",  # To-dos + habits + escalating reminders
    "cogs.reminders",  # One-off + repeating reminders (timezone-aware)
    "cogs.mood",  # Optional mood tracking + daily reminder (opt-in)
    # "cogs.help" # Not loaded as a cog, but assigned directly
]

async def load_extensions():
    for extension in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(extension)
            log.info(f"Successfully loaded extension: {extension}")
        except Exception as e:
            log.error(f"Failed to load extension {extension}:", exc_info=True) # Log with traceback

# --- Global Application Command Error Handler ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """
    Global error handler for slash commands.
    """
    # Log the full error traceback
    command_name = interaction.command.name if interaction.command else "unknown_command"
    log.error(f"Unhandled error in slash command '/{command_name}': {error}", exc_info=False) # exc_info=False because print_exc will show it
    traceback.print_exc()

    error_message = "Sorry, an unexpected error occurred while processing your command. The developers have been notified."

    # Ephemeral messages are not supported in DMs in some clients/APIs.
    effective_ephemeral = interaction.guild is not None

    if interaction.is_response_done():
        try:
            # If the interaction has been responded to or deferred, try sending a followup message.
            await interaction.followup.send(error_message, ephemeral=effective_ephemeral)
        except discord.HTTPException as e:
            log.error(f"Failed to send followup error message for '/{command_name}': {e}")
        except Exception as e:
            log.error(f"An unexpected error occurred while trying to send followup for '/{command_name}': {e}", exc_info=True)
    else:
        try:
            # If the interaction has not been responded to yet, send a new response.
            await interaction.response.send_message(error_message, ephemeral=effective_ephemeral)
        except discord.HTTPException as e:
            log.error(f"Failed to send initial error message for '/{command_name}': {e}")
        except Exception as e:
            log.error(f"An unexpected error occurred while trying to send initial response for '/{command_name}': {e}", exc_info=True)

# --- Flask Web Server for Render Uptime ---
flask_app = Flask(__name__)
# flask_app.logger.critical("!!!!!!!!!! BOT.PY HAS STARTED - LOGGER TEST !!!!!!!!!!") # New test log

@flask_app.route('/')
def home():
    return "Bot is alive and kicking!", 200 # Endpoint for uptime monitor

async def _deliver_webhook_report(
    user_id: int,
    content: str,
    title: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """
    Send a report DM to the user.
    """
    try:
        user = bot.get_user(user_id)
        if not user:
            user = await bot.fetch_user(user_id)
        if not user:
            log.warning(f"Webhook report: user {user_id} not found.")
            return
        content_text = (content or "").strip()
        message = content_text
        if title and isinstance(title, str) and title.strip():
            message = f"**{title.strip()}**\n{message}" if message else f"**{title.strip()}**"

        embed = None
        file = None

        # Try to format activity reports into a nicer embed + chart.
        report_payload = None
        if isinstance(payload, dict):
            report_payload = normalize_activity_report_payload(payload)
        if not report_payload and content_text:
            report_payload = parse_activity_report_text(content_text)

        if report_payload:
            period_label = report_payload.get("period_label") or "Custom range"
            totals = report_payload.get("totals") or {}
            total_words = totals.get("words")
            total_minutes = totals.get("minutes")
            by_lang = report_payload.get("by_language") or []

            embed_title = f"📈 Activity report — {period_label}"
            embed = discord.Embed(title=embed_title, color=discord.Color.blurple())

            if total_words is not None:
                embed.add_field(name="Total words", value=f"**{int(total_words)}**", inline=True)
            if total_minutes is not None:
                minutes_val = float(total_minutes)
                minutes_disp = f"{minutes_val:.0f}m" if minutes_val >= 1 else f"{minutes_val * 60:.0f}s"
                embed.add_field(name="Listening time", value=f"**{minutes_disp}**", inline=True)

            if by_lang:
                lines = []
                labels = []
                words = []
                minutes = []
                for row in by_lang:
                    lang = str(row.get("language") or "Unknown").strip()
                    w = int(row.get("words") or 0)
                    m = row.get("minutes")
                    labels.append(lang)
                    words.append(w)
                    minutes.append(float(m) if m is not None else 0.0)
                    if m is not None and float(m) > 0:
                        lines.append(f"• **{lang}**: {w} words, {float(m):.0f}m")
                    else:
                        lines.append(f"• **{lang}**: {w} words")
                embed.add_field(name="By language", value="\n".join(lines)[:1024], inline=False)

                allow_external = bool(getattr(config, "ALLOW_EXTERNAL_CHARTS", True))
                if allow_external:
                    chart = await bot.loop.run_in_executor(
                        None,
                        get_activity_report_chart_image,
                        "Words & listening time by language",
                        labels,
                        words,
                        minutes,
                    )
                    if chart:
                        file = discord.File(fp=chart, filename="activity_report.png")
                        embed.set_image(url="attachment://activity_report.png")

        allowed_mentions = discord.AllowedMentions.none()
        if embed or file:
            if file:
                await user.send(content=message if message else None, embed=embed, file=file, allowed_mentions=allowed_mentions)
            else:
                await user.send(content=message if message else None, embed=embed, allowed_mentions=allowed_mentions)
        else:
            await user.send(message, allowed_mentions=allowed_mentions)
    except discord.Forbidden:
        log.warning(f"Webhook report: cannot DM user {user_id} (Forbidden).")
    except Exception as e:
        log.error(f"Webhook report: failed to send DM to {user_id}: {e}", exc_info=True)

@flask_app.route("/webhook/report/<token>", methods=["POST"])
def webhook_report(token: str):
    """
    Receives a report payload and forwards it to the user who owns the token.
    """
    if not token:
        return jsonify({"ok": False, "error": "missing_token"}), 400
    if not getattr(bot, "db_manager", None):
        return jsonify({"ok": False, "error": "db_not_ready"}), 503

    # Basic request size guard (default 50 KB, configurable)
    max_bytes = int(getattr(config, "WEBHOOK_MAX_BYTES", 50 * 1024) or 50 * 1024)
    if request.content_length is not None and request.content_length > max_bytes:
        return jsonify({"ok": False, "error": "payload_too_large"}), 413

    # Optional signature verification (HMAC-SHA256)
    shared_secret = str(getattr(config, "WEBHOOK_SHARED_SECRET", "") or "").encode("utf-8")
    if shared_secret:
        signature = request.headers.get("X-Webhook-Signature", "")
        raw_body = request.get_data(cache=True) or b""
        expected = hmac.new(shared_secret, raw_body, hashlib.sha256).hexdigest()
        expected_header = f"sha256={expected}"
        if not hmac.compare_digest(signature, expected_header):
            return jsonify({"ok": False, "error": "invalid_signature"}), 401

    # Rate limit per token + IP (default 30 req/min)
    rl_limit = int(getattr(config, "WEBHOOK_RATE_LIMIT_PER_MIN", 30) or 30)
    rl_key = f"{token}:{_get_client_ip()}"
    if not _rate_limit(rl_key, rl_limit, 60):
        return jsonify({"ok": False, "error": "rate_limited"}), 429

    try:
        user_id = bot.db_manager.get_user_id_for_preference_value("report_webhook_token", token)
    except Exception as e:
        log.error(f"Webhook report: token lookup failed: {e}", exc_info=True)
        return jsonify({"ok": False, "error": "lookup_failed"}), 500

    if not user_id:
        return jsonify({"ok": False, "error": "invalid_token"}), 404

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}

    content = payload.get("content") or payload.get("message") or ""
    title = payload.get("title")
    if not isinstance(content, str) or not content.strip():
        return jsonify({"ok": False, "error": "missing_content"}), 400

    try:
        asyncio.run_coroutine_threadsafe(
            _deliver_webhook_report(int(user_id), content.strip(), title, payload),
            bot.loop,
        )
    except Exception as e:
        log.error(f"Webhook report: failed to schedule send: {e}", exc_info=True)
        return jsonify({"ok": False, "error": "dispatch_failed"}), 503

    return jsonify({"ok": True}), 202
# --- Initialize DataManager ---
# This should be done once, and the instance can be shared.
try:
    db_manager = DataManager()
    bot.db_manager = db_manager # Assign DataManager instance to the bot object (temporary, for cogs that still use it)
    log.info("DataManager initialized successfully.")
except Exception as e:
    log.critical(f"CRITICAL: Failed to initialize DataManager: {e}", exc_info=True)
    bot.db_manager = None # Ensure it's None if initialization fails

def run_flask():
    # Use '0.0.0.0' to be accessible externally.
    # Render typically sets the PORT environment variable.
    port = int(os.environ.get("PORT", 5000)) # Default to 5000 if PORT not set
    print(f"Starting Flask server on port {port}...")
    flask_app.run(host='0.0.0.0', port=port)

# --- Bot Events ---
@bot.event
async def on_ready():
    """
    Called when the bot is successfully logged in and ready.
    """
    log.info(f"Bot is ready and logged in as {bot.user}")
    log.info(f"Bot is in {len(bot.guilds)} guild(s)")
    
    # Log bot's current permissions to check for applications.commands scope
    try:
        app_info = await bot.application_info()
        log.info(f"Bot application ID: {app_info.id}")
        log.info(f"Bot owner: {app_info.owner}")
        
        # Check bot permissions in each guild
        for guild in bot.guilds:
            log.info(f"Guild: {guild.name} (ID: {guild.id}) - Members: {guild.member_count}")
            bot_member = guild.get_member(bot.user.id)
            if bot_member:
                perms = bot_member.guild_permissions
                log.info(f"  Bot permissions in {guild.name}: Administrator: {perms.administrator}, Manage Guild: {perms.manage_guild}")
            else:
                log.warning(f"  Bot member not found in {guild.name}")
    except Exception as e:
        log.error(f"Error checking bot application info: {e}")

    # Sync commands
    commands_synced = False
    try:
        # Make sure commands are DM-capable before syncing them to Discord.
        _enable_dm_for_app_commands(bot)

        # Guild sync is the only way to make new slash commands appear immediately.
        # The old code synced only to bot.guilds[0], which is easy to miss if you test in another server.
        guilds = list(bot.guilds or [])
        if not guilds:
            log.warning("Bot is not in any guilds - cannot do guild-specific sync")
        else:
            # Safety cap to avoid hitting rate limits if the bot is in many guilds.
            max_guild_sync = int(os.environ.get("MAX_GUILD_SYNC", "10"))
            for g in guilds[: max(0, max_guild_sync)]:
                log.info(f"Copying global commands to guild: {g.name} (ID: {g.id}) for immediate availability...")
                bot.tree.copy_global_to(guild=discord.Object(id=g.id))

                log.info(f"Attempting to sync application commands to guild: {g.name} (ID: {g.id})...")
                try:
                    synced_guild = await bot.tree.sync(guild=discord.Object(id=g.id))
                    log.info(f"✅ Successfully synced {len(synced_guild)} command(s) to guild {g.name}")
                    commands_synced = True
                except discord.Forbidden as e:
                    log.error(f"❌ Forbidden error syncing to guild {g.name}: {e}")
                    log.error("This usually means the bot lacks 'applications.commands' scope or manage guild permissions")
                except discord.HTTPException as e:
                    log.error(f"❌ HTTP error syncing to guild {g.name}: {e}")
                except Exception as e:
                    log.error(f"❌ Unexpected error syncing to guild {g.name}: {e}")
        
        # Also sync globally (takes up to 1 hour to propagate)
        log.info("Attempting to sync application commands globally...")
        try:
            synced = await bot.tree.sync()
            log.info(f"✅ Successfully synced {len(synced)} command(s) globally")
            if not commands_synced:
                commands_synced = True
        except discord.Forbidden as e:
            log.error(f"❌ Forbidden error syncing globally: {e}")
            log.error("This usually means the bot application lacks proper scopes")
        except discord.HTTPException as e:
            log.error(f"❌ HTTP error syncing globally: {e}")
        except Exception as e:
            log.error(f"❌ Unexpected error syncing globally: {e}")
            
    except Exception as e:
        log.error("❌ Critical error during command sync:", exc_info=True)
    
    if not commands_synced:
        log.error("🚨 CRITICAL: No commands were synced! Slash commands will not work!")
        log.error("🔧 SOLUTION: Ensure bot has 'bot' AND 'applications.commands' scopes when added to server")
        log.error("🔧 REINVITE: Use this URL pattern: https://discord.com/api/oauth2/authorize?client_id=YOUR_BOT_ID&permissions=8&scope=bot%20applications.commands")
    else:
        log.info("🎉 Command sync completed successfully!")

# --- Basic Slash Command (Example) ---
@bot.hybrid_command(name="ping", description="Checks bot latency and responds with Pong!")
@app_commands.describe(
    ephemeral_response = "Whether the bot's response should only be visible to you (default: False)."
)
async def ping(ctx: commands.Context, ephemeral_response: bool = False):
    """
    Responds with 'Pong!' and the bot's latency.
    """
    latency_ms = round(bot.latency * 1000)
    await ctx.send(f"Pong! Latency: {latency_ms}ms", ephemeral=ephemeral_response)

# --- Manual Command Sync (for fast iteration / new guilds) ---
@bot.command(name="sync")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def sync_prefix(ctx: commands.Context):
    """
    Force-sync application (slash) commands to the current guild.
    This works as a PREFIX command: !sync
    """
    try:
        _enable_dm_for_app_commands(bot)
        guild_id = ctx.guild.id
        bot.tree.copy_global_to(guild=discord.Object(id=guild_id))
        synced = await bot.tree.sync(guild=discord.Object(id=guild_id))
        await ctx.send(f"✅ Synced {len(synced)} command(s) to this server.")
        log.info(f"Manual sync: synced {len(synced)} commands to guild {guild_id}")
    except discord.Forbidden as e:
        log.error(f"Manual sync forbidden in guild {ctx.guild.id}: {e}")
        await ctx.send("❌ I don't have permission to sync commands here. Make sure I was invited with `applications.commands` and I have permission to manage the server.")
    except discord.HTTPException as e:
        log.error(f"Manual sync HTTP error in guild {ctx.guild.id}: {e}")
        await ctx.send(f"❌ Sync failed due to a Discord API error: {e}")
    except Exception as e:
        log.error("Manual sync unexpected error:", exc_info=True)
        await ctx.send(f"❌ Sync failed: {e}")


@sync_prefix.error
async def sync_prefix_error(ctx: commands.Context, error: Exception):
    # Avoid noisy tracebacks when someone tries to run this in DMs.
    if isinstance(error, commands.NoPrivateMessage):
        try:
            await ctx.send("❌ This command can only be used in a server (not in DMs).")
        except Exception:
            pass
        return
    # Let other errors fall back to the default handler/logging.
    raise error

# --- Main Execution ---
async def main():
    log.info("Async main() function started.")
    # Start Flask app in a new thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    log.info("Flask thread started.")

    # Load cogs
    log.info("Attempting to load extensions (cogs)...")
    await load_extensions()
    log.info("Finished attempting to load extensions.")
    
    # Start the bot
    log.info("Starting Discord bot...")
    if config.DISCORD_BOT_TOKEN:
        await bot.start(config.DISCORD_BOT_TOKEN)
    else:
        log.critical("Bot token not found at the point of starting the bot.")

if __name__ == "__main__":
    log.info("Starting bot execution from __main__.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot shutting down due to KeyboardInterrupt.")
    except Exception as e:
        log.error("An unexpected error occurred in __main__:", exc_info=True)
    finally:
        log.info("Bot has shut down.")
