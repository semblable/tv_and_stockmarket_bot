import discord
from discord.ext import commands
import inspect

class MyCustomHelpCommand(commands.HelpCommand):
    def __init__(self):
        super().__init__(
            command_attrs={
                "help": "Shows this help message about commands.",
                "aliases": ["h", "commands"]
            }
        )
        self.color = discord.Color.blurple()  # Default embed color

    def get_command_signature(self, command):
        prefix = self.context.prefix
        # command.signature provides the parameters part of the signature
        if command.signature:
            return f"{prefix}{command.qualified_name} {command.signature}"
        return f"{prefix}{command.qualified_name}"

    async def send_bot_help(self, mapping):
        ctx = self.context
        embed = discord.Embed(title="📺🤖 TV & Stock Market Bot Help", color=self.color)
        description_parts = [
            "**Welcome!** I can help you track your favorite TV shows and monitor the stock market.",
            "",
            "**✨ New Features:**",
            "• **Global Stocks:** Now supporting Global 100 companies and top 20 Polish stocks (WIG20)!",
            "• **Robust Charts:** Improved stock charts using Yahoo Finance data.",
            "• **Smart Notifications:** TV show alerts now persist across restarts.",
            "",
            f"Use `{ctx.prefix}help <command_name>` for more details on a specific command.",
            f"Use `{ctx.prefix}help <CategoryName>` for more details on a category of commands."
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
        if not filtered_commands:
            final_desc = (embed.description + "\n" if embed.description else "") + "No commands in this category."
            embed.description = final_desc
            await self.get_destination().send(embed=embed)
            return

        command_list_text = []
        for command in filtered_commands:
            desc = command.description or command.short_doc or ""
            desc_line = desc.splitlines()[0] if desc else ""
            entry = f"`{ctx.prefix}{command.name}`"
            if desc_line:
                entry += f" - {desc_line}"
            command_list_text.append(entry)
        
        embed.add_field(name="Commands", value="\n".join(command_list_text), inline=False)
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
        embed = discord.Embed(title="Help Error", description=str(error), color=discord.Color.red())
        await self.get_destination().send(embed=embed)

# No setup(bot) function needed here if we import and assign in bot.py directly.