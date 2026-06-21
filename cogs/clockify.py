import asyncio
import logging
import time
from datetime import datetime, timezone

from discord import app_commands
from discord.ext import commands

from api_clients import clockify_client

logger = logging.getLogger(__name__)

# Per-user preferences (stored in the user_preferences table).
PREF_CLOCKIFY_API_KEY = "clockify_api_key"
PREF_CLOCKIFY_WORKSPACE_ID = "clockify_workspace_id"
PREF_CLOCKIFY_USER_ID = "clockify_user_id"
PREF_CLOCKIFY_ENABLED = "clockify_enabled"

# Projects rarely change, so cache them per-user. This lets autocomplete filter
# locally on every keystroke instead of calling Clockify each time, which would
# quickly exhaust the Free plan's 30 requests/hour/workspace budget.
PROJECT_CACHE_TTL_S = 300


def _format_duration(duration_sec: int) -> str:
    duration_sec = max(0, int(duration_sec))
    hours, remainder = divmod(duration_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class ClockifyCog(commands.Cog, name="Clockify"):
    """Per-user Clockify time-tracking integration (each user links their own key)."""

    def __init__(self, bot: commands.Bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager
        # user_id -> (expires_at_monotonic, [project dicts]); see PROJECT_CACHE_TTL_S.
        self._project_cache: dict = {}

    async def cog_load(self):
        # Make the /clockify group (and its subcommands) usable in DMs. For hybrid
        # groups, the top-level group object itself must be DM-enabled or Discord
        # hides the whole tree in DMs. See cogs/mood.py for the same approach.
        def _patch(cmd: app_commands.AppCommand) -> None:
            app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)(cmd)
            app_commands.allowed_installs(guilds=True, users=True)(cmd)

        try:
            hybrid_group = getattr(self, "clockify", None)
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
        logger.info("ClockifyCog loaded and DM contexts patched.")

    # --- helpers ------------------------------------------------------------

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

    async def _get_pref(self, user_id: int, key: str, default=None):
        return await asyncio.to_thread(self.db_manager.get_user_preference, user_id, key, default)

    async def _set_pref(self, user_id: int, key: str, value) -> None:
        await asyncio.to_thread(self.db_manager.set_user_preference, user_id, key, value)

    async def _del_pref(self, user_id: int, key: str) -> None:
        await asyncio.to_thread(self.db_manager.delete_user_preference, user_id, key)

    async def _check_db(self, ctx: commands.Context) -> bool:
        if not self.db_manager:
            await self._send_ctx(ctx, "Database is not available right now. Please try again later.", ephemeral=True)
            return False
        return True

    async def _require_enabled(self, ctx: commands.Context) -> bool:
        enabled = await self._get_pref(ctx.author.id, PREF_CLOCKIFY_ENABLED, False)
        if not enabled:
            await self._send_ctx(ctx, "Clockify is turned off. Enable it with `/clockify enable`.", ephemeral=True)
            return False
        return True

    async def _require_creds(self, ctx: commands.Context):
        """Return (api_key, workspace_id, user_id) or None (after sending a hint)."""
        uid = ctx.author.id
        api_key = await self._get_pref(uid, PREF_CLOCKIFY_API_KEY)
        workspace_id = await self._get_pref(uid, PREF_CLOCKIFY_WORKSPACE_ID)
        clockify_uid = await self._get_pref(uid, PREF_CLOCKIFY_USER_ID)
        if not (api_key and workspace_id and clockify_uid):
            await self._send_ctx(
                ctx,
                "You haven't linked Clockify yet. Run `/clockify config <api_key>` first.",
                ephemeral=True,
            )
            return None
        return api_key, workspace_id, clockify_uid

    async def _get_projects_cached(self, user_id: int, api_key: str, workspace_id: str):
        """
        Return the user's Clockify projects, cached for PROJECT_CACHE_TTL_S seconds.
        Returns a list on success or an error dict (errors are not cached).
        """
        now = time.monotonic()
        cached = self._project_cache.get(user_id)
        if cached and cached[0] > now:
            return cached[1]
        projects = await asyncio.to_thread(clockify_client.get_projects, api_key, workspace_id)
        if clockify_client.is_error(projects):
            return projects
        if not isinstance(projects, list):
            projects = []
        self._project_cache[user_id] = (now + PROJECT_CACHE_TTL_S, projects)
        return projects

    async def _project_autocomplete(self, interaction, current: str):
        """Suggest the user's Clockify project names, filtered locally from the cache."""
        try:
            if not self.db_manager:
                return []
            user_id = interaction.user.id
            api_key = await self._get_pref(user_id, PREF_CLOCKIFY_API_KEY)
            workspace_id = await self._get_pref(user_id, PREF_CLOCKIFY_WORKSPACE_ID)
            if not (api_key and workspace_id):
                return []
            projects = await self._get_projects_cached(user_id, api_key, workspace_id)
            if clockify_client.is_error(projects):
                return []
            current_lower = (current or "").lower()
            names = [str(p.get("name", "")).strip() for p in projects if p.get("name")]
            if current_lower:
                starts = [n for n in names if n.lower().startswith(current_lower)]
                contains = [n for n in names if current_lower in n.lower() and n not in starts]
                ordered = starts + contains
            else:
                ordered = names
            return [app_commands.Choice(name=n[:100], value=n[:100]) for n in ordered[:25]]
        except Exception:
            logger.warning("Clockify project autocomplete failed", exc_info=True)
            return []

    async def _resolve_project_id(self, user_id: int, api_key: str, workspace_id: str, name: str):
        """Resolve a project name to its id. Returns (project_id, error_message)."""
        projects = await self._get_projects_cached(user_id, api_key, workspace_id)
        if clockify_client.is_error(projects):
            return None, f"❌ {projects['message']}"
        if not isinstance(projects, list):
            return None, "❌ Couldn't load your Clockify projects."
        lower = name.lower()
        exact = [p for p in projects if str(p.get("name", "")).lower() == lower]
        partial = [p for p in projects if lower in str(p.get("name", "")).lower()]
        match = exact[0] if exact else (min(partial, key=lambda p: len(str(p.get("name", "")))) if partial else None)
        if not match:
            names = ", ".join(str(p.get("name", "")) for p in projects[:10] if p.get("name")) or "(none)"
            return None, f'❌ Project "{name}" not found. Available: {names}'
        return match.get("id"), None

    # --- commands -----------------------------------------------------------

    @commands.hybrid_group(
        name="clockify",
        invoke_without_command=True,
        description="Track time with your Clockify account.",
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def clockify(self, ctx: commands.Context):
        """Clockify commands. Use a subcommand: config, enable, start, stop, status."""
        await self._send_ctx(
            ctx,
            "**Clockify commands:**\n"
            "`/clockify config <api_key>` — link your account (DM the bot to keep the key private)\n"
            "`/clockify enable` / `/clockify disable` — turn tracking on/off\n"
            "`/clockify start [description] [project]` — start a timer\n"
            "`/clockify stop` — stop the running timer\n"
            "`/clockify status` — show what's running\n"
            "`/clockify unlink` — remove your saved key",
            ephemeral=True,
        )

    @clockify.command(name="config", description="Link your Clockify account by saving your API key.")
    @app_commands.describe(api_key="Your Clockify API key (Clockify → Preferences → Advanced → API).")
    async def clockify_config(self, ctx: commands.Context, *, api_key: str):
        if not await self._check_db(ctx):
            return

        # As a prefix command in a guild the key is typed in plain sight — delete it.
        leaked = getattr(ctx, "interaction", None) is None and getattr(ctx, "guild", None) is not None
        if leaked:
            try:
                await ctx.message.delete()
            except Exception:
                pass

        api_key = (api_key or "").strip()
        if not api_key:
            await self._send_ctx(ctx, "Provide your key: `/clockify config <api_key>`.", ephemeral=True)
            return

        user = await asyncio.to_thread(clockify_client.get_current_user, api_key)
        if clockify_client.is_error(user):
            await self._send_ctx(ctx, f"❌ Couldn't link Clockify: {user['message']}", ephemeral=True)
            return

        workspace_id = user.get("activeWorkspace") or user.get("defaultWorkspace")
        clockify_uid = user.get("id")
        if not (workspace_id and clockify_uid):
            await self._send_ctx(ctx, "❌ Clockify didn't return a workspace for this account.", ephemeral=True)
            return

        uid = ctx.author.id
        await self._set_pref(uid, PREF_CLOCKIFY_API_KEY, api_key)
        await self._set_pref(uid, PREF_CLOCKIFY_WORKSPACE_ID, workspace_id)
        await self._set_pref(uid, PREF_CLOCKIFY_USER_ID, clockify_uid)

        # Best-effort: show a friendly workspace name (one extra call, tolerate failure).
        workspace_label = workspace_id
        workspaces = await asyncio.to_thread(clockify_client.get_workspaces, api_key)
        if isinstance(workspaces, list):
            for ws in workspaces:
                if ws.get("id") == workspace_id:
                    workspace_label = ws.get("name") or workspace_id
                    break

        msg = (
            f"✅ Clockify linked for **{user.get('name', 'your account')}** "
            f"(workspace: **{workspace_label}**).\n"
            "Turn it on with `/clockify enable`, then `/clockify start`."
        )
        if leaked:
            msg += "\n⚠️ Your message held the key and was deleted. Next time use the slash command or DM the bot."
        await self._send_ctx(ctx, msg, ephemeral=True)

    @clockify.command(name="enable", description="Enable Clockify tracking commands.")
    async def clockify_enable(self, ctx: commands.Context):
        if not await self._check_db(ctx):
            return
        api_key = await self._get_pref(ctx.author.id, PREF_CLOCKIFY_API_KEY)
        if not api_key:
            await self._send_ctx(ctx, "Link your account first: `/clockify config <api_key>`.", ephemeral=True)
            return
        await self._set_pref(ctx.author.id, PREF_CLOCKIFY_ENABLED, True)
        await self._send_ctx(
            ctx,
            "✅ Clockify enabled. Use `/clockify start`, `/clockify status`, and `/clockify stop`.",
            ephemeral=True,
        )

    @clockify.command(name="disable", description="Disable Clockify tracking (keeps your saved key).")
    async def clockify_disable(self, ctx: commands.Context):
        if not await self._check_db(ctx):
            return
        await self._set_pref(ctx.author.id, PREF_CLOCKIFY_ENABLED, False)
        await self._send_ctx(
            ctx,
            "🛑 Clockify disabled. Your saved key is kept — use `/clockify unlink` to remove it.",
            ephemeral=True,
        )

    @clockify.command(name="unlink", description="Remove your saved Clockify key and settings.")
    async def clockify_unlink(self, ctx: commands.Context):
        if not await self._check_db(ctx):
            return
        uid = ctx.author.id
        for key in (
            PREF_CLOCKIFY_API_KEY,
            PREF_CLOCKIFY_WORKSPACE_ID,
            PREF_CLOCKIFY_USER_ID,
            PREF_CLOCKIFY_ENABLED,
        ):
            await self._del_pref(uid, key)
        self._project_cache.pop(uid, None)
        await self._send_ctx(ctx, "🗑️ Clockify account unlinked and key removed.", ephemeral=True)

    @clockify.command(name="start", description="Start a Clockify time entry.")
    @app_commands.describe(
        description="What you're working on",
        project="Optional project name (type to autocomplete)",
    )
    @app_commands.autocomplete(project=_project_autocomplete)
    async def clockify_start(self, ctx: commands.Context, *, description: str = "", project: str = ""):
        if not await self._check_db(ctx):
            return
        if not await self._require_enabled(ctx):
            return
        creds = await self._require_creds(ctx)
        if not creds:
            return
        api_key, workspace_id, clockify_uid = creds

        running = await asyncio.to_thread(clockify_client.get_in_progress, api_key, workspace_id, clockify_uid)
        if clockify_client.is_error(running):
            await self._send_ctx(ctx, f"❌ {running['message']}", ephemeral=False)
            return
        if running:
            current = running.get("description") or "(no description)"
            await self._send_ctx(
                ctx,
                f'⏱️ A timer is already running: "{current}". Stop it first with `/clockify stop`.',
                ephemeral=False,
            )
            return

        project = (project or "").strip()
        project_id = None
        if project:
            project_id, err = await self._resolve_project_id(ctx.author.id, api_key, workspace_id, project)
            if err:
                await self._send_ctx(ctx, err, ephemeral=False)
                return

        description = (description or "").strip() or "Discord timer"
        entry = await asyncio.to_thread(
            clockify_client.start_entry, api_key, workspace_id, description, project_id, False
        )
        if clockify_client.is_error(entry):
            await self._send_ctx(ctx, f"❌ {entry['message']}", ephemeral=False)
            return

        message = f'▶️ Started: "{description}"'
        if project:
            message += f" | Project: {project}"
        await self._send_ctx(ctx, message, ephemeral=False)

    @clockify.command(name="stop", description="Stop the currently running Clockify timer.")
    async def clockify_stop(self, ctx: commands.Context):
        if not await self._check_db(ctx):
            return
        if not await self._require_enabled(ctx):
            return
        creds = await self._require_creds(ctx)
        if not creds:
            return
        api_key, workspace_id, clockify_uid = creds

        entry = await asyncio.to_thread(clockify_client.stop_entry, api_key, workspace_id, clockify_uid)
        if clockify_client.is_error(entry):
            await self._send_ctx(ctx, f"❌ {entry['message']}", ephemeral=False)
            return
        if not entry:
            await self._send_ctx(ctx, "No timer is currently running.", ephemeral=False)
            return

        desc = entry.get("description") or "(no description)"
        interval = entry.get("timeInterval") or {}
        start = clockify_client.parse_iso(interval.get("start"))
        end = clockify_client.parse_iso(interval.get("end"))
        duration = _format_duration(int((end - start).total_seconds())) if start and end else "?"
        await self._send_ctx(ctx, f'⏹️ Stopped: "{desc}" | Duration: {duration}', ephemeral=False)

    @clockify.command(name="status", description="Show the currently running Clockify timer.")
    async def clockify_status(self, ctx: commands.Context):
        if not await self._check_db(ctx):
            return
        if not await self._require_enabled(ctx):
            return
        creds = await self._require_creds(ctx)
        if not creds:
            return
        api_key, workspace_id, clockify_uid = creds

        running = await asyncio.to_thread(clockify_client.get_in_progress, api_key, workspace_id, clockify_uid)
        if clockify_client.is_error(running):
            await self._send_ctx(ctx, f"❌ {running['message']}", ephemeral=False)
            return
        if not running:
            await self._send_ctx(ctx, "No timer running.", ephemeral=False)
            return

        desc = running.get("description") or "(no description)"
        start = clockify_client.parse_iso((running.get("timeInterval") or {}).get("start"))
        elapsed = (
            _format_duration(int((datetime.now(timezone.utc) - start).total_seconds()))
            if start
            else "?"
        )
        await self._send_ctx(ctx, f'⏱️ Running: "{desc}" | {elapsed}', ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClockifyCog(bot, db_manager=getattr(bot, "db_manager", None)))
    logger.info("ClockifyCog has been loaded.")
