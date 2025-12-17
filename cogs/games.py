import logging
from functools import partial
from typing import List, Optional

import discord
from discord.ext import commands

from api_clients import pcgamingwiki_client, steam_client, wikipedia_client

logger = logging.getLogger(__name__)

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


class GameSelectionView(discord.ui.View):
    def __init__(self, ctx: commands.Context, results: List[dict], timeout: int = 60):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.results = results
        self.selected_result: Optional[dict] = None
        self.message: Optional[discord.Message] = None

        for i, _ in enumerate(results[:5]):
            self.add_item(GameSelectionButton(i, NUMBER_EMOJIS[i]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This isn't for you!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class GameSelectionButton(discord.ui.Button):
    def __init__(self, index: int, emoji: str):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=str(index + 1),
            emoji=emoji,
            custom_id=f"game_select_{index}",
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: GameSelectionView = self.view  # type: ignore[assignment]
        view.selected_result = view.results[self.index]
        await interaction.response.defer()
        view.stop()


class GamesCog(commands.Cog, name="Games"):
    """Track games you want to play + look up game info (Steam first, no API keys)."""

    def __init__(self, bot: commands.Bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager

    @staticmethod
    def _is_dm_ctx(ctx: commands.Context) -> bool:
        return ctx.guild is None

    async def _defer_if_interaction(self, ctx: commands.Context, *, ephemeral: bool = True) -> None:
        if not getattr(ctx, "interaction", None):
            return
        try:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer(ephemeral=ephemeral)
        except discord.HTTPException:
            pass
        except discord.InteractionResponded:
            pass

    async def _send(
        self,
        ctx: commands.Context,
        content: Optional[str] = None,
        *,
        embed: Optional[discord.Embed] = None,
        ephemeral: bool = True,
        view: Optional[discord.ui.View] = None,
        wait: bool = False,
    ):
        base_kwargs = {}
        if content is not None:
            base_kwargs["content"] = content
        if embed is not None:
            base_kwargs["embed"] = embed
        if view is not None:
            base_kwargs["view"] = view

        interaction = getattr(ctx, "interaction", None)
        if interaction:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(**base_kwargs, ephemeral=ephemeral)
                    if wait:
                        try:
                            return await interaction.original_response()
                        except discord.HTTPException:
                            return None
                    return None
            except (discord.InteractionResponded, discord.HTTPException):
                pass

            if wait:
                return await interaction.followup.send(**base_kwargs, ephemeral=ephemeral, wait=True)
            return await interaction.followup.send(**base_kwargs, ephemeral=ephemeral)

        return await ctx.send(**base_kwargs)

    @staticmethod
    def _status_label(status: str) -> str:
        s = (status or "").strip().lower()
        mapping = {
            "backlog": "🗒️ backlog",
            "playing": "🎮 playing",
            "paused": "⏸️ paused",
            "completed": "✅ completed",
            "dropped": "🗑️ dropped",
        }
        return mapping.get(s, s or "unknown")

    def _game_embed(self, item: dict, *, title: str) -> discord.Embed:
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        embed.description = f"**{item.get('title', 'Untitled')}**"

        embed.add_field(name="Status", value=self._status_label(str(item.get("status") or "backlog")), inline=True)
        if item.get("platform"):
            embed.add_field(name="Platform", value=str(item.get("platform")), inline=True)
        if item.get("release_date"):
            embed.add_field(name="Release", value=str(item.get("release_date")), inline=True)

        links: List[str] = []
        if item.get("steam_url"):
            links.append(f"[Steam]({item['steam_url']})")
        if links:
            embed.add_field(name="Links", value=" ".join(links), inline=False)

        genres = item.get("genres")
        if isinstance(genres, list) and genres:
            g = [str(x) for x in genres if isinstance(x, str) and x.strip()]
            if g:
                embed.add_field(name="Genres", value=", ".join(g[:10]), inline=False)

        if item.get("developer"):
            embed.add_field(name="Developer", value=str(item.get("developer")), inline=True)
        if item.get("publisher"):
            embed.add_field(name="Publisher", value=str(item.get("publisher")), inline=True)

        if item.get("notes"):
            note = str(item.get("notes"))
            embed.add_field(name="Notes", value=note[:1000], inline=False)

        if isinstance(item.get("cover_url"), str) and item["cover_url"].strip():
            embed.set_thumbnail(url=item["cover_url"].strip())

        if item.get("id") is not None:
            embed.set_footer(text=f"Game ID: #{item.get('id')}")
        return embed

    async def _steam_pick(self, ctx: commands.Context, query: str, *, is_dm: bool) -> Optional[dict]:
        try:
            results = await self.bot.loop.run_in_executor(None, partial(steam_client.search_store, query, limit=10))
        except Exception:
            return None

        if not results:
            return None

        # Prefer exact-ish match
        ql = query.strip().lower()
        exact = [r for r in results if isinstance(r.get("name"), str) and r["name"].lower() == ql]
        if len(exact) == 1:
            return exact[0]

        if len(results) == 1:
            return results[0]

        display = results[:5]
        embed_pick = discord.Embed(title="Which Steam game?", color=discord.Color.blurple())
        lines = []
        for i, r in enumerate(display):
            nm = r.get("name") or "Unknown"
            typ = r.get("type")
            tag = f" ({typ})" if isinstance(typ, str) and typ else ""
            lines.append(f"{NUMBER_EMOJIS[i]} **{nm}**{tag}")
        embed_pick.description = "\n".join(lines)

        view = GameSelectionView(ctx, display)
        msg = await self._send(ctx, embed=embed_pick, ephemeral=not is_dm, view=view, wait=True)
        view.message = msg
        await view.wait()
        return view.selected_result

    @commands.hybrid_group(name="games", aliases=["game"], description="Track your games backlog / now playing.")
    async def games_group(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self.games_now(ctx)

    @games_group.command(name="add", description="Add a game to your list (Steam auto-lookup if possible).")
    @discord.app_commands.describe(
        title="Game title",
        platform="Platform (PC/PS5/Switch/etc.)",
        status="backlog|playing|paused|completed|dropped",
        notes="Optional notes",
        steam_lookup="Try to match the title on Steam",
    )
    async def games_add(
        self,
        ctx: commands.Context,
        title: str,
        platform: Optional[str] = None,
        status: str = "backlog",
        notes: Optional[str] = None,
        steam_lookup: bool = True,
    ):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        title_in = (title or "").strip()
        if len(title_in) < 2:
            await self._send(ctx, "Provide a longer title.", ephemeral=not is_dm)
            return

        steam_match = None
        steam_details = None
        if steam_lookup:
            steam_match = await self._steam_pick(ctx, title_in, is_dm=is_dm)
            if steam_match and isinstance(steam_match.get("appid"), int):
                try:
                    steam_details = await self.bot.loop.run_in_executor(
                        None, partial(steam_client.get_app_details, int(steam_match["appid"]))
                    )
                except Exception:
                    steam_details = None

        # Fill fields
        final_title = title_in
        steam_appid = None
        steam_url = None
        cover_url = None
        release_date = None
        genres = None
        developer = None
        publisher = None

        if steam_details:
            final_title = steam_details.get("name") or final_title
            steam_appid = steam_details.get("appid")
            steam_url = steam_details.get("steam_url")
            cover_url = steam_details.get("header_image")
            release_date = steam_details.get("release_date")
            genres = steam_details.get("genres")
            developer = steam_details.get("developer")
            publisher = steam_details.get("publisher")
        elif steam_match:
            final_title = steam_match.get("name") or final_title
            steam_appid = steam_match.get("appid")
            steam_url = steam_match.get("steam_url")
            cover_url = steam_match.get("tiny_image")

        item_id = await self.bot.loop.run_in_executor(
            None,
            partial(
                self.db_manager.create_game_item,
                ctx.author.id,
                final_title,
                status=status,
                platform=platform,
                steam_appid=steam_appid,
                steam_url=steam_url,
                cover_url=cover_url,
                release_date=release_date,
                genres=genres,
                developer=developer,
                publisher=publisher,
                notes=notes,
            ),
        )

        if not item_id:
            await self._send(ctx, "❌ Could not create that game entry.", ephemeral=not is_dm)
            return

        await self.bot.loop.run_in_executor(None, self.db_manager.set_current_game_item_id, ctx.author.id, int(item_id))
        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_game_item, ctx.author.id, int(item_id))
        if not item:
            await self._send(ctx, "✅ Added game.", ephemeral=not is_dm)
            return

        embed = self._game_embed(item, title="✅ Game added")
        embed.set_footer(text="Tip: /games status playing  |  /games note ...")
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    @games_group.command(name="now", description="Show your current game.")
    async def games_now(self, ctx: commands.Context):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_game_item, ctx.author.id)
        if not item:
            await self._send(ctx, "You’re not tracking any games yet. Use `/games add`.", ephemeral=not is_dm)
            return

        await self._send(ctx, embed=self._game_embed(item, title="🎮 Current game"), ephemeral=not is_dm)

    @games_group.command(name="list", description="List your games (default: backlog/playing/paused).")
    @discord.app_commands.describe(statuses="Comma-separated statuses (backlog,playing,paused,completed,dropped)")
    async def games_list(self, ctx: commands.Context, statuses: Optional[str] = None):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        st = None
        if isinstance(statuses, str) and statuses.strip():
            st = [s.strip().lower() for s in statuses.split(",") if s.strip()]

        items = await self.bot.loop.run_in_executor(None, partial(self.db_manager.list_game_items, ctx.author.id, st, 25))
        if not items:
            await self._send(ctx, "No games found for that filter.", ephemeral=not is_dm)
            return

        current_id = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_game_item_id, ctx.author.id)

        embed = discord.Embed(title=f"🎮 Your games ({len(items)})", color=discord.Color.blurple())
        lines: List[str] = []
        for it in items[:25]:
            try:
                iid = int(it.get("id"))
            except Exception:
                iid = None
            marker = "👉 " if (current_id is not None and iid is not None and int(current_id) == int(iid)) else ""
            platform_s = f" — {it.get('platform')}" if it.get("platform") else ""
            st_label = self._status_label(str(it.get("status") or "backlog"))
            link = f" [Steam]({it.get('steam_url')})" if it.get("steam_url") else ""
            lines.append(f"- {marker}**#{iid}**: **{it.get('title','Untitled')}**{platform_s} ({st_label}){link}")

        embed.description = "\n".join(lines)
        embed.set_footer(text="Tip: /games switch_to  |  /games remove <id>")
        await self._send(ctx, embed=embed, ephemeral=not is_dm)

    async def active_game_item_autocomplete(self, interaction: discord.Interaction, current: str) -> List[discord.app_commands.Choice[str]]:
        if not self.db_manager:
            return []
        try:
            uid = interaction.user.id
        except Exception:
            return []

        try:
            items = await self.bot.loop.run_in_executor(None, self.db_manager.list_game_items, uid, ["backlog", "playing", "paused"], 25)
        except Exception:
            return []

        q = (current or "").strip().lower()
        choices: List[discord.app_commands.Choice[str]] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            try:
                item_id = int(it.get("id"))
            except Exception:
                continue
            title = str(it.get("title") or "Untitled")
            platform = str(it.get("platform") or "").strip()
            label = f"{title}" + (f" — {platform}" if platform else "") + f" (#{item_id})"
            if q and (q not in title.lower()) and (q not in platform.lower()) and (q not in label.lower()):
                continue
            choices.append(discord.app_commands.Choice(name=label[:100], value=str(item_id)))
        return choices[:25]

    @games_group.command(name="switch_to", description="Switch your current game (autocomplete).")
    @discord.app_commands.describe(item="Pick one of your active games")
    @discord.app_commands.autocomplete(item=active_game_item_autocomplete)
    async def games_switch_to(self, ctx: commands.Context, item: str):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        try:
            item_id = int(str(item).strip())
        except Exception:
            await self._send(ctx, "Pick an item from the autocomplete list.", ephemeral=not is_dm)
            return

        it = await self.bot.loop.run_in_executor(None, self.db_manager.get_game_item, ctx.author.id, item_id)
        if not it:
            await self._send(ctx, f"❌ I can’t find game **#{item_id}**.", ephemeral=not is_dm)
            return

        await self.bot.loop.run_in_executor(None, self.db_manager.set_current_game_item_id, ctx.author.id, item_id)
        await self.games_now(ctx)

    @games_group.command(name="status", description="Set status for your current game (or a specific id).")
    @discord.app_commands.describe(status="backlog|playing|paused|completed|dropped", item_id="Optional game id")
    async def games_status(self, ctx: commands.Context, status: str, item_id: Optional[int] = None):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        gid = item_id
        if gid is None:
            gid = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_game_item_id, ctx.author.id)
        if gid is None:
            await self._send(ctx, "No current game. Use `/games add` or `/games switch_to`.", ephemeral=not is_dm)
            return

        ok = await self.bot.loop.run_in_executor(None, self.db_manager.update_game_status, ctx.author.id, int(gid), status)
        if not ok:
            await self._send(ctx, "❌ Could not update status. Use: backlog|playing|paused|completed|dropped", ephemeral=not is_dm)
            return

        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_game_item, ctx.author.id, int(gid))
        if item:
            await self._send(ctx, embed=self._game_embed(item, title="✅ Status updated"), ephemeral=not is_dm)
        else:
            await self._send(ctx, "✅ Status updated.", ephemeral=not is_dm)

    @games_group.command(name="note", description="Set notes for your current game (or a specific id).")
    @discord.app_commands.describe(notes="Notes text", item_id="Optional game id")
    async def games_note(self, ctx: commands.Context, notes: str, item_id: Optional[int] = None):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        gid = item_id
        if gid is None:
            gid = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_game_item_id, ctx.author.id)
        if gid is None:
            await self._send(ctx, "No current game. Use `/games add` or `/games switch_to`.", ephemeral=not is_dm)
            return

        ok = await self.bot.loop.run_in_executor(None, self.db_manager.update_game_notes, ctx.author.id, int(gid), notes)
        if not ok:
            await self._send(ctx, "❌ Could not save notes.", ephemeral=not is_dm)
            return

        item = await self.bot.loop.run_in_executor(None, self.db_manager.get_game_item, ctx.author.id, int(gid))
        if item:
            await self._send(ctx, embed=self._game_embed(item, title="📝 Notes updated"), ephemeral=not is_dm)
        else:
            await self._send(ctx, "📝 Notes updated.", ephemeral=not is_dm)

    @games_group.command(name="remove", description="Remove a game from your list.")
    @discord.app_commands.describe(item_id="Game id to remove")
    async def games_remove(self, ctx: commands.Context, item_id: int):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self._send(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        # If removing current, clear pointer
        cur = await self.bot.loop.run_in_executor(None, self.db_manager.get_current_game_item_id, ctx.author.id)
        ok = await self.bot.loop.run_in_executor(None, self.db_manager.delete_game_item, ctx.author.id, int(item_id))
        if not ok:
            await self._send(ctx, f"❌ I can’t find game **#{item_id}**.", ephemeral=not is_dm)
            return

        if cur is not None and int(cur) == int(item_id):
            await self.bot.loop.run_in_executor(None, self.db_manager.set_current_game_item_id, ctx.author.id, None)

        await self._send(ctx, f"✅ Removed game **#{item_id}**.", ephemeral=not is_dm)

    @commands.hybrid_command(name="game_info", description="Find info on a game (Steam first, with Wikipedia/PCGamingWiki fallback).")
    @discord.app_commands.describe(query="Game title")
    async def game_info(self, ctx: commands.Context, *, query: str):
        is_dm = self._is_dm_ctx(ctx)
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)

        q = (query or "").strip()
        if len(q) < 2:
            await self._send(ctx, "Provide a longer query.", ephemeral=not is_dm)
            return

        steam_match = await self._steam_pick(ctx, q, is_dm=is_dm)
        steam_details = None
        if steam_match and isinstance(steam_match.get("appid"), int):
            try:
                steam_details = await self.bot.loop.run_in_executor(None, partial(steam_client.get_app_details, int(steam_match["appid"])))
            except Exception:
                steam_details = None

        wiki_summary = None
        pcgw = None

        # If Steam fails, try fallbacks.
        if not steam_details:
            try:
                wiki_summary = await self.bot.loop.run_in_executor(None, partial(wikipedia_client.lookup, q, limit=5))
            except Exception:
                wiki_summary = None
            try:
                pcgw = await self.bot.loop.run_in_executor(None, partial(pcgamingwiki_client.lookup, q, limit=5))
            except Exception:
                pcgw = None

        if steam_details:
            embed = discord.Embed(
                title=f"🎮 {steam_details.get('name', 'Game')}",
                description=(steam_details.get("short_description") or "")[:1000] or None,
                color=discord.Color.green(),
                url=steam_details.get("steam_url"),
            )
            if steam_details.get("header_image"):
                embed.set_thumbnail(url=steam_details["header_image"])

            if steam_details.get("release_date"):
                embed.add_field(name="Release", value=str(steam_details["release_date"]), inline=True)
            if steam_details.get("developer"):
                embed.add_field(name="Developer", value=str(steam_details["developer"]), inline=True)
            if steam_details.get("publisher"):
                embed.add_field(name="Publisher", value=str(steam_details["publisher"]), inline=True)

            genres = steam_details.get("genres")
            if isinstance(genres, list) and genres:
                embed.add_field(name="Genres", value=", ".join(genres[:10]), inline=False)

            mc = steam_details.get("metacritic_score")
            if isinstance(mc, int):
                embed.add_field(name="Metacritic", value=str(mc), inline=True)

            plats = steam_details.get("platforms")
            if isinstance(plats, list) and plats:
                embed.add_field(name="Platforms", value=", ".join(plats), inline=True)

            embed.set_footer(text="Source: Steam (public store endpoints)")
            await self._send(ctx, embed=embed, ephemeral=not is_dm)
            return

        # Fallback embed
        if wiki_summary:
            embed = discord.Embed(
                title=f"🧠 {wiki_summary.get('title', 'Wikipedia')}",
                description=(wiki_summary.get("extract") or "")[:1200] or "No summary available.",
                color=discord.Color.blurple(),
                url=wiki_summary.get("url"),
            )
            if wiki_summary.get("thumbnail"):
                embed.set_thumbnail(url=wiki_summary["thumbnail"])
            links = []
            if wiki_summary.get("url"):
                links.append(f"[Wikipedia]({wiki_summary['url']})")
            if pcgw and pcgw.get("url"):
                links.append(f"[PCGamingWiki]({pcgw['url']})")
            if links:
                embed.add_field(name="Links", value=" ".join(links), inline=False)
            embed.set_footer(text="Source: Wikipedia (fallback)")
            await self._send(ctx, embed=embed, ephemeral=not is_dm)
            return

        if pcgw and pcgw.get("url"):
            embed = discord.Embed(
                title=f"🛠️ {pcgw.get('title', 'PCGamingWiki')}",
                description="PCGamingWiki page found.",
                color=discord.Color.dark_teal(),
                url=pcgw.get("url"),
            )
            embed.set_footer(text="Source: PCGamingWiki (fallback)")
            await self._send(ctx, embed=embed, ephemeral=not is_dm)
            return

        await self._send(ctx, "No results found on Steam/Wikipedia/PCGamingWiki.", ephemeral=not is_dm)


async def setup(bot: commands.Bot):
    await bot.add_cog(GamesCog(bot, db_manager=bot.db_manager))
    logger.info("GamesCog has been loaded.")


