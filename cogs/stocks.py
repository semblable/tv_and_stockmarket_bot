# cogs/stocks.py

import discord
from discord.ext import commands
from api_clients import alpha_vantage_client
from data_manager import add_tracked_stock, remove_tracked_stock, get_user_tracked_stocks

class Stocks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print("Stocks Cog is ready.")

    @commands.hybrid_command(name="stock_price", description="Get the current price of a stock.")
    @discord.app_commands.describe(symbol="The stock symbol (e.g., AAPL, MSFT)")
    async def stock_price(self, ctx: commands.Context, *, symbol: str):
        """Fetches and displays the current price for a given stock symbol."""
        price_data = alpha_vantage_client.get_stock_price(symbol.upper())

        if price_data:
            if "error" in price_data and price_data["error"] == "api_limit":
                await ctx.send(f"Could not retrieve price for {symbol.upper()}: {price_data['message']}", ephemeral=True)
            elif "01. symbol" in price_data and "05. price" in price_data:
                stock_symbol_from_api = price_data['01. symbol']
                price = price_data['05. price']
                change = price_data.get('09. change', 'N/A')
                change_percent = price_data.get('10. change percent', 'N/A')

                embed = discord.Embed(title=f"Stock Price for {stock_symbol_from_api}", color=discord.Color.green())
                embed.add_field(name="Price", value=f"${price}", inline=True)
                embed.add_field(name="Change", value=change, inline=True)
                embed.add_field(name="Change %", value=change_percent, inline=True)
                embed.set_footer(text="Data provided by Alpha Vantage")
                await ctx.send(embed=embed)
            else: # Handles other dictionary-form errors or missing essential data
                await ctx.send(f"Error fetching data for {symbol.upper()}. The symbol might be invalid or there was an API issue.", ephemeral=True)
        else: # price_data is None (network issue, truly invalid symbol not caught by API as dict error)
            await ctx.send(f"Error fetching data for {symbol.upper()}. The symbol might be invalid or there was an API issue.", ephemeral=True)


    @commands.hybrid_command(name="track_stock", description="Track a stock symbol.")
    @discord.app_commands.describe(symbol="The stock symbol to track (e.g., AAPL, MSFT)")
    async def track_stock(self, ctx: commands.Context, *, symbol: str):
        """Allows a user to start tracking a stock symbol."""
        upper_symbol = symbol.upper()
        user_id = ctx.author.id
        
        # Optional: Add stock price validation here if desired in the future
        # price_data = alpha_vantage_client.get_stock_price(upper_symbol)
        # if not price_data or ("error" in price_data and price_data["error"] != "api_limit") or "01. symbol" not in price_data:
        #     await ctx.send(f"Could not validate stock symbol {upper_symbol}. Please ensure it's a valid symbol.", ephemeral=True)
        #     return

        if add_tracked_stock(user_id, upper_symbol):
            await ctx.send(f"Successfully started tracking {upper_symbol}.", ephemeral=True)
        else:
            await ctx.send(f"{upper_symbol} is already in your tracked list.", ephemeral=True)

    @commands.hybrid_command(name="untrack_stock", description="Stop tracking a stock symbol.")
    @discord.app_commands.describe(symbol="The stock symbol to untrack (e.g., AAPL, MSFT)")
    async def untrack_stock(self, ctx: commands.Context, *, symbol: str):
        """Allows a user to stop tracking a stock symbol."""
        upper_symbol = symbol.upper()
        user_id = ctx.author.id
        if remove_tracked_stock(user_id, upper_symbol):
            await ctx.send(f"Successfully stopped tracking {upper_symbol}.", ephemeral=True)
        else:
            await ctx.send(f"{upper_symbol} was not found in your tracked list.", ephemeral=True)

    @commands.hybrid_command(name="my_tracked_stocks", description="Lists your tracked stock symbols.")
    async def my_tracked_stocks(self, ctx: commands.Context):
        """Lists all stock symbols the invoking user is currently tracking."""
        user_id = ctx.author.id
        tracked_stocks = get_user_tracked_stocks(user_id)

        if not tracked_stocks:
            await ctx.send("You are not tracking any stocks.", ephemeral=True)
        else:
            stocks_list_str = ", ".join(tracked_stocks)
            # For a simple message:
            # await ctx.send(f"Your tracked stocks: {stocks_list_str}", ephemeral=True)

            # For an embed:
            embed = discord.Embed(title="Your Tracked Stocks", color=discord.Color.blue())
            embed.description = stocks_list_str
            await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Stocks(bot))
    print("Stocks Cog has been loaded.")