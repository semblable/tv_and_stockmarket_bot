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
    # Single command interface
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="gemini", description="Chat with Google Gemini AI.")
    @app_commands.describe(
        prompt="Your prompt for Gemini AI (omit when just resetting).",
        new="Start a brand-new conversation (ignores previous context).",
        reset="Reset the current conversation without sending a prompt.",
    )
    async def gemini(
        self,
        ctx: commands.Context,
        *,
        prompt: Optional[str] = None,
        new: Optional[bool] = False,
        reset: Optional[bool] = False,
    ):
        """Unified command:

        • /gemini <prompt> – continue conversation
        • /gemini new:true <prompt> – start fresh conversation
        • /gemini reset:true – drop conversation history

        Prefix aliases:
        !gemini reset | !gemini new <prompt> | !gemini <prompt>
        """

        # Prefix commands may supply flags like "reset" or "new" as first token
        if not ctx.interaction and prompt is not None:
            tokens = prompt.split()
            if tokens and tokens[0].lower() in {"reset", "r"}:
                reset = True
                prompt = " ".join(tokens[1:]) or None
            elif tokens and tokens[0].lower() in {"new", "n"}:
                new = True
                prompt = " ".join(tokens[1:]) or None

        # Handle reset-only request
        if reset and (prompt is None):
            self._delete_session(ctx)
            await self._send_simple(ctx, "✅ Conversation reset.")
            return

        # Decide whether to reset before asking
        reset_before_ask = new or reset

        if prompt is None or prompt.strip() == "":
            await self._send_simple(ctx, "❌ Please provide a prompt for Gemini AI.")
            return

        await self._handle_prompt(ctx, prompt.strip(), reset=reset_before_ask)

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
            if is_slash:
                await ctx.followup.send(content, **kwargs)
            else:
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
            response = await loop.run_in_executor(None, chat_session.send_message, prompt)
            answer: Optional[str] = getattr(response, "text", str(response))

            if not answer:
                raise ValueError("Empty response from Gemini API")

            # 2k char limit for Discord
            if len(answer) > 2000:
                answer = answer[:1990] + "…"

            notice = ""
            if model_name != self.primary_model_name:
                notice = f"⚠️ Primary model '{self.primary_model_name}' unavailable, using '{model_name}'.\n\n"

            await send(notice + answer)

            # Update bookkeeping
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
                await ctx.followup.send(content, ephemeral=True)
        else:
            await ctx.send(content)

# ------------------------------------------------------------------
# Setup function (cog loader)
# ------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(GeminiAI(bot)) 