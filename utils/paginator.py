
import discord
import math

class BasePaginatorView(discord.ui.View):
    message: discord.Message | None = None

    def __init__(self, *, timeout=300, user_id: int, items: list, items_per_page: int = 5):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.items = items
        self.items_per_page = items_per_page if items_per_page > 0 else 5
        self.current_page = 0
        
        if not self.items:
            self.total_pages = 0
        else:
            self.total_pages = math.ceil(len(self.items) / self.items_per_page)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't for you!", ephemeral=True)
            return False
        return True

    def _update_button_states(self):
        is_first_page = self.current_page == 0
        if hasattr(self, 'first_page_button'): self.first_page_button.disabled = is_first_page
        if hasattr(self, 'prev_page_button'): self.prev_page_button.disabled = is_first_page

        is_last_page = self.current_page >= self.total_pages - 1
        if hasattr(self, 'next_page_button'): self.next_page_button.disabled = is_last_page
        if hasattr(self, 'last_page_button'): self.last_page_button.disabled = is_last_page
        
        if self.total_pages <= 1:
            if hasattr(self, 'first_page_button'): self.first_page_button.disabled = True
            if hasattr(self, 'prev_page_button'): self.prev_page_button.disabled = True
            if hasattr(self, 'next_page_button'): self.next_page_button.disabled = True
            if hasattr(self, 'last_page_button'): self.last_page_button.disabled = True

    async def _get_embed_for_current_page(self) -> discord.Embed:
        """
        Subclasses must implement this to return the embed for the current page.
        Access items via self.items and current page via self.current_page.
        Range: start_index to end_index
        """
        raise NotImplementedError("Subclasses must implement _get_embed_for_current_page")

    async def start(self, ctx, ephemeral: bool = True):
        self._update_button_states()
        initial_embed = await self._get_embed_for_current_page()

        if ctx.interaction:
            # Use followup if already deferred, otherwise response.send_message
            # Assuming defer was called, use followup.
            try:
                self.message = await ctx.interaction.followup.send(embed=initial_embed, view=self, ephemeral=ephemeral)
            except (discord.InteractionResponded, AttributeError): # Fallback if not deferred or context is different
                 self.message = await ctx.send(embed=initial_embed, view=self)
        else:
            self.message = await ctx.send(embed=initial_embed, view=self)

    async def _edit_message(self, interaction: discord.Interaction):
        embed = await self._get_embed_for_current_page()
        await interaction.response.edit_message(embed=embed, view=self)
        self.message = interaction.message

    @discord.ui.button(label="⏪ First", style=discord.ButtonStyle.grey, row=1)
    async def first_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        await self._edit_message(interaction)

    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.blurple, row=1)
    async def prev_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
        await self._edit_message(interaction)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.blurple, row=1)
    async def next_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        await self._edit_message(interaction)

    @discord.ui.button(label="Last ⏩", style=discord.ButtonStyle.grey, row=1)
    async def last_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages - 1
        await self._edit_message(interaction)

    async def on_timeout(self):
        if self.message:
            try:
                # Try to get the last embed state
                embed = self.message.embeds[0] if self.message.embeds else discord.Embed(title="Timed Out")
                
                # Update footer
                current_footer = embed.footer.text if embed.footer else "Controls timed out."
                if "(Controls timed out)" not in current_footer:
                    embed.set_footer(text=f"{current_footer} (Controls timed out)")
                
                await self.message.edit(embed=embed, view=None)
            except (discord.HTTPException, IndexError):
                pass # Message might be deleted
        
        # Disable all buttons as fallback
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

class SelectionView(discord.ui.View):
    def __init__(self, user_id: int, num_options: int, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.value = None
        
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        
        for i in range(min(num_options, 5)):
            button = discord.ui.Button(
                style=discord.ButtonStyle.blurple, 
                emoji=emojis[i],
                custom_id=f"select_{i}"
            )
            button.callback = self.create_callback(i)
            self.add_item(button)
            
    def create_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This isn't for you!", ephemeral=True)
                return
            self.value = idx
            await interaction.response.defer()
            self.stop()
        return callback

    async def on_timeout(self):
        self.stop()
