import asyncio
import json
import logging
import os
import time

import requests
import discord
from discord import app_commands
from discord.ext import commands

import config

logger = logging.getLogger(__name__)

# Persists authorized Discord user IDs across restarts.
# Stored next to the SQLite DB so the data/ directory already exists.
_AUTH_FILE = os.path.join(os.path.dirname(config.SQLITE_DB_PATH), "timer_auth.json")


# ---------------------------------------------------------------------------
# Auth persistence helpers
# ---------------------------------------------------------------------------

def _load_authorized_ids() -> set[int]:
    try:
        with open(_AUTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(int(x) for x in data.get("authorized_ids", []))
    except FileNotFoundError:
        return set()
    except Exception:
        logger.exception("Failed to read timer auth file")
        return set()


def _save_authorized_ids(ids: set[int]) -> None:
    os.makedirs(os.path.dirname(_AUTH_FILE), exist_ok=True)
    try:
        with open(_AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump({"authorized_ids": list(ids)}, f)
    except Exception:
        logger.exception("Failed to write timer auth file")


# ---------------------------------------------------------------------------
# Firebase helpers
# ---------------------------------------------------------------------------

def _missing_firebase_config() -> list[str]:
    """Return which env vars are unset (empty or whitespace). Both are required for Firebase REST."""
    missing: list[str] = []
    if not (config.FIREBASE_DATABASE_URL or "").strip():
        missing.append("FIREBASE_DATABASE_URL")
    if not (config.FIREBASE_DATABASE_SECRET or "").strip():
        missing.append("FIREBASE_DATABASE_SECRET")
    return missing


def _fb_url(path: str) -> str:
    base = config.FIREBASE_DATABASE_URL.rstrip("/")
    return f"{base}/{path}.json?auth={config.FIREBASE_DATABASE_SECRET}"


def _send_timer_command(cmd_data: dict, timeout: int = 15) -> dict:
    """POST a command to Firebase then poll for the result (blocking)."""
    payload = {
        **cmd_data,
        "secret": config.DISCORD_SYNC_SECRET,
        "timestamp": int(time.time() * 1000),
        "processed": False,
    }
    resp = requests.post(_fb_url("discord-sync/commands"), json=payload, timeout=10)
    resp.raise_for_status()
    cmd_id = resp.json()["name"]

    result_url = _fb_url(f"discord-sync/command-results/{cmd_id}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(result_url, timeout=10)
        result = r.json()
        if result is not None:
            return result
        time.sleep(0.5)

    return {"success": False, "message": "Timed out — is the local app running?"}


def _read_timer_status() -> str:
    """Read timer state directly from Firebase — no command queue needed."""
    r = requests.get(_fb_url("discord-sync/timer-state"), timeout=10)
    state = r.json()

    if not state or not state.get("active"):
        return "No timer running."

    elapsed_sec = int((time.time() * 1000 - state["startTime"]) / 1000)
    h, remainder = divmod(elapsed_sec, 3600)
    m, s = divmod(remainder, 60)

    msg = f'Timer running: "{state.get("description", "?")}" | {h}h {m}m {s}s'
    if state.get("projectName"):
        msg += f" | Project: {state['projectName']}"
    if state.get("goalName"):
        msg += f" | Goal: {state['goalName']}"

    if time.time() * 1000 - state.get("lastUpdated", 0) > 120_000:
        msg += "\n(local app may be offline)"

    return msg


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class TimerCog(commands.Cog, name="Timer"):
    """Timer commands relayed to a local time-tracking app via Firebase."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._authorized_ids: set[int] = _load_authorized_ids()

    async def _check_configured(self, ctx: commands.Context) -> bool:
        """Return True if Firebase env vars are set; otherwise send an error and return False."""
        missing = _missing_firebase_config()
        if missing:
            await ctx.send(
                "Timer is not configured. Missing: **"
                + "**, **".join(missing)
                + "**. "
                "They must be in the container environment (usually from `~/.env` on the host via "
                "`docker run --env-file`). Add them as GitHub Actions repository secrets and redeploy, "
                "or edit `~/.env` on the server and `docker restart bot-container`.",
                ephemeral=True,
            )
            return False
        return True

    def _is_authorized(self, user_id: int) -> bool:
        return user_id in self._authorized_ids

    def _authorize(self, user_id: int) -> None:
        self._authorized_ids.add(user_id)
        _save_authorized_ids(self._authorized_ids)

    def _deauthorize(self, user_id: int) -> None:
        self._authorized_ids.discard(user_id)
        _save_authorized_ids(self._authorized_ids)

    @commands.hybrid_group(name="timer", invoke_without_command=True)
    async def timer(self, ctx: commands.Context):
        """Timer commands. Use a subcommand: auth, start, stop, status, projects, goals."""
        await ctx.send(
            "**Timer commands:**\n"
            "`!timer auth <password>` — enable timer access for your account\n"
            "`!timer revoke` — remove your timer access\n"
            "`!timer start [description] [project:<name>] [goal:<name>]`\n"
            "`!timer stop`\n"
            "`!timer status`\n"
            "`!timer projects`\n"
            "`!timer goals [project:<name>]`",
            ephemeral=True,
        )

    # --- Auth management ---

    @timer.command(name="auth")
    @app_commands.describe(password="The timer auth password")
    async def timer_auth(self, ctx: commands.Context, password: str):
        """Authorize yourself to use timer commands by providing the shared password."""
        if not await self._check_configured(ctx):
            return
        configured = config.TIMER_AUTH_PASSWORD
        if not configured:
            await ctx.send(
                "Timer auth is not configured (TIMER_AUTH_PASSWORD is not set).",
                ephemeral=True,
            )
            return

        if password != configured:
            await ctx.send("Incorrect password.", ephemeral=True)
            logger.warning("Failed timer auth attempt by user %s", ctx.author.id)
            return

        self._authorize(ctx.author.id)
        await ctx.send("You now have access to timer commands.", ephemeral=True)
        logger.info("Timer access granted to user %s (%s)", ctx.author.id, ctx.author)

    @timer.command(name="revoke")
    async def timer_revoke(self, ctx: commands.Context):
        """Remove your own timer access."""
        if not self._is_authorized(ctx.author.id):
            await ctx.send("You don't have timer access.", ephemeral=True)
            return

        self._deauthorize(ctx.author.id)
        await ctx.send("Your timer access has been removed.", ephemeral=True)
        logger.info("Timer access revoked for user %s (%s)", ctx.author.id, ctx.author)

    # --- Timer commands ---

    @timer.command(name="start")
    @app_commands.describe(
        description="What to work on (optional)",
        project="Project name — fuzzy-matched (optional)",
        goal="Goal name — fuzzy-matched (optional)",
    )
    async def timer_start(
        self,
        ctx: commands.Context,
        description: str = "",
        project: str = "",
        goal: str = "",
    ):
        """Start a timer. Optionally specify a description, project, and/or goal."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await ctx.send(
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        cmd: dict = {"type": "start"}
        if description:
            cmd["description"] = description
        if project:
            cmd["project"] = project
        if goal:
            cmd["goal"] = goal

        await ctx.send("Starting timer...")
        try:
            result = await asyncio.to_thread(_send_timer_command, cmd)
        except Exception as exc:
            logger.exception("Error sending timer start command")
            await ctx.send(f"Error communicating with Firebase: {exc}")
            return
        await ctx.send(result["message"])

    @timer.command(name="stop")
    async def timer_stop(self, ctx: commands.Context):
        """Stop the currently running timer."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await ctx.send(
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        await ctx.send("Stopping timer...")
        try:
            result = await asyncio.to_thread(_send_timer_command, {"type": "stop"})
        except Exception as exc:
            logger.exception("Error sending timer stop command")
            await ctx.send(f"Error communicating with Firebase: {exc}")
            return
        await ctx.send(result["message"])

    @timer.command(name="status")
    async def timer_status(self, ctx: commands.Context):
        """Check timer status directly from Firebase (fast, works even if local app is offline)."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await ctx.send(
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        try:
            msg = await asyncio.to_thread(_read_timer_status)
        except Exception as exc:
            logger.exception("Error reading timer status")
            await ctx.send(f"Error communicating with Firebase: {exc}")
            return
        await ctx.send(msg)

    @timer.command(name="projects")
    async def timer_projects(self, ctx: commands.Context):
        """List available projects."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await ctx.send(
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        try:
            result = await asyncio.to_thread(_send_timer_command, {"type": "projects"})
        except Exception as exc:
            logger.exception("Error sending timer projects command")
            await ctx.send(f"Error communicating with Firebase: {exc}")
            return
        await ctx.send(result["message"])

    @timer.command(name="goals")
    @app_commands.describe(project="Filter goals by project name (optional)")
    async def timer_goals(self, ctx: commands.Context, project: str = ""):
        """List available goals, optionally filtered by project."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await ctx.send(
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        cmd: dict = {"type": "goals"}
        if project:
            cmd["project"] = project

        try:
            result = await asyncio.to_thread(_send_timer_command, cmd)
        except Exception as exc:
            logger.exception("Error sending timer goals command")
            await ctx.send(f"Error communicating with Firebase: {exc}")
            return
        await ctx.send(result["message"])


async def setup(bot: commands.Bot):
    await bot.add_cog(TimerCog(bot))
