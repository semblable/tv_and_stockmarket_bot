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
from flask import Flask, request
from werkzeug.utils import secure_filename
from threading import Thread
from data_manager import DataManager # For API endpoints
import random # For placeholder chart data
import json
import textwrap
import datetime
import csv
from typing import Any, Dict, List

# Get logger
log = logger.get_logger(__name__)
log.info("Bot script started. Logging configured via logger.py.")

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
    "cogs.xiaomi_notify",  # Notify for Xiaomi ingestion (sleep/health -> webhook -> DM)
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
# Prevent accidental huge uploads from exhausting memory.
# Automate CSV exports are usually small, but this keeps the endpoint safer.
try:
    _max_mb = int(os.environ.get("XIAOMI_MAX_UPLOAD_MB", "25"))
except Exception:
    _max_mb = 25
flask_app.config["MAX_CONTENT_LENGTH"] = max(1, _max_mb) * 1024 * 1024
# flask_app.logger.critical("!!!!!!!!!! BOT.PY HAS STARTED - LOGGER TEST !!!!!!!!!!") # New test log

@flask_app.route('/')
def home():
    return "Bot is alive and kicking!", 200 # Endpoint for uptime monitor


def _data_dir() -> str:
    # Keep uploads alongside the SQLite DB so Docker volume mapping persists them.
    db_path = getattr(config, "SQLITE_DB_PATH", "") or "data/app.db"
    base = os.path.dirname(db_path) or "data"
    return base


