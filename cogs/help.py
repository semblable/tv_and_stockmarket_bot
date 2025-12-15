import discord
from discord.ext import commands
import re
from typing import Iterable, Optional, Sequence

class MyCustomHelpCommand(commands.HelpCommand):
    def __init__(self):
        super().__init__(
            command_attrs={
                "help": "Shows this help message about commands.",
                "aliases": ["h", "commands"]
            }
        )
        self.color = discord.Color.blurple()  # Default embed color

    def _iter_app_commands(self) -> Iterable[discord.app_commands.Command]:
        """
        Iterate all registered application commands (including commands inside groups).
        """
        tree_cmds: Sequence[discord.app_commands.Command] = self.context.bot.tree.get_commands()  # type: ignore[assignment]
        for cmd in tree_cmds:
            yield cmd
            if isinstance(cmd, discord.app_commands.Group):
                yield from self._iter_app_group_commands(cmd)

    def _iter_app_group_commands(self, group: discord.app_commands.Group) -> Iterable[discord.app_commands.Command]:
        for cmd in group.commands:
            yield cmd
            if isinstance(cmd, discord.app_commands.Group):
                yield from self._iter_app_group_commands(cmd)

    def _find_app_command(self, query: str) -> Optional[discord.app_commands.Command]:
        """
        Best-effort lookup for an application command by name or qualified name.
        Accepts 'weather', '/weather', or group qualified names like 'admin ban'.
        """
        q = (query or "").strip()
        if not q:
            return None
        q = q.lstrip("/").strip()
        q_lower = q.lower()
        q_lower_spaces = re.sub(r"\s+", " ", q_lower).strip()
        q_lower_underscores = q_lower_spaces.replace(" ", "_")

        for cmd in self._iter_app_commands():
            name = getattr(cmd, "name", "")
            qualified = getattr(cmd, "qualified_name", name)
            if not name:
                continue
            if name.lower() == q_lower_spaces:
                return cmd
            if qualified.lower() == q_lower_spaces:
                return cmd
            if qualified.lower().replace(" ", "_") == q_lower_underscores:
                return cmd
        return None

    def _format_app_command_usage(self, app_cmd: discord.app_commands.Command) -> str:
        qualified = getattr(app_cmd, "qualified_name", app_cmd.name)
        params = getattr(app_cmd, "parameters", []) or []
        parts = [f"/{qualified}"]
        for p in params:
            p_name = getattr(p, "name", "param")
            required = bool(getattr(p, "required", False))
            parts.append(f"<{p_name}>" if required else f"[{p_name}]")
        return " ".join(parts)

    async def _send_app_command_help(self, app_cmd: discord.app_commands.Command):
        """
        Render help for a slash-only command (or any app command).
        """
        qualified = getattr(app_cmd, "qualified_name", app_cmd.name)
        embed = discord.Embed(title=f"Help: /{qualified}", color=self.color)
        embed.description = getattr(app_cmd, "description", None) or "No detailed help available."
        embed.add_field(name="Usage", value=f"`{self._format_app_command_usage(app_cmd)}`", inline=False)

        params = getattr(app_cmd, "parameters", []) or []
        if params:
            param_lines = []
            for p in params:
                p_name = getattr(p, "name", "param")
                p_desc = getattr(p, "description", None) or "No description."
                p_req = "Required" if bool(getattr(p, "required", False)) else "Optional"
                param_lines.append(f"**`{p_name}`** ({p_req}): {p_desc}")
            embed.add_field(name="Parameters", value="\n".join(param_lines), inline=False)

        binding = getattr(app_cmd, "binding", None)
        if binding and hasattr(binding, "qualified_name"):
            embed.set_footer(text=f"Category: {binding.qualified_name} • Slash command")
        else:
            embed.set_footer(text="Slash command")

        await self.get_destination().send(embed=embed)

    def get_command_signature(self, command):
        prefix = self.context.prefix
        # command.signature provides the parameters part of the signature
        if command.signature:
            return f"{prefix}{command.qualified_name} {command.signature}"
        return f"{prefix}{command.qualified_name}"

    async def send_bot_help(self, mapping):
        ctx = self.context
        embed = discord.Embed(title="TV, Stocks & Assistant Bot — Help", color=self.color)
        description_parts = [
            "**Welcome!** I can help with entertainment tracking, stocks, reminders, productivity, and more.",
            "",
            "**Highlights:**",
            "- **TV & Movies**: subscriptions + reminders, trending, info lookups",
            "- **Stocks**: quotes, tracking, alerts, charts, portfolio view",
            "- **Weather**: `/weather` + optional scheduled DMs (see `settings`)",
            "- **Reminders & Productivity**: one-off/repeating reminders, todos, habits, stats",
            "- **Books & Games**: author subscriptions, reading progress, game lookups",
            "",
            f"Use `{ctx.prefix}help <command>` for details on a specific command.",
            f"Use `{ctx.prefix}help <CategoryName>` for a category.",
            f"Tip: you can also type `/` in chat to browse slash commands.",
        ]
        embed.description = "\n".join(description_parts)

        listed_command_names = set() # To avoid listing hybrid commands twice

        for cog, commands_in_cog in mapping.items():
            filtered_commands = await self.filter_commands(commands_in_cog, sort=True)
            if not filtered_commands:
                continue

            cog_name = cog.qualified_name if cog else "General Commands"
            command_list_text = []
            for command in filtered_commands:
                if isinstance(command, commands.HybridCommand):
                    listed_command_names.add(command.name)
                
                # Use command.description (from decorator) or short_doc (first line of docstring)
                desc = command.description or command.short_doc or ""
                desc_line = desc.splitlines()[0] if desc else "" # Take first line for brevity
                
                entry = f"`{ctx.prefix}{command.name}`"
                if desc_line:
                    entry += f" - {desc_line}"
                command_list_text.append(entry)
            
            if command_list_text:
                embed.add_field(name=cog_name, value="\n".join(command_list_text), inline=False)

        # List pure slash commands (those not already listed as hybrid)
        app_commands = self.context.bot.tree.get_commands()
        pure_app_command_list_text = []
        if app_commands:
            for app_cmd in app_commands:
                if app_cmd.name not in listed_command_names and isinstance(app_cmd, discord.app_commands.Command):
                    desc = app_cmd.description if app_cmd.description else "No description."
                    pure_app_command_list_text.append(f"`/{app_cmd.name}` - {desc.splitlines()[0]}")
            
            if pure_app_command_list_text:
                 embed.add_field(name="Application Commands (Slash-Only)", value="\n".join(pure_app_command_list_text), inline=False)

        if not embed.fields:
            embed.description = "No callable commands found."
        
        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command):
        ctx = self.context
        embed = discord.Embed(title=f"Help: {ctx.prefix}{command.qualified_name}", color=self.color)
        
        # Description (from docstring/help attribute, fallback to decorator description)
        help_text = command.help or command.description or "No detailed help available."
        embed.description = help_text

        embed.add_field(name="Usage", value=f"`{self.get_command_signature(command)}`", inline=False)

        if command.aliases:
            embed.add_field(name="Aliases", value=", ".join([f"`{ctx.prefix}{alias}`" for alias in command.aliases]), inline=False)
        
        # Parameters for Hybrid Commands (from @app_commands.describe)
        if isinstance(command, commands.HybridCommand) and command.app_command and command.app_command.parameters:
            param_details = []
            for param in command.app_command.parameters:
                p_name = param.name
                p_desc = param.description if param.description else "No specific description."
                p_req = "Required" if param.required else "Optional"
                param_details.append(f"**`{p_name}`** ({p_req}): {p_desc}")
            if param_details:
                embed.add_field(name="Parameters", value="\n".join(param_details), inline=False)
        
        if command.cog_name:
            embed.set_footer(text=f"Category: {command.cog_name}")

        await self.get_destination().send(embed=embed)

    async def send_cog_help(self, cog):
        ctx = self.context
        embed = discord.Embed(title=f"{cog.qualified_name} Commands", color=self.color)
        if cog.description:
            embed.description = cog.description

        filtered_commands = await self.filter_commands(cog.get_commands(), sort=True)
        command_list_text = []
        if filtered_commands:
            for command in filtered_commands:
                desc = command.description or command.short_doc or ""
                desc_line = desc.splitlines()[0] if desc else ""
                entry = f"`{ctx.prefix}{command.name}`"
                if desc_line:
                    entry += f" - {desc_line}"
                command_list_text.append(entry)

        # Slash-only commands bound to this cog (defined with @app_commands.command inside the cog)
        slash_only_lines = []
        for app_cmd in self._iter_app_commands():
            if not isinstance(app_cmd, discord.app_commands.Command):
                continue
            if getattr(app_cmd, "binding", None) is cog:
                desc = getattr(app_cmd, "description", None) or "No description."
                slash_only_lines.append(f"`/{getattr(app_cmd, 'qualified_name', app_cmd.name)}` - {desc.splitlines()[0]}")

        if command_list_text:
            embed.add_field(name="Commands", value="\n".join(command_list_text), inline=False)
        if slash_only_lines:
            embed.add_field(name="Slash Commands", value="\n".join(slash_only_lines), inline=False)

        if not command_list_text and not slash_only_lines:
            final_desc = (embed.description + "\n" if embed.description else "") + "No commands in this category."
            embed.description = final_desc
        await self.get_destination().send(embed=embed)

    async def send_group_help(self, group):
        ctx = self.context
        embed = discord.Embed(title=f"Help: {ctx.prefix}{group.qualified_name} (Group)", color=self.color)
        
        help_text = group.help or group.description or "No detailed help available for this command group."
        embed.description = help_text

        embed.add_field(name="Usage", value=f"`{self.get_command_signature(group)}`", inline=False)

        if group.aliases:
            embed.add_field(name="Aliases", value=", ".join([f"`{ctx.prefix}{alias}`" for alias in group.aliases]), inline=False)

        subcommands = await self.filter_commands(group.commands, sort=True)
        if subcommands:
            sub_list_text = []
            for cmd in subcommands:
                desc = cmd.description or cmd.short_doc or ""
                desc_line = desc.splitlines()[0] if desc else ""
                entry = f"`{ctx.prefix}{cmd.qualified_name}`" # Use qualified_name for subcommands
                if desc_line:
                    entry += f" - {desc_line}"
                sub_list_text.append(entry)
            embed.add_field(name="Subcommands", value="\n".join(sub_list_text), inline=False)
        
        if group.cog_name:
            embed.set_footer(text=f"Category: {group.cog_name}")

        await self.get_destination().send(embed=embed)
        
    async def send_error_message(self, error):
        # If the user asked for help on a slash-only command (e.g. `!help weather`),
        # the base resolver won't find it as a prefix command. Try app commands before erroring out.
        error_text = str(error)
        m = re.search(r'"(.+?)"', error_text)
        if m:
            query = m.group(1)
            app_cmd = self._find_app_command(query)
            if app_cmd:
                await self._send_app_command_help(app_cmd)
                return

        embed = discord.Embed(title="Help Error", description=error_text, color=discord.Color.red())
        await self.get_destination().send(embed=embed)

# No setup(bot) function needed here if we import and assign in bot.py directly.