import asyncio
from datetime import datetime, timezone
import logging
import re
import time

import requests
from discord import app_commands
from discord.ext import commands

import config

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_S = 10


def _missing_firebase_config() -> list[str]:
    """Return which timer-related env vars are missing."""
    missing: list[str] = []
    if not (config.FIREBASE_DATABASE_URL or "").strip():
        missing.append("FIREBASE_DATABASE_URL")
    if not (config.FIREBASE_DATABASE_SECRET or "").strip():
        missing.append("FIREBASE_DATABASE_SECRET")
    if int(getattr(config, "TIMER_OWNER_ID", 0) or 0) <= 0:
        missing.append("TIMER_OWNER_ID")
    return missing


def _fb_url(path: str) -> str:
    base = config.FIREBASE_DATABASE_URL.rstrip("/")
    return f"{base}/{path}.json?auth={config.FIREBASE_DATABASE_SECRET}"


def _fb_get(path: str):
    response = requests.get(_fb_url(path), timeout=_REQUEST_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def _fb_put(path: str, data: dict):
    response = requests.put(_fb_url(path), json=data, timeout=_REQUEST_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def _fb_post(path: str, data: dict):
    response = requests.post(_fb_url(path), json=data, timeout=_REQUEST_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def _read_timer_state() -> dict | None:
    """Read raw timer state from Firebase."""
    state = _fb_get("discord-sync/timer-state")
    return state if isinstance(state, dict) else None


def _get_projects() -> list[dict]:
    projects = _fb_get("discord-sync/projects")
    return projects if isinstance(projects, list) else []


def _get_goals() -> list[dict]:
    goals = _fb_get("discord-sync/goals")
    return goals if isinstance(goals, list) else []


def _resolve_project(name: str | None) -> dict | None:
    if not name:
        return None
    lower = name.lower()
    projects = _get_projects()
    for project in projects:
        project_name = str(project.get("name", "")).lower()
        if project_name == lower:
            return project
    matches = [project for project in projects if lower in str(project.get("name", "")).lower()]
    matches.sort(key=lambda project: len(str(project.get("name", ""))))
    return matches[0] if matches else None


def _resolve_goal(name: str | None, project_id: int | None = None) -> dict | None:
    if not name:
        return None
    lower = name.lower()
    goals = _get_goals()
    if project_id is not None:
        goals = [goal for goal in goals if goal.get("projectId") == project_id]
    for goal in goals:
        description = str(goal.get("description", "")).lower()
        if description == lower:
            return goal
    matches = [goal for goal in goals if lower in str(goal.get("description", "")).lower()]
    matches.sort(key=lambda goal: len(str(goal.get("description", ""))))
    return matches[0] if matches else None


def _format_duration(duration_sec: int) -> str:
    hours, remainder = divmod(duration_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def _format_timer_status_message(state: dict | None) -> str:
    if not state or not state.get("active"):
        return "No timer running."

    elapsed_sec = int((time.time() * 1000 - state["startTime"]) / 1000)
    msg = f'Timer running: "{state.get("description", "?")}" | {_format_duration(elapsed_sec)}'
    if state.get("projectName"):
        msg += f" | Project: {state['projectName']}"
    if state.get("goalName"):
        msg += f" | Goal: {state['goalName']}"
    return msg


def _read_timer_status() -> str:
    return _format_timer_status_message(_read_timer_state())


def _list_projects_message() -> str:
    projects = _get_projects()
    if not projects:
        return "No projects found. (Run the local app at least once to sync.)"
    names = ", ".join(str(project.get("name", "")) for project in projects[:10] if project.get("name"))
    return f"Available projects: {names}" if names else "No projects found. (Run the local app at least once to sync.)"


def _list_goals_message(project_name: str | None = None) -> str:
    goals = _get_goals()
    if project_name:
        project = _resolve_project(project_name)
        if project:
            goals = [goal for goal in goals if goal.get("projectId") == project.get("id")]
    if not goals:
        return "No goals found. (Run the local app at least once to sync.)"
    names = ", ".join(str(goal.get("description", "")) for goal in goals[:15] if goal.get("description"))
    return f"Available goals: {names}" if names else "No goals found. (Run the local app at least once to sync.)"


def parse_start_args(args_str: str) -> dict[str, str | None]:
    """Parse freeform timer args into description, project, and goal parts."""
    project = None
    goal = None

    project_match = re.search(r"\bproject:(.+?)(?=\bgoal:|$)", args_str, re.IGNORECASE)
    goal_match = re.search(r"\bgoal:(.+?)(?=\bproject:|$)", args_str, re.IGNORECASE)

    cut_start = len(args_str)
    if project_match:
        project = project_match.group(1).strip()
        cut_start = min(cut_start, project_match.start())
    if goal_match:
        goal = goal_match.group(1).strip()
        cut_start = min(cut_start, goal_match.start())

    description = args_str[:cut_start].strip() or None
    return {"description": description, "project": project, "goal": goal}


def _build_available_names(items: list[dict], key: str, limit: int = 10) -> str:
    names = [str(item.get(key, "")) for item in items if item.get(key)]
    names = names[:limit]
    return ", ".join(names) if names else "(none)"


def _timer_start(description: str = "Discord timer", project_name: str | None = None, goal_name: str | None = None) -> tuple[bool, str]:
    state = _read_timer_state()
    if state and state.get("active"):
        current_description = state.get("description", "?")
        return False, f'Timer already running: "{current_description}". Stop it first.'

    project = _resolve_project(project_name)
    if project_name and not project:
        return False, f'Project "{project_name}" not found. Available: {_build_available_names(_get_projects(), "name")}'

    goal = _resolve_goal(goal_name, project.get("id") if project else None)
    if goal_name and not goal:
        goals = _get_goals()
        if project:
            goals = [item for item in goals if item.get("projectId") == project.get("id")]
        return False, f'Goal "{goal_name}" not found. Available: {_build_available_names(goals, "description")}'

    now_ms = int(time.time() * 1000)
    session_id = f"discord-{now_ms}"
    payload = {
        "active": True,
        "description": description,
        "projectName": project.get("name") if project else None,
        "goalName": goal.get("description") if goal else None,
        "projectId": project.get("id") if project else None,
        "goalId": goal.get("id") if goal else None,
        "startTime": now_ms,
        "sessionId": session_id,
        "origin": "discord",
        "lastUpdated": now_ms,
    }
    _fb_put("discord-sync/timer-state", payload)

    message = f'Timer started: "{description}"'
    if payload["projectName"]:
        message += f" | Project: {payload['projectName']}"
    if payload["goalName"]:
        message += f" | Goal: {payload['goalName']}"
    return True, message


def _timer_stop() -> tuple[bool, str]:
    state = _read_timer_state()
    if not state or not state.get("active"):
        return False, "No timer is currently running."

    now_ms = int(time.time() * 1000)
    start_ms = int(state["startTime"])
    duration_sec = round((now_ms - start_ms) / 1000)
    start_iso = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()

    _fb_post(
        "discord-sync/pending-entries",
        {
            "description": state.get("description", "Discord timer"),
            "startTime": start_iso,
            "endTime": end_iso,
            "duration": duration_sec,
            "projectId": state.get("projectId"),
            "goalId": state.get("goalId"),
            "sessionId": state.get("sessionId", ""),
            "createdAt": now_ms,
        },
    )
    _fb_put("discord-sync/timer-state", {"active": False, "lastUpdated": now_ms})

    message = f'Timer stopped: "{state.get("description", "?")}" | Duration: {_format_duration(duration_sec)}'
    if state.get("goalName"):
        message += f' | Logged to goal "{state["goalName"]}"'
    return True, message


class TimerCog(commands.Cog, name="Timer"):
    """Owner-only timer commands backed directly by Firebase."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
        """Return True if timer env vars are set; otherwise send an error and return False."""
        missing = _missing_firebase_config()
        if missing:
            await self._send_ctx(
                ctx,
                "Timer is not configured. Missing: **"
                + "**, **".join(missing)
                + "**. "
                "They must be present in the bot environment before timer commands can run.",
                ephemeral=True,
            )
            return False
        return True

    async def _check_owner_access(self, ctx: commands.Context) -> bool:
        if ctx.author.id == int(getattr(config, "TIMER_OWNER_ID", 0) or 0):
            return True
        await self._send_ctx(ctx, "You don't have access to timer commands.", ephemeral=True)
        return False

    @commands.hybrid_group(
        name="timer",
        invoke_without_command=True,
        description="Owner-only timer commands backed by Firebase.",
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def timer(self, ctx: commands.Context):
        """Timer commands. Use a subcommand: start, stop, status, projects, goals."""
        await self._send_ctx(
            ctx,
            "**Timer commands:**\n"
            "`!timer start [description] [project:<name>] [goal:<name>]`\n"
            "`!timer stop`\n"
            "`!timer status`\n"
            "`!timer projects`\n"
            "`!timer goals [project:<name>]`",
            ephemeral=True,
        )

    @timer.command(name="start")
    @app_commands.describe(
        args="Format: description project:<name> goal:<name>",
    )
    async def timer_start(self, ctx: commands.Context, *, args: str = ""):
        """Start a timer with optional description, project, and goal tags."""
        if not await self._check_configured(ctx):
            return
        if not await self._check_owner_access(ctx):
            return

        parsed = parse_start_args(args)
        try:
            _, message = await asyncio.to_thread(
                _timer_start,
                parsed["description"] or "Discord timer",
                parsed["project"],
                parsed["goal"],
            )
        except Exception as exc:
            logger.exception("Error sending timer start command")
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, message, ephemeral=False)

    @timer.command(name="stop")
    async def timer_stop(self, ctx: commands.Context):
        """Stop the currently running timer."""
        if not await self._check_configured(ctx):
            return
        if not await self._check_owner_access(ctx):
            return

        try:
            _, message = await asyncio.to_thread(_timer_stop)
        except Exception as exc:
            logger.exception("Error sending timer stop command")
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, message, ephemeral=False)

    @timer.command(name="status")
    async def timer_status(self, ctx: commands.Context):
        """Check timer status directly from Firebase."""
        if not await self._check_configured(ctx):
            return
        if not await self._check_owner_access(ctx):
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
        if not await self._check_owner_access(ctx):
            return

        try:
            message = await asyncio.to_thread(_list_projects_message)
        except Exception as exc:
            logger.exception("Error sending timer projects command")
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, message, ephemeral=False)

    @timer.command(name="goals")
    @app_commands.describe(args="Optional project filter, e.g. project:Web App")
    async def timer_goals(self, ctx: commands.Context, *, args: str = ""):
        """List available goals, optionally filtered by project."""
        if not await self._check_configured(ctx):
            return
        if not await self._check_owner_access(ctx):
            return

        parsed = parse_start_args(args)
        project_name = parsed["project"] or (args.strip() if args.strip() and not parsed["goal"] else None)
        try:
            message = await asyncio.to_thread(_list_goals_message, project_name)
        except Exception as exc:
            logger.exception("Error sending timer goals command")
            await self._send_ctx(ctx, f"Error communicating with Firebase: {exc}", ephemeral=False)
            return
        await self._send_ctx(ctx, message, ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(TimerCog(bot))
