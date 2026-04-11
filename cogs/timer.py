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

    async def cog_load(self):
        # Patch the top-level hybrid group directly since some discord.py versions
        # do not expose it reliably via generic tree walking before sync.
        def _patch(cmd: app_commands.AppCommand) -> None:
            app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)(cmd)
            app_commands.allowed_installs(guilds=True, users=True)(cmd)

        try:
            hybrid_group = getattr(self, "timer", None)
            app_group = getattr(hybrid_group, "app_command", None)
            if app_group is not None:
                try:
                    _patch(app_group)
                except Exception:
                    pass
                try:
                    for child in app_group.walk_commands():
                        try:
                            _patch(child)
                        except Exception:
                            continue
                except Exception:
                    pass
        except Exception:
            pass
        logger.info("TimerCog loaded and DM contexts patched.")

    async def _send_ctx(
        self,
        ctx: commands.Context,
        content: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        if getattr(ctx, "interaction", None):
            kwargs = {"content": content}
            if getattr(ctx, "guild", None) is not None:
                kwargs["ephemeral"] = bool(ephemeral)
            await ctx.send(**kwargs)
        else:
            await ctx.send(content)

    async def _check_configured(self, ctx: commands.Context) -> bool:
        """Return True if Firebase env vars are set; otherwise send an error and return False."""
        missing = _missing_firebase_config()
        if missing:
            await self._send_ctx(
                ctx,
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

    @commands.hybrid_group(
        name="timer",
        invoke_without_command=True,
        description="Relay timer actions to the local tracker.",
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def timer(self, ctx: commands.Context):
        """Timer commands. Use a subcommand: auth, start, stop, status, projects, goals."""
        await self._send_ctx(
            ctx,
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
            await self._send_ctx(
                ctx,
                "Timer auth is not configured (TIMER_AUTH_PASSWORD is not set).",
                ephemeral=True,
            )
            return

        if password != configured:
            await self._send_ctx(ctx, "Incorrect password.", ephemeral=True)
            logger.warning("Failed timer auth attempt by user %s", ctx.author.id)
            return

        self._authorize(ctx.author.id)
        await self._send_ctx(ctx, "You now have access to timer commands.", ephemeral=True)
        logger.info("Timer access granted to user %s (%s)", ctx.author.id, ctx.author)

    @timer.command(name="revoke")
    async def timer_revoke(self, ctx: commands.Context):
        """Remove your own timer access."""
        if not self._is_authorized(ctx.author.id):
            await self._send_ctx(ctx, "You don't have timer access.", ephemeral=True)
            return

        self._deauthorize(ctx.author.id)
        await self._send_ctx(ctx, "Your timer access has been removed.", ephemeral=True)
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
            await self._send_ctx(
                ctx,
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

        await self._send_ctx(ctx, "Starting timer...", ephemeral=False)
        try:
            result = await asyncio.to_thread(_send_timer_command, cmd)
        except Exception as exc:
            logger.exception("Error sending timer start command")
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, result["message"], ephemeral=False)

    @timer.command(name="stop")
    async def timer_stop(self, ctx: commands.Context):
        """Stop the currently running timer."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await self._send_ctx(
                ctx,
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        await self._send_ctx(ctx, "Stopping timer...", ephemeral=False)
        try:
            result = await asyncio.to_thread(_send_timer_command, {"type": "stop"})
        except Exception as exc:
            logger.exception("Error sending timer stop command")
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, result["message"], ephemeral=False)

    @timer.command(name="status")
    async def timer_status(self, ctx: commands.Context):
        """Check timer status directly from Firebase (fast, works even if local app is offline)."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await self._send_ctx(
                ctx,
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        try:
            msg = await asyncio.to_thread(_read_timer_status)
        except Exception as exc:
            logger.exception("Error reading timer status")
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, msg, ephemeral=False)

    @timer.command(name="projects")
    async def timer_projects(self, ctx: commands.Context):
        """List available projects."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await self._send_ctx(
                ctx,
                "You don't have timer access. Use `!timer auth <password>` first.",
                ephemeral=True,
            )
            return

        try:
            result = await asyncio.to_thread(_send_timer_command, {"type": "projects"})
        except Exception as exc:
            logger.exception("Error sending timer projects command")
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, result["message"], ephemeral=False)

    @timer.command(name="goals")
    @app_commands.describe(project="Filter goals by project name (optional)")
    async def timer_goals(self, ctx: commands.Context, project: str = ""):
        """List available goals, optionally filtered by project."""
        if not await self._check_configured(ctx):
            return
        if not self._is_authorized(ctx.author.id):
            await self._send_ctx(
                ctx,
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
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, result["message"], ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(TimerCog(bot))