def _xiaomi_upload_dir(user_id: str) -> str:
    base = _data_dir()
    path = os.path.join(base, "xiaomi_uploads", str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _read_file_head_text(path: str, *, max_bytes: int = 4096) -> str:
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _summarize_csv(path: str, *, max_rows: int = 200000) -> Dict[str, Any]:
    """
    Lightweight CSV summary for DM/debugging.
    - Handles UTF-8 with BOM.
    - Limits row counting to avoid worst-case slow parsing.
    """
    result: Dict[str, Any] = {"headers": [], "rows": 0, "truncated": False}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.reader(f)
            headers = next(reader, None)
            if headers is None:
                return result
            result["headers"] = [h.strip() for h in headers]
            rows = 0
            for _ in reader:
                rows += 1
                if rows >= max_rows:
                    result["truncated"] = True
                    break
            result["rows"] = rows
            return result
    except Exception as e:
        result["error"] = str(e)
        return result


def _ingest_uploaded_files_for_xiaomi(user_id: str) -> List[Dict[str, Any]]:
    """
    Save any uploaded multipart files (e.g., Automate -> multipart/form-data) and return metadata.
    Expected field from Automate flow: 'file'
    """
    files_meta: List[Dict[str, Any]] = []
    try:
        if not request.files:
            return files_meta
    except Exception:
        return files_meta

    upload_dir = _xiaomi_upload_dir(user_id)
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    try:
        field_names = list(request.files.keys())
    except Exception:
        field_names = []

    for field in field_names:
        try:
            file_list = request.files.getlist(field)
        except Exception:
            file_list = []
        for storage in file_list:
            try:
                orig_name = (getattr(storage, "filename", None) or "").strip() or "upload.bin"
                safe_name = secure_filename(orig_name) or "upload.bin"
                save_name = f"{ts}_{safe_name}"
                save_path = os.path.join(upload_dir, save_name)

                storage.save(save_path)

                try:
                    size_bytes = int(os.path.getsize(save_path))
                except Exception:
                    size_bytes = None

                head_text = _read_file_head_text(save_path)
                ct = (getattr(storage, "content_type", "") or "").lower()
                is_csv = safe_name.lower().endswith(".csv") or ("text/csv" in ct)
                csv_summary = _summarize_csv(save_path) if is_csv else None

                files_meta.append(
                    {
                        "field": field,
                        "filename": safe_name,
                        "saved_as": save_name,
                        "content_type": getattr(storage, "content_type", None),
                        "size_bytes": size_bytes,
                        "head": head_text[:1500],
                        "csv": csv_summary,
                    }
                )
            except Exception as e:
                files_meta.append({"field": field, "error": str(e)})

    return files_meta


def _extract_incoming_payload() -> dict:
    """
    Best-effort parsing for:
    - JSON POST bodies
    - form-encoded bodies
    - querystring (Tasker sometimes uses this)
    - multipart file uploads (Automate export CSV)
    """
    content_type = ""
    try:
        content_type = (request.content_type or "").lower()
    except Exception:
        content_type = ""

    content_length = None
    try:
        content_length = request.content_length
    except Exception:
        content_length = None

    # IMPORTANT: for multipart uploads, don't read the whole body into memory.
    body_text = ""
    if not content_type.startswith("multipart/"):
        try:
            body_text = request.get_data(as_text=True) or ""
        except Exception:
            body_text = ""

    json_body = None
    try:
        json_body = request.get_json(silent=True)
    except Exception:
        json_body = None

    form_body = {}
    try:
        form_body = request.form.to_dict(flat=True) if request.form else {}
    except Exception:
        form_body = {}

    args = {}
    try:
        args = request.args.to_dict(flat=True) if request.args else {}
    except Exception:
        args = {}

    payload = {
        "headers": {k: v for k, v in request.headers.items()},
        "json": json_body,
        "form": form_body,
        "query": args,
        "raw": body_text[:50000],
        "content_type": content_type,
        "content_length": content_length,
        "path": request.path,
        "method": request.method,
    }
    return payload


def _format_xiaomi_message(payload: dict) -> str:
    """
    Create a readable DM message from arbitrary webhook payloads.
    We keep it generic, but try to surface common fields (value1/value2/value3, event/type).
    """
    data = payload.get("json") if isinstance(payload.get("json"), dict) else None
    form = payload.get("form") if isinstance(payload.get("form"), dict) else {}
    query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    files = payload.get("files") if isinstance(payload.get("files"), list) else []

    # Common fields used by webhooks/IFTTT/Tasker patterns
    def pick(*keys: str) -> str:
        for k in keys:
            if data and k in data and data[k] is not None:
                return str(data[k])
            if k in form and form[k] is not None:
                return str(form[k])
            if k in query and query[k] is not None:
                return str(query[k])
        return ""

    event = pick("event", "type", "action", "name")
    v1 = pick("value1", "v1", "title")
    v2 = pick("value2", "v2", "message", "text")
    v3 = pick("value3", "v3", "extra", "details")

    lines = ["📥 **Xiaomi/Notify webhook received**"]
    if files:
        lines.append(f"**Upload:** `{len(files)}` file(s)")
        for f in files[:2]:
            if not isinstance(f, dict):
                continue
            if f.get("error"):
                lines.append(f"- `{f.get('field','file')}`: ❌ {f.get('error')}")
                continue
            fname = f.get("filename") or "upload"
            saved_as = f.get("saved_as") or fname
            size_b = f.get("size_bytes")
            size_s = f"{size_b} bytes" if isinstance(size_b, int) else "unknown size"
            lines.append(f"- `{fname}` saved as `{saved_as}` ({size_s})")
            csv_info = f.get("csv") if isinstance(f.get("csv"), dict) else None
            if csv_info:
                hdrs = csv_info.get("headers") if isinstance(csv_info.get("headers"), list) else []
                rows = csv_info.get("rows")
                truncated = bool(csv_info.get("truncated"))
                suffix = " (truncated)" if truncated else ""
                lines.append(f"  - CSV: `{len(hdrs)}` columns, `{rows}` rows{suffix}")
    if event:
        lines.append(f"**Event:** `{event}`")
    if v1 or v2 or v3:
        if v1:
            lines.append(f"**value1:** {v1}")
        if v2:
            lines.append(f"**value2:** {v2}")
        if v3:
            lines.append(f"**value3:** {v3}")

    # Attach a compact JSON snippet for debugging
    snippet_obj = data if data is not None else {"form": form, "query": query}
    try:
        snippet = json.dumps(snippet_obj, ensure_ascii=False, indent=2)
    except Exception:
        snippet = str(snippet_obj)
    snippet = snippet[:1500]
    lines.append("```json\n" + snippet + "\n```")
    return "\n".join(lines)


@flask_app.route("/webhook/xiaomi/<token>", methods=["POST"])
def xiaomi_webhook(token: str):
    # Token is the only authentication mechanism; keep it secret per user.
    if not token or len(token) < 10:
        return "invalid token", 400

    if not getattr(bot, "db_manager", None):
        return "db not ready", 503

    user_id = None
    try:
        user_id = bot.db_manager.get_xiaomi_user_id_by_token(str(token))
    except Exception as e:
        log.error(f"Xiaomi webhook lookup failed: {e}", exc_info=True)
        return "lookup failed", 500

    if not user_id:
        return "unknown token", 404

    payload = _extract_incoming_payload()
    # If Automate sends a CSV via multipart/form-data, save it and include metadata in the payload.
    try:
        files_meta = _ingest_uploaded_files_for_xiaomi(str(user_id))
        if files_meta:
            payload["files"] = files_meta
    except Exception as e:
        payload["files"] = [{"error": str(e)}]
    try:
        bot.db_manager.touch_xiaomi_webhook_last_seen(user_id, payload)
    except Exception as e:
        log.warning(f"Could not persist Xiaomi webhook payload: {e}")

    # Deliver to Discord DM via the bot event loop.
    try:
        msg = _format_xiaomi_message(payload)
        asyncio.run_coroutine_threadsafe(_deliver_xiaomi_dm(int(user_id), msg), bot.loop)
    except Exception as e:
        log.error(f"Failed to schedule Xiaomi DM delivery: {e}", exc_info=True)
        return "delivery failed", 500

    return "ok", 200


async def _deliver_xiaomi_dm(user_id: int, content: str) -> None:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        if not user:
            return
        # Discord hard limit is 2000 chars; keep it safe.
        if len(content) <= 1900:
            await user.send(content)
            return
        for chunk in textwrap.wrap(content, width=1800, break_long_words=False, replace_whitespace=False):
            await user.send(chunk)
    except Exception as e:
        log.error(f"Failed to DM Xiaomi webhook content to user {user_id}: {e}", exc_info=True)

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
