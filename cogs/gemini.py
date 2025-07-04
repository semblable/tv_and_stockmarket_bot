import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

try:
    import google.generativeai as genai  # Google Gemini SDK
except ImportError:
    genai = None
    logger.warning("google-generativeai package not installed. Gemini commands will be disabled.")

class GeminiAI(commands.Cog):
    """Cog providing an interface to Google Gemini AI models."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._configured: bool = bool(config.GEMINI_API_KEY) and genai is not None
        if self._configured:
            genai.configure(api_key=config.GEMINI_API_KEY)
            # Prepare both primary and fallback models
            self.primary_model_name = "gemini-2.5-pro"
            self.fallback_model_name = "gemini-2.5-flash"
            self.model_primary = genai.GenerativeModel(self.primary_model_name)
            # Lazily instantiate fallback when first needed to save memory; set to None initially
            self._model_fallback = None
            logger.info("GeminiAI Cog initialized with configured API key.")
        else:
            self.model_primary = None
            self._model_fallback = None
            if genai is None:
                logger.error("GeminiAI Cog could not import google-generativeai. Install the package to enable.")
            else:
                logger.warning("GeminiAI Cog loaded but GEMINI_API_KEY is missing. Commands will inform users of unavailability.")

    # ---------------------------------
    # Discord Commands
    # ---------------------------------
    @commands.hybrid_command(name="gemini", description="Ask Google Gemini AI a question and receive a response.")
    @app_commands.describe(prompt="Your question or prompt for Gemini AI.")
    async def gemini(self, ctx: commands.Context, *, prompt: str):
        """Handles the /gemini or !gemini command."""
        is_slash = ctx.interaction is not None
        if is_slash:
            await ctx.interaction.response.defer(thinking=True)
        else:
            # For prefix commands, show typing indicator
            await ctx.channel.typing().__aenter__()

        async def send_message(content: str, **kwargs):
            """Unified send helper for both slash and prefix invocations."""
            if is_slash:
                await ctx.followup.send(content, **kwargs)
            else:
                # ctx.send doesn't support ephemeral; ignore such kwarg
                kwargs.pop("ephemeral", None)
                await ctx.send(content, **kwargs)

        # Check configuration
        if not self._configured:
            await send_message(
                "❌ Gemini AI is not configured by the bot owner. Please try again later.",
                ephemeral=True if is_slash else False,
            )
            return

        # Use event loop executor for blocking SDK call
        loop = asyncio.get_running_loop()

        def generate(model):
            """Helper to run synchronous generate_content."""
            return model.generate_content(prompt)

        # First attempt with primary model
        try:
            response = await loop.run_in_executor(None, lambda: generate(self.model_primary))
            answer: Optional[str] = getattr(response, "text", str(response))
            if not answer:
                raise ValueError("Empty response from Gemini API (primary model)")

            if len(answer) > 2000:
                answer = answer[:1990] + "…"

            await send_message(answer)
            return
        except Exception as primary_error:
            # Log and prepare fallback attempt
            logger.warning(
                f"Primary Gemini model '{self.primary_model_name}' failed: {primary_error}. Attempting fallback '{self.fallback_model_name}'."
            )

        # Instantiate fallback model lazily
        if self._model_fallback is None:
            try:
                self._model_fallback = genai.GenerativeModel(self.fallback_model_name)
            except Exception as inst_err:
                logger.error(f"Failed to instantiate fallback model '{self.fallback_model_name}': {inst_err}")
                await send_message(
                    "⚠️ Gemini AI is currently unavailable (both primary and fallback models failed to initialize). Please try again later.",
                    ephemeral=True if is_slash else False,
                )
                return

        # Second attempt with fallback model
        try:
            response = await loop.run_in_executor(None, lambda: generate(self._model_fallback))
            answer: Optional[str] = getattr(response, "text", str(response))
            if not answer:
                raise ValueError("Empty response from Gemini API (fallback model)")

            if len(answer) > 2000:
                answer = answer[:1990] + "…"

            notice = f"⚠️ Primary model '{self.primary_model_name}' encountered an issue, switched to '{self.fallback_model_name}'.\n\n"
            await send_message(notice + answer)
        except Exception as fallback_error:
            logger.error(
                f"Both Gemini models failed (primary '{self.primary_model_name}', fallback '{self.fallback_model_name}'). Error: {fallback_error}"
            )
            await send_message(
                "⚠️ Gemini AI is currently unavailable (both models failed). Please try again later.",
                ephemeral=True if is_slash else False,
            )

async def setup(bot: commands.Bot):
    """Setup function to add the cog."""
    await bot.add_cog(GeminiAI(bot)) 