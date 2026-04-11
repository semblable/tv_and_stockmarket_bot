import asyncio
import json
import logging
import os
import re
import time

import requests
import discord
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


def _parse_start_args(args_str: str) -> dict:
    """Parse 'description project:Foo goal:Bar' into components."""
    proj_match = re.search(r"\bproject:(.+?)(?=\bgoal:|$)", args_str, re.IGNORECASE)
    goal_match = re.search(r"\bgoal:(.+?)(?=\bproject:|$)", args_str, re.IGNORECASE)

    cut_start = len(args_str)
    project = None
    goal = None

    if proj_match:
        project = proj_match.group(1).strip()
        cut_start = min(cut_start, proj_match.start())
    if goal_match:
        goal = goal_match.group(1).strip()
        cut_start = min(cut_start, goal_match.start())

    description = args_str[:cut_start].strip()
    return {
        "description": description or None,
        "project": project,
        "goal": goal,
    }


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class TimerCog(commands.Cog, name="Timer"):
    """Timer commands relayed to a local time-tracking app via Firebase."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._authorized_ids: set[int] = _load_authorized_ids()

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
    async def timer_auth(self, ctx: commands.Context, *, password: str = ""):
        """Authorize yourself to use timer commands by providing the shared password."""
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
    async def timer_start(self, ctx: commands.Context, *, args: str = ""):
        """Start a timer. Optionally specify description, project:, and/or goal:."""
        if not self._is_authorized(ctx.author.id):
            await ctx.send(
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        parsed = _parse_start_args(args)
        cmd: dict = {"type": "start"}
        if parsed["description"]:
            cmd["description"] = parsed["description"]
        if parsed["project"]:
            cmd["project"] = parsed["project"]
        if parsed["goal"]:
            cmd["goal"] = parsed["goal"]

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
    async def timer_goals(self, ctx: commands.Context, *, args: str = ""):
        """List available goals, optionally filtered by project:<name>."""
        if not self._is_authorized(ctx.author.id):
            await ctx.send(
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        cmd: dict = {"type": "goals"}
        if args.strip():
            parsed = _parse_start_args(args)
            if parsed["project"]:
                cmd["project"] = parsed["project"]

        try:
            result = await asyncio.to_thread(_send_timer_command, cmd)
        except Exception as exc:
            logger.exception("Error sending timer goals command")
            await ctx.send(f"Error communicating with Firebase: {exc}")
            return
        await ctx.send(result["message"])


async def setup(bot: commands.Bot):
    await bot.add_cog(TimerCog(bot))
