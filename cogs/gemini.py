import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import logging
import time
from typing import Optional, Dict, Tuple, Any

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

        # Check configuration
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

        loop = asyncio.get_running_loop()

        try:
            # Run the blocking Gemini call in a thread pool but give it a hard timeout so the bot does not get stuck
            send_future = loop.run_in_executor(None, chat_session.send_message, prompt)
            # If the request takes longer than 30 seconds we abort and inform the user
            try:
                response = await asyncio.wait_for(send_future, timeout=30)
            except asyncio.TimeoutError:
                # Best-effort cancellation; thread may keep running but we still respond.
                self.logger.warning("Gemini chat request timed out (>30s).")
                raise RuntimeError("Gemini AI took too long to respond. Please try again later.")
            answer: Optional[str] = getattr(response, "text", str(response))

            if not answer:
                raise ValueError("Empty response from Gemini API")

            notice = ""
            if model_name != self.primary_model_name:
                notice = f"⚠️ Primary model '{self.primary_model_name}' unavailable, using '{model_name}'.\n\n"

            full_message = notice + answer
            # 2k char limit for Discord content field (safety margin)
            if len(full_message) > 2000:
                full_message = full_message[:1990] + "…"

            # Logging before attempting to send
            self.logger.info(
                "Gemini answer length=%d (notice=%s) preview=%r", len(full_message), bool(notice), full_message[:120]
            )

            try:
                await send(full_message)
                self.logger.debug("Gemini follow-up delivered successfully")
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