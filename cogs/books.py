import asyncio
import logging
from datetime import datetime, time
from functools import partial
from typing import List, Optional

import discord
from discord.ext import commands, tasks

from api_clients import openlibrary_client

logger = logging.getLogger(__name__)

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


class AuthorSelectionView(discord.ui.View):
    def __init__(self, ctx: commands.Context, results: List[dict], timeout: int = 60):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.results = results
        self.selected_result: Optional[dict] = None
        self.message: Optional[discord.Message] = None

        for i, _ in enumerate(results[:5]):
            self.add_item(AuthorSelectionButton(i, NUMBER_EMOJIS[i]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This isn't for you!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        # Best-effort: update the message to reflect disabled buttons.
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class AuthorSelectionButton(discord.ui.Button):
    def __init__(self, index: int, emoji: str):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=str(index + 1),
            emoji=emoji,
            custom_id=f"book_author_select_{index}",
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: AuthorSelectionView = self.view  # type: ignore[assignment]
        view.selected_result = view.results[self.index]
        await interaction.response.defer()
        view.stop()


class BooksCog(commands.Cog, name="Books"):
    """
    Subscribe to book authors and get notified when new works appear in Open Library.

    Notes:
    - Open Library doesn't guarantee "future release" metadata for all titles. As a practical
      implementation, we notify when a new work shows up under an author's works list compared to
      what the bot has already seen.
    """

    def __init__(self, bot: commands.Bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager
        logger.info("BooksCog: Initializing and starting check_new_books task.")
        self.check_new_books.start()

    def cog_unload(self):
        logger.info("BooksCog: Unloading and cancelling check_new_books task.")
        self.check_new_books.cancel()

    async def _defer_if_interaction(self, ctx: commands.Context, *, ephemeral: bool = True) -> None:
        """
        Hybrid commands can be invoked as prefix commands too. Only defer when we have an interaction.
        """
        if not getattr(ctx, "interaction", None):
            return
        try:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer(ephemeral=ephemeral)
        except discord.InteractionResponded:
            pass
        except discord.HTTPException:
            pass

    @staticmethod
    def _scope_guild_id_from_ctx(ctx: commands.Context) -> int:
        """
        Returns a stable "scope guild id" used by the DB layer.

        - In a server: actual guild id
        - In DMs: 0 (personal/global scope)
        """
        return ctx.guild.id if ctx.guild else 0

    async def _is_user_in_dnd(self, user_id: int) -> bool:
        """
        Best-effort DND check. If preferences cannot be loaded/parsed, treat as not in DND.
        """
        try:
            dnd_enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_enabled", False)
            if not dnd_enabled:
                return False

            dnd_start_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_start_time", "00:00")
            dnd_end_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_end_time", "00:00")
            try:
                dnd_start_time_obj = datetime.strptime(dnd_start_str, "%H:%M").time()
                dnd_end_time_obj = datetime.strptime(dnd_end_str, "%H:%M").time()
            except ValueError:
                dnd_start_time_obj = time(0, 0)
                dnd_end_time_obj = time(0, 0)

            now_t = datetime.now().time()
            if dnd_start_time_obj <= dnd_end_time_obj:
                return dnd_start_time_obj <= now_t <= dnd_end_time_obj
            return now_t >= dnd_start_time_obj or now_t <= dnd_end_time_obj
        except Exception:
            return False

    async def send_response(self, ctx: commands.Context, content: Optional[str] = None, *, embed: Optional[discord.Embed] = None, ephemeral: bool = True, view: Optional[discord.ui.View] = None, wait: bool = False):
        if ctx.interaction:
            kwargs = {"ephemeral": ephemeral}
            if wait:
                kwargs["wait"] = wait
            if view is not None:
                kwargs["view"] = view
            if embed is not None:
                kwargs["embed"] = embed
            if content is not None:
                kwargs["content"] = content

            # If we haven't responded/deferred yet, use the initial response.
            try:
                if not ctx.interaction.response.is_done():
                    return await ctx.interaction.response.send_message(**kwargs)
            except discord.HTTPException:
                pass
            return await ctx.interaction.followup.send(**kwargs)
        return await ctx.send(content=content, embed=embed, view=view)

    async def author_autocomplete(self, interaction: discord.Interaction, current: str) -> List[discord.app_commands.Choice[str]]:
        if not current or len(current.strip()) < 2:
            return []
        try:
            results = await self.bot.loop.run_in_executor(None, partial(openlibrary_client.search_authors, current, limit=10))
        except Exception:
            return []

        choices: List[discord.app_commands.Choice[str]] = []
        for a in results[:25]:
            name = a.get("name")
            aid = a.get("author_id")
            if not isinstance(name, str) or not isinstance(aid, str):
                continue
            label = name
            top_work = a.get("top_work")
            if isinstance(top_work, str) and top_work:
                label = f"{name} — {top_work}"
            # Store author_id in the value (stable, short).
            choices.append(discord.app_commands.Choice(name=label[:100], value=aid))
        return choices

    async def user_author_subscription_autocomplete(self, interaction: discord.Interaction, current: str) -> List[discord.app_commands.Choice[str]]:
        user_id = interaction.user.id
        guild_id = interaction.guild.id if interaction.guild else 0
        try:
            subs = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_book_author_subscriptions, guild_id, user_id)
        except Exception:
            return []
        if not subs:
            return []
        current_lower = (current or "").lower()
        choices: List[discord.app_commands.Choice[str]] = []
        for sub in subs:
            name = sub.get("author_name") or sub.get("author_id")
            aid = sub.get("author_id")
            if not isinstance(aid, str):
                continue
            display = str(name)
            if current_lower and current_lower not in display.lower():
                continue
            choices.append(discord.app_commands.Choice(name=display[:100], value=aid))
        return choices[:25]

    @commands.hybrid_command(name="book_author_subscribe", description="Subscribe to an author and get notified (via DM) when they release a new book.")
    @discord.app_commands.describe(author="Author to subscribe to (searches Open Library)")
    @discord.app_commands.autocomplete(author=author_autocomplete)
    async def book_author_subscribe(self, ctx: commands.Context, author: str):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        guild_id = self._scope_guild_id_from_ctx(ctx)
        user_id = ctx.author.id

        # If autocomplete was used, `author` is the author_id. If not, search.
        selected: Optional[dict] = None
        if isinstance(author, str) and author.upper().startswith("OL") and author.upper().endswith("A"):
            # Autocomplete passes author_id only; resolve display name via author endpoint.
            aid = author.upper()
            try:
                resolved_name = await self.bot.loop.run_in_executor(None, partial(openlibrary_client.get_author_name, aid))
            except Exception:
                resolved_name = None
            selected = {"author_id": aid, "name": resolved_name or aid}
        else:
            try:
                results = await self.bot.loop.run_in_executor(None, partial(openlibrary_client.search_authors, author, limit=10))
            except openlibrary_client.OpenLibraryConnectionError:
                await self.send_response(ctx, "Could not reach Open Library. Please try again later.", ephemeral=not is_dm)
                return
            except openlibrary_client.OpenLibraryAPIError:
                await self.send_response(ctx, "Open Library returned an error. Please try again later.", ephemeral=not is_dm)
                return
            except Exception as e:
                logger.error(f"BooksCog: author search failed for '{author}': {e}")
                await self.send_response(ctx, "Unexpected error while searching for that author.", ephemeral=not is_dm)
                return

            if not results:
                await self.send_response(ctx, f"No authors found for '{author}'. Try a different spelling.", ephemeral=not is_dm)
                return

            if len(results) == 1:
                selected = results[0]
            else:
                # Try exact match first.
                exact = [r for r in results if isinstance(r.get("name"), str) and r["name"].lower() == author.lower()]
                if len(exact) == 1:
                    selected = exact[0]
                else:
                    display_results = results[:5]
                    embeds: List[discord.Embed] = []
                    for i, a in enumerate(display_results):
                        nm = a.get("name", "Unknown")
                        top = a.get("top_work")
                        wc = a.get("work_count")
                        desc_parts = [f"{NUMBER_EMOJIS[i]} **{nm}**"]
                        if top:
                            desc_parts.append(f"Top work: *{top}*")
                        if wc is not None:
                            desc_parts.append(f"Works: {wc}")
                        e = discord.Embed(description="\n".join(desc_parts), color=discord.Color.blurple())
                        embeds.append(e)

                    view = AuthorSelectionView(ctx, display_results)
                    if ctx.interaction:
                        msg = await ctx.interaction.followup.send(
                            content="Multiple authors found. Pick one:",
                            embeds=embeds,
                            ephemeral=not is_dm,
                            view=view,
                            wait=True,
                        )
                        view.message = msg
                    else:
                        msg = await ctx.send(content="Multiple authors found. Pick one:", embeds=embeds, view=view)
                        view.message = msg

                    await view.wait()
                    selected = view.selected_result

        if not selected or not isinstance(selected.get("author_id"), str):
            await self.send_response(ctx, "Selection cancelled or timed out.", ephemeral=not is_dm)
            return

        author_id = selected["author_id"]
        author_name = selected.get("name") if isinstance(selected.get("name"), str) else author_id

        # Baseline: mark existing works as "seen" so we only notify for future additions.
        try:
            works = await self.bot.loop.run_in_executor(None, partial(openlibrary_client.get_author_works, author_id, limit=100))
            work_ids = [w["work_id"] for w in works if isinstance(w, dict) and isinstance(w.get("work_id"), str)]
            # Per-user seen (required for correct DND + no duplicates)
            await self.bot.loop.run_in_executor(None, self.db_manager.mark_user_author_works_seen, user_id, author_id, work_ids)
        except Exception as e:
            logger.warning(f"BooksCog: baseline mark seen failed for author {author_id}: {e}")

        ok = await self.bot.loop.run_in_executor(None, self.db_manager.add_book_author_subscription, guild_id, user_id, author_id, author_name, None)
        if not ok:
            await self.send_response(ctx, "Database error while saving that subscription.", ephemeral=not is_dm)
            return

        await self.send_response(ctx, f"✅ Subscribed to **{author_name}**. I’ll DM you when new titles appear.", ephemeral=not is_dm)

    @commands.hybrid_command(name="book_author_unsubscribe", description="Unsubscribe from an author.")
    @discord.app_commands.describe(author="Author to unsubscribe from")
    @discord.app_commands.autocomplete(author=user_author_subscription_autocomplete)
    async def book_author_unsubscribe(self, ctx: commands.Context, author: str):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        guild_id = self._scope_guild_id_from_ctx(ctx)
        user_id = ctx.author.id
        author_id = author.upper().strip()

        ok = await self.bot.loop.run_in_executor(None, self.db_manager.remove_book_author_subscription, guild_id, user_id, author_id)
        if not ok:
            await self.send_response(ctx, "Database error while removing that subscription.", ephemeral=not is_dm)
            return
        await self.send_response(ctx, f"✅ Unsubscribed from **{author_id}**.", ephemeral=not is_dm)

    @commands.hybrid_command(name="my_book_authors", description="List your subscribed book authors.")
    async def my_book_authors(self, ctx: commands.Context):
        is_dm = ctx.guild is None
        await self._defer_if_interaction(ctx, ephemeral=not is_dm)
        if not self.db_manager:
            await self.send_response(ctx, "Database is not available right now. Please try again later.", ephemeral=not is_dm)
            return

        guild_id = self._scope_guild_id_from_ctx(ctx)
        subs = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_book_author_subscriptions, guild_id, ctx.author.id)
        if not subs:
            await self.send_response(ctx, "You’re not subscribed to any authors yet. Use `/book_author_subscribe`.", ephemeral=not is_dm)
            return

        embed = discord.Embed(title="📚 Your Author Subscriptions", color=discord.Color.green())
        lines: List[str] = []
        # Best-effort: backfill missing/placeholder author_name values.
        fixed_any = False
        for s in subs[:40]:
            aid = s.get("author_id")
            stored_name = s.get("author_name")

            display_name = stored_name if isinstance(stored_name, str) and stored_name.strip() else None
            if not display_name and isinstance(aid, str) and aid:
                # If older rows stored author_id as author_name, treat that as missing.
                if isinstance(stored_name, str) and stored_name.strip().upper() == aid.upper():
                    display_name = None

                if display_name is None:
                    try:
                        resolved = await self.bot.loop.run_in_executor(None, partial(openlibrary_client.get_author_name, aid))
                    except Exception:
                        resolved = None
                    if resolved:
                        display_name = resolved
                        try:
                            ok = await self.bot.loop.run_in_executor(
                                None, self.db_manager.update_book_author_subscription_name, guild_id, ctx.author.id, aid, resolved
                            )
                            if ok:
                                fixed_any = True
                        except Exception:
                            pass

            display_name = display_name or (aid if isinstance(aid, str) else "Unknown")
            lines.append(f"- **{display_name}** (`{aid}`)")
        if len(subs) > 40:
            lines.append(f"\n…and {len(subs) - 40} more.")
        embed.description = "\n".join(lines)
        if fixed_any:
            embed.set_footer(text="Some author names were refreshed from Open Library.")
        await self.send_response(ctx, embed=embed, ephemeral=not is_dm)

    @tasks.loop(hours=6)
    async def check_new_books(self):
        if not self.db_manager:
            return
        try:
            subs = await self.bot.loop.run_in_executor(None, self.db_manager.get_all_book_author_subscriptions)
        except Exception as e:
            logger.error(f"BooksCog: failed to load subscriptions: {e}")
            return
        if not subs:
            return

        # Build distinct author list
        author_ids = sorted({s["author_id"] for s in subs if isinstance(s, dict) and isinstance(s.get("author_id"), str)})

        dnd_cache: dict[int, bool] = {}

        for author_id in author_ids:
            try:
                author_subs = [s for s in subs if s.get("author_id") == author_id]
                if not author_subs:
                    continue

                author_name = None
                for s in author_subs:
                    if isinstance(s.get("author_name"), str) and s["author_name"].strip():
                        author_name = s["author_name"].strip()
                        break
                author_name = author_name or author_id

                works = await self.bot.loop.run_in_executor(None, partial(openlibrary_client.get_author_works, author_id, limit=25))
                work_items = [w for w in works if isinstance(w, dict) and isinstance(w.get("work_id"), str)]
                if not work_items:
                    await asyncio.sleep(0.15)
                    continue

                # Group subscriptions by user_id (DM delivery).
                user_ids: List[int] = []
                for s in author_subs:
                    gid_s = s.get("guild_id")
                    uid_s = s.get("user_id")
                    if not (isinstance(gid_s, str) and gid_s.isdigit() and isinstance(uid_s, str) and uid_s.isdigit()):
                        continue
                    uid = int(uid_s)
                    user_ids.append(uid)
                user_ids = sorted(set(user_ids))

                # For each user, compute unseen works and send/mark per-user.
                # This prevents duplicates and ensures DND doesn't cause permanent misses.
                for uid in user_ids:
                    # DND cache
                    if uid not in dnd_cache:
                        dnd_cache[uid] = await self._is_user_in_dnd(uid)
                    if dnd_cache[uid]:
                        continue

                    seen_user = set(
                        await self.bot.loop.run_in_executor(None, self.db_manager.get_seen_work_ids_for_user_author, uid, author_id)
                    )
                    unseen = [w for w in work_items if w["work_id"] not in seen_user]
                    if not unseen:
                        continue

                    user = self.bot.get_user(uid)
                    if not user:
                        try:
                            user = await self.bot.fetch_user(uid)
                        except (discord.NotFound, discord.HTTPException):
                            continue

                    for w in unseen[:10]:  # avoid spam
                        work_id = w["work_id"]
                        title = w.get("title") or "Untitled"
                        first_publish_date = w.get("first_publish_date")

                        embed = discord.Embed(
                            title=f"📚 New title by {author_name}",
                            description=f"**{title}**",
                            color=discord.Color.purple(),
                            url=openlibrary_client.work_url(work_id),
                        )
                        if isinstance(first_publish_date, str) and first_publish_date.strip():
                            embed.add_field(name="First publish date", value=first_publish_date.strip(), inline=True)
                        embed.add_field(name="Open Library", value=f"[View book]({openlibrary_client.work_url(work_id)})", inline=True)
                        embed.set_footer(text="Source: Open Library")

                        try:
                            await user.send(embed=embed)
                            await self.bot.loop.run_in_executor(None, self.db_manager.mark_user_author_work_seen, uid, author_id, work_id)
                        except discord.Forbidden:
                            # User blocked bot / DMs disabled; keep unseen so it can retry if they re-enable.
                            logger.warning(f"BooksCog: Cannot DM user {uid} (Forbidden).")
                            break
                        except discord.HTTPException as e:
                            logger.warning(f"BooksCog: HTTP error sending DM to user {uid}: {e}")
                            break

                await asyncio.sleep(0.25)
            except Exception as e:
                logger.error(f"BooksCog: error checking author {author_id}: {e}", exc_info=True)
                await asyncio.sleep(0.25)

    @check_new_books.before_loop
    async def before_check_new_books(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(BooksCog(bot, db_manager=bot.db_manager))
    logger.info("BooksCog has been loaded.")


