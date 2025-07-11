import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import logging
import time
from typing import Optional, Dict, Tuple, Any
import mimetypes
import tempfile
import os

import config

logger = logging.getLogger(__name__)

try:
    import google.generativeai as genai  # Google Gemini SDK
except ImportError:
    genai = None
    logger.warning(
        "google-generativeai package not installed. Gemini commands will be disabled.")

# ---------------------------------
# Helper Types
# ---------------------------------
SessionKey = Tuple[int, int, int]  # (guild_id, channel_id, user_id)
SessionEntry = Dict[str, Any]  # {"session": ChatSession, "model": str, "last": float}

# ---------------------------------
# Cog
# ---------------------------------

class GeminiAI(commands.Cog):
    """Cog providing an interface to Google Gemini AI models (multi-turn)."""

    # Allowed attachment settings
    # Set to None for no hard size cap (Discord’s 25 MB limit still applies)
    _MAX_ATTACHMENT_MB: Optional[int] = None
    _ALLOWED_MIME_PREFIXES = ("image/",)
    _ALLOWED_MIME_TYPES = {"application/pdf", "text/plain"}

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._configured: bool = bool(config.GEMINI_API_KEY) and genai is not None

        # Setup models
        if self._configured:
            genai.configure(api_key=config.GEMINI_API_KEY)
            self.primary_model_name = "gemini-2.5-pro"
            self.fallback_model_name = "gemini-2.5-flash"
            self.model_primary = genai.GenerativeModel(self.primary_model_name)
            self._model_fallback: Optional[genai.GenerativeModel] = None  # lazily created
        else:
            self.model_primary = None
            self._model_fallback = None

        self.logger = logging.getLogger(__name__)

        # One ChatSession per user per channel
        self.sessions: Dict[SessionKey, SessionEntry] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_key(self, ctx: commands.Context) -> SessionKey:
        return (
            ctx.guild.id if ctx.guild else 0,
            ctx.channel.id,
            ctx.author.id,
        )

    def _get_session(self, ctx: commands.Context, reset: bool = False) -> SessionEntry:
        key = self._make_key(ctx)

        if not self._configured:
            raise RuntimeError("Gemini AI not configured")

        # Reset or create
        if reset or key not in self.sessions:
            # Try primary first
            try:
                chat_obj = self.model_primary.start_chat(history=[])
                self.sessions[key] = {
                    "session": chat_obj,
                    "model": self.primary_model_name,
                    "last": time.time(),
                }
            except Exception as primary_error:
                self.logger.warning(
                    f"Primary Gemini model '{self.primary_model_name}' failed to start chat: {primary_error}. Trying fallback '{self.fallback_model_name}'."
                )

                if self._model_fallback is None:
                    try:
                        self._model_fallback = genai.GenerativeModel(
                            self.fallback_model_name)
                    except Exception as inst_err:
                        raise RuntimeError(
                            f"Failed to instantiate fallback model '{self.fallback_model_name}': {inst_err}")

                try:
                    chat_obj = self._model_fallback.start_chat(history=[])
                    self.sessions[key] = {
                        "session": chat_obj,
                        "model": self.fallback_model_name,
                        "last": time.time(),
                    }
                except Exception as fallback_error:
                    raise RuntimeError(
                        f"Both Gemini models failed to create chat sessions: {fallback_error}")

        return self.sessions[key]

    def _delete_session(self, ctx: commands.Context):
        self.sessions.pop(self._make_key(ctx), None)

    # Basic history trimming – keep last 20 exchanges (40 messages)
    def _prune_history(self, chat_session, max_exchanges: int = 20):
        try:
            history = chat_session.history  # list[dict]
            # Each exchange is 2 messages (user + assistant)
            while len(history) > max_exchanges * 2:
                history.pop(0)
        except Exception as e:
            # Be resilient – pruning is best-effort
            self.logger.debug(f"Pruning history failed: {e}")

    # ------------------------------------------------------------------
    # Attachment helpers
    # ------------------------------------------------------------------

    async def _upload_attachments(self, attachments: list[discord.Attachment]) -> list[dict[str, str]]:
        """Download and upload Discord attachments to Gemini Files API, returning file_ids."""
        if not attachments:
            return []

        if genai is None:
            raise RuntimeError("google-generativeai library required for file uploads")

        file_parts: list[dict[str, str]] = []

        for attachment in attachments:
            # Size validation
            if self._MAX_ATTACHMENT_MB is not None and attachment.size > self._MAX_ATTACHMENT_MB * 1024 * 1024:
                self.logger.warning("Attachment %s rejected: exceeds size limit", attachment.filename)
                continue

            # Mime detection – prefer Discord-provided content_type, fallback to filename extension
            mime = attachment.content_type or mimetypes.guess_type(attachment.filename)[0] or "application/octet-stream"

            # Type validation
            if not any(mime.startswith(pref) for pref in self._ALLOWED_MIME_PREFIXES) and mime not in self._ALLOWED_MIME_TYPES:
                self.logger.debug("Attachment %s skipped: mime %s not allowed", attachment.filename, mime)
                continue

            # Download bytes
            try:
                data: bytes = await attachment.read()
            except Exception as dl_err:
                self.logger.warning("Failed to read attachment %s: %s", attachment.filename, dl_err)
                continue

            # Write to temporary file because current genai.upload_file API expects a path
            try:
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(data)
                    tmp.flush()
                    tmp_path = tmp.name

                # Upload – genai.upload_file returns File object with .name containing file_id
                file_obj = genai.upload_file(path=tmp_path, mime_type=mime, display_name=attachment.filename)
                file_part = {
                    "file_uri": getattr(file_obj, "uri", file_obj.name),
                    "mime_type": mime,
                }
                file_parts.append(file_part)
                self.logger.debug("Uploaded attachment %s as uri %s", attachment.filename, file_part["file_uri"])
            except Exception as up_err:
                self.logger.error("Failed to upload attachment %s: %s", attachment.filename, up_err)
            finally:
                # Clean temp file
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        return file_parts

    # ------------------------------------------------------------------
    # NEW: Model switching helper
    # ------------------------------------------------------------------

    def _switch_primary_model(self, new_primary: str, new_fallback: str) -> None:
        """Switch the primary / fallback Gemini model pair at runtime.

        This updates `self.primary_model_name`, reinstantiates `self.model_primary`,
        sets `self.fallback_model_name`, and resets the cached fallback model so it
        will be lazily recreated when needed. Existing chat sessions are *not*
        modified – they will continue using the model they were started with.
        New conversations (or resets) will use the updated primary model.
        """
        # Short-circuit if nothing changes
        if self.primary_model_name == new_primary:
            return

        self.logger.info(
            "Switching primary Gemini model from '%s' to '%s' (fallback -> '%s')",
            self.primary_model_name,
            new_primary,
            new_fallback,
        )

        # Update primary model
        try:
            self.model_primary = genai.GenerativeModel(new_primary)
        except Exception as err:
            # Keep old model on failure and raise upstream so the command can notify the user.
            self.logger.exception("Failed to instantiate new primary model '%s': %s", new_primary, err)
            raise

        # Update names and reset fallback for lazy re-creation
        self.primary_model_name = new_primary
        self.fallback_model_name = new_fallback
        self._model_fallback = None

    # Utility: split long text into Discord-compatible chunks (≤ 2000 chars each)
    def _split_content(self, content: str, limit: int = 2000) -> list[str]:
        """Return a list of strings each ≤ `limit` characters preserving order."""
        # Simple greedy slicing – break exactly at the limit. Could be improved to split on newlines.
        return [content[i : i + limit] for i in range(0, len(content), limit)]

    # ------------------------------------------------------------------
    # Command Group interface – better UX for slash commands
    # ------------------------------------------------------------------

    @commands.hybrid_group(name="gemini", invoke_without_command=True, description="Chat with Google Gemini AI (multi-turn)")
    async def gemini(self, ctx: commands.Context):
        """Prefix: !gemini <prompt> (alias for ask). Slash: suggest using subcommands."""

        # If called as slash without subcommand, show usage hint
        if ctx.interaction is not None:
            await ctx.interaction.response.send_message(
                "❌ Please use `/gemini ask <prompt>`, `/gemini new <prompt>`, or `/gemini reset`.",
                ephemeral=True,
            )
            return

        # Prefix invocation – extract prompt after command name
        raw = ctx.message.content[len(ctx.prefix) + len(ctx.invoked_with):].lstrip()

        if not raw:
            await ctx.send("❌ Please provide a prompt for Gemini AI (e.g., `!gemini What is quantum entanglement?`).")
            return

        tokens = raw.split()
        if tokens[0].lower() in {"reset", "r"}:
            await self.reset(ctx)
            return
        if tokens[0].lower() in {"new", "n"}:
            new_prompt = " ".join(tokens[1:])
            if not new_prompt:
                await ctx.send("❌ Please provide a prompt after `new` (e.g., `!gemini new What is Rust?`).")
                return
            await self.new(ctx, prompt=new_prompt)
            return

        # Default → ask
        await self.ask(ctx, prompt=raw)

    # ---------------------------
    # /gemini ask
    # ---------------------------

    @gemini.command(name="ask", description="Ask Gemini AI – continue current conversation or start if none.")
    @app_commands.describe(prompt="Your prompt for Gemini AI.")
    async def ask(self, ctx: commands.Context, *, prompt: str):
        await self._handle_prompt(ctx, prompt, reset=False)

    # ---------------------------
    # /gemini new
    # ---------------------------

    @gemini.command(name="new", description="Start a brand-new conversation with Gemini AI.")
    @app_commands.describe(prompt="Your prompt for Gemini AI.")
    async def new(self, ctx: commands.Context, *, prompt: str):
        await self._handle_prompt(ctx, prompt, reset=True)

    # ---------------------------
    # NEW: /gemini slow & /gemini fast
    # ---------------------------

    @gemini.command(name="slow", description="Switch the default Gemini model to the slower but more capable 2.5-pro.")
    async def slow(self, ctx: commands.Context):
        """Alias: !gemini slow – sets primary model to gemini-2.5-pro."""
        if not self._configured:
            await self._send_simple(ctx, "❌ Gemini AI is not configured by the bot owner.")
            return
        try:
            self._switch_primary_model("gemini-2.5-pro", "gemini-2.5-flash")
            await self._send_simple(ctx, "✅ Default Gemini model set to **gemini-2.5-pro**. New conversations will use this model.")
        except Exception as e:
            await self._send_simple(ctx, f"⚠️ Failed to switch model: {e}")

    @gemini.command(name="fast", description="Switch the default Gemini model to the faster 2.5-flash.")
    async def fast(self, ctx: commands.Context):
        """Alias: !gemini fast – sets primary model to gemini-2.5-flash."""
        if not self._configured:
            await self._send_simple(ctx, "❌ Gemini AI is not configured by the bot owner.")
            return
        try:
            self._switch_primary_model("gemini-2.5-flash", "gemini-2.5-pro")
            await self._send_simple(ctx, "✅ Default Gemini model set to **gemini-2.5-flash**. New conversations will use this model.")
        except Exception as e:
            await self._send_simple(ctx, f"⚠️ Failed to switch model: {e}")

    # ---------------------------
    # NEW: /gemini superfast
    # ---------------------------

    @gemini.command(name="superfast", description="Switch the default Gemini model to the ultra-fast flash-lite preview.")
    async def superfast(self, ctx: commands.Context):
        """Alias: !gemini superfast – sets primary model to gemini-2.5-flash-lite-preview-06-17."""
        if not self._configured:
            await self._send_simple(ctx, "❌ Gemini AI is not configured by the bot owner.")
            return
        try:
            self._switch_primary_model("gemini-2.5-flash-lite-preview-06-17", "gemini-2.5-flash")
            await self._send_simple(ctx, "✅ Default Gemini model set to **gemini-2.5-flash-lite-preview-06-17**. New conversations will use this model.")
        except Exception as e:
            await self._send_simple(ctx, f"⚠️ Failed to switch model: {e}")

    # ---------------------------
    # /gemini reset
    # ---------------------------

    @gemini.command(name="reset", description="Reset / forget the current conversation with Gemini AI.")
    async def reset(self, ctx: commands.Context):
        self._delete_session(ctx)
        await self._send_simple(ctx, "✅ Conversation reset.")

    # ------------------------------------------------------------------
    # Core prompt handler
    # ------------------------------------------------------------------

    async def _handle_prompt(self, ctx: commands.Context, prompt: str, *, reset: bool):
        """Shared logic for ask/new commands."""

        # Interaction vs prefix helpers
        is_slash = ctx.interaction is not None

        typing_ctx = None
        if is_slash:
            await ctx.interaction.response.defer(thinking=True)
        else:
            typing_ctx = ctx.channel.typing()
            await typing_ctx.__aenter__()

        async def send(content: str, **kwargs):
            """Unified send helper that works for both slash and prefix invocations."""
            if is_slash:
                interaction: discord.Interaction = ctx.interaction  # type: ignore
                # Prefer original response if not yet sent; otherwise use follow-up
                if not interaction.response.is_done():
                    await interaction.response.send_message(content, **kwargs)
                else:
                    await interaction.followup.send(content, **kwargs)
            else:
                # 'ephemeral' kwarg is meaningless for normal channel messages
                kwargs.pop("ephemeral", None)
                await ctx.send(content, **kwargs)

        # Gather any attachments (prefix commands) or from interaction (slash commands)
        attachments: list[discord.Attachment] = []
        if hasattr(ctx, "message") and ctx.message and ctx.message.attachments:
            attachments = list(ctx.message.attachments)
        # Slash commands may provide attachments via Interaction if option defined (future-proof)
        elif getattr(ctx.interaction, "attachments", None):
            attachments = list(ctx.interaction.attachments)  # type: ignore

        # Check configuration (after we know if attachments exist)
        if not self._configured:
            await send("❌ Gemini AI is not configured by the bot owner. Please try again later.", ephemeral=is_slash)
            return

        # Acquire / create session
        try:
            entry = self._get_session(ctx, reset=reset)
        except RuntimeError as err:
            await send(f"⚠️ {err}", ephemeral=is_slash)
            return

        chat_session = entry["session"]
        model_name = entry["model"]

        # Prepare message payload – include Files API references if we have attachments
        file_parts: list[dict[str, str]] = []
        if attachments:
            try:
                file_parts = await self._upload_attachments(attachments)
            except Exception as attach_err:
                self.logger.error("Attachment processing failed: %s", attach_err)

        # Build parts list if we have uploaded files
        if file_parts:
            parts: list[dict[str, Any]] = [{"text": prompt}] if prompt else []
            for fp in file_parts:
                parts.append({"file_data": fp})
            user_input = {"parts": parts}
        else:
            user_input = prompt

        loop = asyncio.get_running_loop()

        try:
            # Run the blocking Gemini call in a thread pool but give it a generous timeout (up to 5 minutes)
            send_future = loop.run_in_executor(None, chat_session.send_message, user_input)
            # If the request takes longer than 300 seconds we abort and inform the user
            try:
                response = await asyncio.wait_for(send_future, timeout=300)
            except asyncio.TimeoutError:
                # Best-effort cancellation; thread may keep running but we still respond.
                self.logger.warning("Gemini chat request timed out (>300s).")
                raise RuntimeError("Gemini AI took too long to respond. Please try again later.")
            answer: Optional[str] = getattr(response, "text", str(response))

            if not answer:
                raise ValueError("Empty response from Gemini API")

            notice = ""
            if model_name != self.primary_model_name:
                notice = f"⚠️ Primary model '{self.primary_model_name}' unavailable, using '{model_name}'.\n\n"

            full_message = notice + answer
            # Send in chunks if necessary (Discord 2k char hard limit)
            if len(full_message) > 2000:
                chunks = self._split_content(full_message, limit=2000)
            else:
                chunks = [full_message]

            # Logging before attempting to send first chunk
            self.logger.info(
                "Gemini answer length=%d (notice=%s) preview=%r", len(full_message), bool(notice), full_message[:120]
            )

            try:
                for chunk in chunks:
                    await send(chunk)
                self.logger.debug("Gemini response delivered successfully in %d chunk(s)", len(chunks))
            except (discord.HTTPException, discord.Forbidden, discord.NotFound) as send_err:
                # Log the full stack trace for diagnosis and try a graceful fallback
                self.logger.exception("Failed to deliver Gemini response: %s", send_err)
                fallback_msg = "⚠️ Unable to send Gemini response due to Discord error. Please check bot permissions or try again later."
                try:
                    await send(fallback_msg, ephemeral=is_slash)
                except Exception:
                    # Give up; re-raise so the outer except handles it too.
                    raise

            # Update bookkeeping only if we didn't raise
            entry["last"] = time.time()
            self._prune_history(chat_session)

        except Exception as error:
            self.logger.error(f"Gemini chat failed: {error}")
            await send("⚠️ Gemini AI is currently unavailable. Please try again later.", ephemeral=is_slash)
        finally:
            # Ensure typing indicator stops
            if typing_ctx is not None:
                await typing_ctx.__aexit__(None, None, None)

    # ------------------------------------------------------------------
    # Utility send helper
    # ------------------------------------------------------------------

    async def _send_simple(self, ctx: commands.Context, content: str):
        """Send a basic response handling defer/typing gracefully."""
        if ctx.interaction is not None:
            # If we haven't deferred yet, send initial response; else followup
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, ephemeral=True)
            else:
                await ctx.interaction.followup.send(content, ephemeral=True)
        else:
            await ctx.send(content)

# ------------------------------------------------------------------
# Setup function (cog loader)
# ------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(GeminiAI(bot)) 