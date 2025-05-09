# cogs/stocks.py

import discord
import asyncio # Added for rate limiting
import logging # For background task logging
import typing # For type hinting
from discord.ext import commands, tasks
from api_clients import alpha_vantage_client
from api_clients.alpha_vantage_client import get_daily_time_series, get_intraday_time_series # Added
from utils.chart_utils import generate_stock_chart_url # Added
from data_manager import (
    add_tracked_stock, remove_tracked_stock, get_user_tracked_stocks,
    add_stock_alert, get_stock_alert, deactivate_stock_alert_target,
    get_all_active_alerts_for_monitoring
)

# Configure logging for this cog
logger = logging.getLogger(__name__)
# Example: Set a basic configuration if no root logger is configured
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

STOCK_CHECK_INTERVAL_MINUTES = 60 # Check one stock approximately every hour

SUPPORTED_TIMESPAN = {
    "1D": {"func": get_intraday_time_series, "params": {'interval': '15min', 'outputsize': 'compact'}, "label": "1 Day", "is_intraday": True},
    "5D": {"func": get_intraday_time_series, "params": {'interval': '60min', 'outputsize': 'compact'}, "label": "5 Days", "is_intraday": True},
    "1M": {"func": get_daily_time_series, "params": {'outputsize': 'compact'}, "label": "1 Month", "is_intraday": False},
    "3M": {"func": get_daily_time_series, "params": {'outputsize': 'compact'}, "label": "3 Months", "is_intraday": False}, # compact is 100 data points
    "6M": {"func": get_daily_time_series, "params": {'outputsize': 'full'}, "label": "6 Months", "is_intraday": False}, # full for more data
    "YTD": {"func": get_daily_time_series, "params": {'outputsize': 'full'}, "label": "Year-to-Date", "is_intraday": False},
    "1Y": {"func": get_daily_time_series, "params": {'outputsize': 'full'}, "label": "1 Year", "is_intraday": False},
    "MAX": {"func": get_daily_time_series, "params": {'outputsize': 'full'}, "label": "Max Available", "is_intraday": False},
}

class Stocks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.unique_stocks_queue = []
        self.current_queue_index = 0
        self.check_stock_alerts.start() # Start the background task

    def cog_unload(self):
        self.check_stock_alerts.cancel() # Ensure the task is cancelled on cog unload

    @commands.Cog.listener()
    async def on_ready(self):
        print("Stocks Cog is ready.")
        logger.info("Stocks Cog is ready and stock alert monitoring task is running.")

    @tasks.loop(minutes=STOCK_CHECK_INTERVAL_MINUTES)
    async def check_stock_alerts(self):
        logger.info("Stock alert check task running...")
        all_user_alerts = get_all_active_alerts_for_monitoring()

        if not all_user_alerts:
            logger.info("No active stock alerts to monitor.")
            return

        latest_unique_symbols = sorted(list(set(a['symbol'] for a in all_user_alerts)))

        if not latest_unique_symbols:
            logger.info("No unique symbols from active alerts.")
            self.unique_stocks_queue = []
            self.current_queue_index = 0
            return

        # Update queue if the set of unique symbols has changed
        if set(latest_unique_symbols) != set(self.unique_stocks_queue):
            logger.info(f"Unique stock list changed. Old: {self.unique_stocks_queue}, New: {latest_unique_symbols}")
            self.unique_stocks_queue = latest_unique_symbols
            self.current_queue_index = 0 # Reset index if list changes

        if not self.unique_stocks_queue: # Should be caught by previous check, but as a safeguard
            logger.info("Stock monitoring queue is empty after update.")
            return
        
        # Ensure index is valid
        if self.current_queue_index >= len(self.unique_stocks_queue):
            self.current_queue_index = 0

        symbol_to_check = self.unique_stocks_queue[self.current_queue_index]
        logger.info(f"Selected stock for current check: {symbol_to_check}")

        self.current_queue_index = (self.current_queue_index + 1) % len(self.unique_stocks_queue)
        
        # Add a small delay before API call, even if it's one per hour, good practice.
        await asyncio.sleep(2)
        price_data = alpha_vantage_client.get_stock_price(symbol_to_check)

        if not price_data or "error" in price_data or "05. price" not in price_data or "08. previous close" not in price_data:
            error_msg = price_data.get("message", "Unknown API error or invalid/incomplete data") if isinstance(price_data, dict) else "No data received"
            logger.error(f"Could not fetch complete price data (current and previous close) for {symbol_to_check} during alert check: {error_msg}")
            if isinstance(price_data, dict) and price_data.get("error") == "api_limit":
                logger.warning(f"Alpha Vantage API limit reached while checking {symbol_to_check}. Task will retry next cycle.")
            return

        try:
            current_price = float(price_data['05. price'])
            previous_close_price = float(price_data['08. previous close'])
            logger.info(f"Data for {symbol_to_check}: Current Price: {current_price}, Previous Close: {previous_close_price}")
        except (ValueError, TypeError) as e:
            logger.error(f"Could not parse price/previous close for {symbol_to_check}. Data: {price_data}. Error: {e}")
            return

        alerts_for_this_symbol = [a for a in all_user_alerts if a['symbol'] == symbol_to_check]

        for alert in alerts_for_this_symbol:
            user_id = alert['user_id']
            user = self.bot.get_user(user_id)
            if not user:
                logger.warning(f"Could not find user {user_id} for alert on {symbol_to_check}.")
                continue

            triggered_message = None
            deactivate_direction = None # Can be 'above', 'below', 'dpc_above', 'dpc_below'

            # --- Price Target Checks ---
            if alert.get('active_above') and alert.get('target_above') is not None:
                if current_price > alert['target_above']:
                    triggered_message = f"📈 **Price Alert!** {symbol_to_check} has risen above your target of ${alert['target_above']:.2f}. Current price: ${current_price:.2f}"
                    deactivate_direction = "above"
            
            if not triggered_message and alert.get('active_below') and alert.get('target_below') is not None:
                if current_price < alert['target_below']:
                    triggered_message = f"📉 **Price Alert!** {symbol_to_check} has fallen below your target of ${alert['target_below']:.2f}. Current price: ${current_price:.2f}"
                    deactivate_direction = "below"

            # --- Daily Percentage Change (DPC) Target Checks ---
            # Only proceed if a price alert hasn't already been triggered for this cycle for this user/stock
            if not triggered_message and previous_close_price != 0: # Avoid division by zero
                percentage_change = ((current_price - previous_close_price) / previous_close_price) * 100
                logger.info(f"DPC calc for {symbol_to_check}: Current: {current_price}, Prev Close: {previous_close_price}, Change: {percentage_change:.2f}%")

                if alert.get('dpc_above_active') and alert.get('dpc_above_target') is not None:
                    if percentage_change > alert['dpc_above_target']:
                        triggered_message = f"📈 **DPC Alert!** {symbol_to_check} is up +{percentage_change:.2f}% today (currently ${current_price:.2f}), meeting your +{alert['dpc_above_target']:.2f}% target."
                        deactivate_direction = "dpc_above"
                
                if not triggered_message and alert.get('dpc_below_active') and alert.get('dpc_below_target') is not None:
                    # dpc_below_target is stored as positive, so compare absolute percentage_change if it's negative
                    if percentage_change < 0 and abs(percentage_change) > alert['dpc_below_target']: # or percentage_change < -alert['dpc_below_target']
                         triggered_message = f"📉 **DPC Alert!** {symbol_to_check} is down {percentage_change:.2f}% today (currently ${current_price:.2f}), meeting your -{alert['dpc_below_target']:.2f}% target."
                         deactivate_direction = "dpc_below"
            elif not triggered_message and previous_close_price == 0:
                logger.warning(f"Cannot calculate DPC for {symbol_to_check} as previous_close_price is 0.")


            if triggered_message and deactivate_direction:
                try:
                    await user.send(triggered_message)
                    logger.info(f"Sent alert DM to user {user_id} for {symbol_to_check} ({deactivate_direction} target). Message: {triggered_message}")
                    deactivate_stock_alert_target(user_id, symbol_to_check, deactivate_direction)
                    logger.info(f"Deactivated {deactivate_direction} alert for user {user_id}, stock {symbol_to_check}.")
                except discord.Forbidden:
                    logger.warning(f"Could not send DM to user {user_id} (DM disabled or bot blocked).")
                except Exception as e:
                    logger.error(f"Error sending DM or deactivating alert for user {user_id}, {symbol_to_check}: {e}")
        logger.info(f"Finished alert check for {symbol_to_check}.")

    @check_stock_alerts.before_loop
    async def before_check_stock_alerts(self):
        await self.bot.wait_until_ready()
        logger.info("Stock alert monitoring task is waiting for bot to be ready...")

    @commands.hybrid_command(name="stock_price", description="Get the current price of a stock.")
    @discord.app_commands.describe(symbol="The stock symbol (e.g., AAPL, MSFT)")
    async def stock_price(self, ctx: commands.Context, *, symbol: str):
        """
        Fetches and displays the current price and other relevant information for a given stock symbol.

        Usage examples:
        `!stock_price AAPL`
        `/stock_price symbol:TSLA`
        """
        price_data = alpha_vantage_client.get_stock_price(symbol.upper())

        if price_data:
            if "error" in price_data:
                error_type = price_data["error"]
                error_message = price_data.get("message", "An unspecified error occurred.")
                if error_type == "api_limit":
                    await ctx.send(f"Could not retrieve price for {symbol.upper()}: {error_message}", ephemeral=True)
                elif error_type == "config_error":
                    print(f"Stock price configuration error for {symbol.upper()}: {error_message}") # Log server-side
                    await ctx.send(f"Could not retrieve price for {symbol.upper()} due to a server configuration issue. Please notify the bot administrator.", ephemeral=True)
                elif error_type == "api_error":
                    await ctx.send(f"Could not retrieve price for {symbol.upper()}: {error_message}", ephemeral=True)
                else: # Unknown error type in dictionary
                    print(f"Stock price: Unknown error type '{error_type}' for {symbol.upper()}: {error_message}")
                    await ctx.send(f"Error fetching data for {symbol.upper()}. An unexpected error occurred with the data provider.", ephemeral=True)
            elif "01. symbol" in price_data and "05. price" in price_data: # Success
                stock_symbol_from_api = price_data['01. symbol']
                
                # Helper to safely get and format numbers
                def get_formatted_value(key, prefix="", suffix="", is_numeric=True, is_currency=False, is_volume=False):
                    value = price_data.get(key)
                    if value is None or value == "":
                        return "N/A"
                    try:
                        if is_numeric:
                            num_value = float(value.rstrip('%')) # Remove % for change percent
                            if is_currency:
                                return f"{prefix}{num_value:,.2f}{suffix}"
                            elif is_volume:
                                return f"{prefix}{int(num_value):,}{suffix}"
                            # For change and change percent, format based on original string
                            if key == '09. change': # Retain sign
                                return f"{value}"
                            if key == '10. change percent': # Retain sign and %
                                return f"{value}"
                            return f"{prefix}{num_value:,.2f}{suffix}" # Default numeric formatting
                        return f"{prefix}{value}{suffix}" # Non-numeric
                    except ValueError:
                        return "N/A" # Should not happen if API is consistent

                price = get_formatted_value('05. price', prefix="$", is_currency=True)
                change_val_str = price_data.get('09. change', '0') # Default to '0' for float conversion
                change_percent_val_str = price_data.get('10. change percent', '0%') # Default to '0%' for float conversion
                
                change_display = get_formatted_value('09. change')
                change_percent_display = get_formatted_value('10. change percent')

                day_high = get_formatted_value('03. high', prefix="$", is_currency=True)
                day_low = get_formatted_value('04. low', prefix="$", is_currency=True)
                volume = get_formatted_value('06. volume', is_volume=True)

                # Determine embed color and trend emoji
                embed_color = discord.Color.light_grey() # Default color
                trend_emoji = "📊" # Default emoji

                try:
                    change_float = float(change_val_str)
                    if change_float > 0:
                        embed_color = discord.Color.green()
                        trend_emoji = "📈"
                    elif change_float < 0:
                        embed_color = discord.Color.red()
                        trend_emoji = "📉"
                except ValueError:
                    pass # Keep default color and emoji if change is N/A or not a number

                embed = discord.Embed(title=f"{trend_emoji} Stock Info for {stock_symbol_from_api}", color=embed_color)
                
                embed.add_field(name="💰 Price", value=price, inline=True)
                embed.add_field(name="↕️ Change", value=f"{change_display}", inline=True)
                embed.add_field(name="📈 Change %", value=f"{change_percent_display}", inline=True)
                
                embed.add_field(name="🔼 Day's High", value=day_high, inline=True)
                embed.add_field(name="🔽 Day's Low", value=day_low, inline=True)
                embed.add_field(name="📊 Volume", value=volume, inline=True)
                
                embed.add_field(name="🗓️ 52-Week High", value="N/A", inline=True)
                embed.add_field(name="🗓️ 52-Week Low", value="N/A", inline=True)
                embed.add_field(name="🏦 Market Cap", value="N/A", inline=True)
                
                embed.set_footer(text="Data provided by Alpha Vantage")
                await ctx.send(embed=embed)
            else: # price_data is a dictionary, but not a known error type and not a success structure
                print(f"Stock price: Unexpected data structure for {symbol.upper()}: {price_data}")
                await ctx.send(f"Error fetching data for {symbol.upper()}. Unexpected data format received from the provider.", ephemeral=True)
        else: # price_data is None (e.g., network issue, client-side timeout before API response)
            await ctx.send(f"Error fetching data for {symbol.upper()}. Could not connect to the data provider or the symbol is invalid.", ephemeral=True)


    @commands.hybrid_command(name="track_stock", description="Track a stock symbol, optionally with quantity and purchase price.")
    @discord.app_commands.describe(
        symbol="The stock symbol to track (e.g., AAPL, MSFT)",
        quantity="Number of shares (e.g., 10.5)",
        purchase_price="Price per share at purchase (e.g., 150.75)"
    )
    async def track_stock(self, ctx: commands.Context, symbol: str, quantity: typing.Optional[float] = None, purchase_price: typing.Optional[float] = None):
        """
        Allows a user to start tracking a stock symbol.
        Optionally, users can provide quantity and purchase price for portfolio tracking.
        If a stock is already tracked and new quantity/price are provided, they will be updated.

        Usage examples:
        `!track_stock GOOG`
        `/track_stock symbol:AMZN`
        `!track_stock AAPL quantity=10 purchase_price=150.00`
        `/track_stock symbol:TSLA quantity:5 purchase_price:700.25`
        """
        upper_symbol = symbol.upper()
        user_id = ctx.author.id

        # Validate quantity and purchase_price: if one is provided, the other must also be.
        if (quantity is not None and purchase_price is None) or \
           (quantity is None and purchase_price is not None):
            await ctx.send("If providing portfolio details, both `quantity` and `purchase_price` must be specified.", ephemeral=True)
            return

        if quantity is not None and quantity <= 0:
            await ctx.send("`quantity` must be a positive number.", ephemeral=True)
            return
        if purchase_price is not None and purchase_price <= 0:
            await ctx.send("`purchase_price` must be a positive number.", ephemeral=True)
            return

        # Attempt to add/update the stock
        # The data_manager.add_tracked_stock now handles the logic of adding vs updating
        # and whether portfolio data is new, updated, or absent.
        success = add_tracked_stock(user_id, upper_symbol, quantity, purchase_price)

        if success:
            if quantity is not None and purchase_price is not None:
                await ctx.send(f"Successfully tracking {upper_symbol} with {quantity} shares at ${purchase_price:,.2f} each. Portfolio data updated.", ephemeral=True)
            else:
                # Check if it was already tracked with portfolio data that's being kept, or just simple tracking
                tracked_stocks = get_user_tracked_stocks(user_id)
                existing_stock_info = next((s for s in tracked_stocks if s['symbol'] == upper_symbol), None)
                if existing_stock_info and existing_stock_info.get('quantity') is not None:
                     await ctx.send(f"Successfully tracking {upper_symbol}. Existing portfolio data (Quantity: {existing_stock_info['quantity']}, Price: ${existing_stock_info.get('purchase_price', 0):,.2f}) is maintained.", ephemeral=True)
                else:
                    await ctx.send(f"Successfully started tracking {upper_symbol} (no portfolio data provided/updated).", ephemeral=True)
        else:
            # This 'else' might indicate an issue like invalid data format if data_manager returns False for reasons other than "already tracked"
            # For now, assume it means "already tracked and no changes made" or a more specific error from data_manager if it were to raise one.
            # The current data_manager.add_tracked_stock returns True if already tracked and no portfolio update,
            # and False for invalid data format for quantity/price.
            await ctx.send(f"Could not track {upper_symbol}. This might be due to invalid quantity/price format, or the stock is already tracked and no valid update was provided.", ephemeral=True)

    @commands.hybrid_command(name="untrack_stock", description="Stop tracking a stock symbol.")
    @discord.app_commands.describe(symbol="The stock symbol to untrack (e.g., AAPL, MSFT)")
    async def untrack_stock(self, ctx: commands.Context, *, symbol: str):
        """
        Allows a user to stop tracking a stock symbol.

        Usage examples:
        `!untrack_stock MSFT`
        `/untrack_stock symbol:NVDA`
        """
        upper_symbol = symbol.upper()
        user_id = ctx.author.id
        if remove_tracked_stock(user_id, upper_symbol):
            await ctx.send(f"Successfully stopped tracking {upper_symbol}.", ephemeral=True)
        else:
            await ctx.send(f"{upper_symbol} was not found in your tracked list.", ephemeral=True)

    @commands.hybrid_command(name="my_tracked_stocks", description="Lists your tracked stock symbols.")
    async def my_tracked_stocks(self, ctx: commands.Context):
        """
        Lists all stock symbols you are currently tracking, along with their current prices.
        Note: Due to API rate limits, fetching prices for many stocks may take some time.

        Usage examples:
        `!my_tracked_stocks`
        `/my_tracked_stocks`
        """
        user_id = ctx.author.id
        tracked_stocks = get_user_tracked_stocks(user_id)

        if not tracked_stocks:
            await ctx.send("You are not tracking any stocks. Use `/track_stock <symbol>` to add some!", ephemeral=True)
            return

        embed = discord.Embed(title=f"📊 Your Tracked Stocks ({len(tracked_stocks)})", color=discord.Color.purple())
        embed.set_footer(text="Data provided by Alpha Vantage. Prices may be delayed. Alerts shown are active.")
        
        description_lines = []
        api_call_count = 0
        max_calls_for_prices = 3 # Limit direct price fetches in this command to avoid hitting limits quickly
        # For more than this, users should use !stock_price for individual stocks.
        # The alert system handles background checks.

        if len(tracked_stocks) > max_calls_for_prices:
             await ctx.send(f"Displaying basic info for {len(tracked_stocks)} stocks. For current prices of more than {max_calls_for_prices} stocks, please use `/stock_price <symbol>` individually to manage API rate limits.", ephemeral=True)


        for i, symbol in enumerate(tracked_stocks):
            stock_display = f"**{symbol.upper()}**:"
            
            # Fetch and display price only for a limited number
            if i < max_calls_for_prices:
                if api_call_count > 0: # Delay between calls if we are making multiple
                    await asyncio.sleep(13) # Alpha Vantage: 5 calls/min, so ~12s interval
                
                price_data = alpha_vantage_client.get_stock_price(symbol)
                api_call_count += 1

                if price_data:
                    if "error" in price_data:
                        error_type = price_data["error"]
                        error_message = price_data.get("message", "Unknown error")
                        if error_type == "api_limit": stock_display += f" ⚠️ Price: API limit. ({error_message[:20]}...)"
                        elif error_type == "config_error": stock_display += " ❌ Price: N/A (Server issue)"
                        else: stock_display += f" ❌ Price: N/A ({error_message})"
                    elif "01. symbol" in price_data and "05. price" in price_data:
                        raw_price = price_data.get('05. price')
                        raw_change = price_data.get('09. change', '0')
                        try:
                            price_val = float(raw_price)
                            price_display_val = f"${price_val:,.2f}"
                        except (ValueError, TypeError): price_display_val = "N/A"
                        try:
                            change_val_float = float(raw_change)
                            trend_emoji = "📈 " if change_val_float > 0 else "📉 " if change_val_float < 0 else ""
                        except (ValueError, TypeError): trend_emoji = ""
                        stock_display += f" 💰 {price_display_val} {trend_emoji}"
                    else: stock_display += " ❌ Price: N/A (Format error)"
                else: stock_display += " ❌ Price: N/A (Fetch error)"
            elif i == max_calls_for_prices: # For stocks beyond the limit, don't fetch price
                stock_display += " (Price check skipped due to API limits in this view)"


            # Display alert info
            alert_info = get_stock_alert(user_id, symbol)
            alert_texts = []
            if alert_info:
                if alert_info.get('active_above') and alert_info.get('target_above') is not None:
                    alert_texts.append(f"Price > ${alert_info['target_above']:.2f}")
                if alert_info.get('active_below') and alert_info.get('target_below') is not None:
                    alert_texts.append(f"Price < ${alert_info['target_below']:.2f}")
                if alert_info.get('dpc_above_active') and alert_info.get('dpc_above_target') is not None:
                    alert_texts.append(f"DPC > +{alert_info['dpc_above_target']:.2f}%")
                if alert_info.get('dpc_below_active') and alert_info.get('dpc_below_target') is not None:
                    alert_texts.append(f"DPC < -{alert_info['dpc_below_target']:.2f}%")
            
            if alert_texts:
                stock_display += f" | Alerts: {'; '.join(alert_texts)}"
            else:
                stock_display += " | No active alerts."
            
            description_lines.append(stock_display)

        if not description_lines:
            embed.description = "Could not retrieve information for your tracked stocks."
        else:
            embed.description = "\n".join(description_lines)
            
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="stock_alert", description="Set, update, or clear a price alert for a tracked stock.")
    @discord.app_commands.describe(
        symbol="The stock symbol (e.g., AAPL). Must be already tracked.",
        above_target="Price target: Notify when price > value. E.g., '150.50' or 'clear'.",
        below_target="Price target: Notify when price < value. E.g., '120.75' or 'clear'.",
        dpc_above_target="Daily % Change: Notify if % above prev close. E.g., '5' for +5%, '2.5', or 'clear'.",
        dpc_below_target="Daily % Change: Notify if % below prev close. E.g., '5' for -5%, '2.5', or 'clear'."
    )
    @discord.app_commands.rename(
        above_target='above',
        below_target='below',
        dpc_above_target='dpc_above',
        dpc_below_target='dpc_below'
    )
    async def stock_alert(self, ctx: commands.Context,
                          symbol: str,
                          above_target: typing.Optional[str] = None,
                          below_target: typing.Optional[str] = None,
                          dpc_above_target: typing.Optional[str] = None,
                          dpc_below_target: typing.Optional[str] = None):
        """
        Sets, updates, or clears price and daily percentage change (DPC) alerts for a tracked stock.

        Usage examples:
        `/stock_alert symbol:AAPL above:150.50`
        `/stock_alert symbol:MSFT dpc_above:5` (for +5% change)
        `/stock_alert symbol:GOOG below:2400 dpc_below:2.5` (for -2.5% change)
        `/stock_alert symbol:TSLA above:clear dpc_above:clear`
        `!stock_alert AAPL above=150.50 dpc_above=5`
        """
        user_id = ctx.author.id
        symbol_upper = symbol.upper()

        tracked_stocks = get_user_tracked_stocks(user_id)
        if symbol_upper not in [s.upper() for s in tracked_stocks]:
            await ctx.send(f"You are not tracking {symbol_upper}. Please use `/track_stock {symbol_upper}` first.", ephemeral=True)
            return

        if above_target is None and below_target is None and dpc_above_target is None and dpc_below_target is None:
            current_alert_info = get_stock_alert(user_id, symbol_upper)
            if current_alert_info:
                alerts_display = []
                if current_alert_info.get('active_above') and current_alert_info.get('target_above') is not None:
                    alerts_display.append(f"Price Above: ${current_alert_info['target_above']:.2f}")
                if current_alert_info.get('active_below') and current_alert_info.get('target_below') is not None:
                    alerts_display.append(f"Price Below: ${current_alert_info['target_below']:.2f}")
                if current_alert_info.get('dpc_above_active') and current_alert_info.get('dpc_above_target') is not None:
                    alerts_display.append(f"DPC Above: +{current_alert_info['dpc_above_target']:.2f}%")
                if current_alert_info.get('dpc_below_active') and current_alert_info.get('dpc_below_target') is not None:
                    alerts_display.append(f"DPC Below: -{current_alert_info['dpc_below_target']:.2f}%")
                
                if alerts_display:
                    await ctx.send(f"Current alerts for {symbol_upper}: {'; '.join(alerts_display)}. To change, provide target parameters.", ephemeral=True)
                else:
                    await ctx.send(f"No active alerts for {symbol_upper}. Set one using parameters like 'above', 'dpc_above', etc.", ephemeral=True)
            else:
                 await ctx.send(f"No alerts set for {symbol_upper}. Use parameters to set an alert (e.g., `above=150`, `dpc_above=5`).", ephemeral=True)
            return

        target_above_val, target_below_val = None, None
        dpc_above_val, dpc_below_val = None, None
        clear_above_flag, clear_below_flag = False, False
        clear_dpc_above_flag, clear_dpc_below_flag = False, False
        
        action_summary = []

        # Helper to parse percentage input
        def parse_percentage(value_str: str, param_name: str) -> typing.Optional[float]:
            if value_str is None: return None
            try:
                val = float(value_str.rstrip('%'))
                if val <= 0:
                    # Use asyncio.create_task for sending messages from non-async helper
                    asyncio.create_task(ctx.send(f"Error: '{param_name}' percentage must be a positive number.", ephemeral=True))
                    return None # Indicate error by returning None, caller should check
                return val
            except ValueError:
                asyncio.create_task(ctx.send(f"Error: Invalid format for '{param_name}': '{value_str}'. Use a number (e.g., 5 or 2.5) or 'clear'.", ephemeral=True))
                return None # Indicate error

        if above_target is not None:
            if above_target.lower() == 'clear':
                clear_above_flag = True
                action_summary.append("clearing 'price above' alert")
            else:
                try:
                    target_above_val = float(above_target)
                    if target_above_val <= 0:
                        await ctx.send("Error: 'Above' price must be a positive number.", ephemeral=True); return
                    action_summary.append(f"setting 'price above' target to ${target_above_val:.2f}")
                except ValueError:
                    await ctx.send(f"Error: Invalid price format for 'above': '{above_target}'. Use number or 'clear'.", ephemeral=True); return
        
        if below_target is not None:
            if below_target.lower() == 'clear':
                clear_below_flag = True
                action_summary.append("clearing 'price below' alert")
            else:
                try:
                    target_below_val = float(below_target)
                    if target_below_val <= 0:
                        await ctx.send("Error: 'Below' price must be a positive number.", ephemeral=True); return
                    action_summary.append(f"setting 'price below' target to ${target_below_val:.2f}")
                except ValueError:
                    await ctx.send(f"Error: Invalid price format for 'below': '{below_target}'. Use number or 'clear'.", ephemeral=True); return

        if dpc_above_target is not None:
            if dpc_above_target.lower() == 'clear':
                clear_dpc_above_flag = True
                action_summary.append("clearing 'DPC above' alert")
            else:
                # Need to await the send from the helper if it's used directly in async func
                # For simplicity, directly call and check return, error message sent by helper
                parsed_val = parse_percentage(dpc_above_target, "dpc_above")
                if parsed_val is None and dpc_above_target.lower() != 'clear': return
                dpc_above_val = parsed_val
                if dpc_above_val is not None:
                    action_summary.append(f"setting 'DPC above' target to +{dpc_above_val:.2f}%")

        if dpc_below_target is not None:
            if dpc_below_target.lower() == 'clear':
                clear_dpc_below_flag = True
                action_summary.append("clearing 'DPC below' alert")
            else:
                parsed_val = parse_percentage(dpc_below_target, "dpc_below")
                if parsed_val is None and dpc_below_target.lower() != 'clear': return
                dpc_below_val = parsed_val
                if dpc_below_val is not None:
                    action_summary.append(f"setting 'DPC below' target to -{dpc_below_val:.2f}%")

        if target_above_val is not None and target_below_val is not None and target_above_val <= target_below_val:
            await ctx.send(f"Error: 'Above' price target (${target_above_val:.2f}) must be greater than 'below' price target (${target_below_val:.2f}).", ephemeral=True); return
        
        success = add_stock_alert(
            user_id, symbol_upper,
            target_above=target_above_val, target_below=target_below_val,
            dpc_above_target=dpc_above_val, dpc_below_target=dpc_below_val,
            clear_above=clear_above_flag, clear_below=clear_below_flag,
            clear_dpc_above=clear_dpc_above_flag, clear_dpc_below=clear_dpc_below_flag
        )

        if success:
            final_action_summary = ", ".join(action_summary) if action_summary else "No changes specified."
            await ctx.send(f"Successfully updated alerts for {symbol_upper}: {final_action_summary}.", ephemeral=True)
        else:
            # This 'else' might mean no actual change was made, or an internal save error.
            # data_manager.add_stock_alert returns False if no change or error.
            # Check current state to provide a more specific message if no changes were made.
            current_alert = get_stock_alert(user_id, symbol_upper)
            
            if not action_summary: # No valid actions were parsed from input
                 await ctx.send(f"No valid alert changes specified for {symbol_upper}. Please provide targets or use 'clear'.", ephemeral=True)
            else: # Actions were specified, but add_stock_alert returned False
                # Check if it was because the state already matched the request
                is_no_actual_change = True # Assume no change was needed
                if clear_above_flag and (current_alert and current_alert.get("target_above") is not None): is_no_actual_change = False
                elif target_above_val is not None and (not current_alert or current_alert.get("target_above") != target_above_val or not current_alert.get("active_above")): is_no_actual_change = False
                
                if clear_below_flag and (current_alert and current_alert.get("target_below") is not None): is_no_actual_change = False
                elif target_below_val is not None and (not current_alert or current_alert.get("target_below") != target_below_val or not current_alert.get("active_below")): is_no_actual_change = False

                if clear_dpc_above_flag and (current_alert and current_alert.get("dpc_above_target") is not None): is_no_actual_change = False
                elif dpc_above_val is not None and (not current_alert or current_alert.get("dpc_above_target") != dpc_above_val or not current_alert.get("dpc_above_active")): is_no_actual_change = False

                if clear_dpc_below_flag and (current_alert and current_alert.get("dpc_below_target") is not None): is_no_actual_change = False
                elif dpc_below_val is not None and (not current_alert or current_alert.get("dpc_below_target") != dpc_below_val or not current_alert.get("dpc_below_active")): is_no_actual_change = False
                
                # If any of the conditions for 'is_no_actual_change = False' were met, it means a change was intended.
                # If all passed (is_no_actual_change remains True), then it was a no-op.
                if is_no_actual_change and action_summary: # action_summary means user intended something
                    await ctx.send(f"Alerts for {symbol_upper} are already set as requested. No changes made.", ephemeral=True)
                else: # An actual change was intended but add_stock_alert failed, or it's an unhandled case
                    await ctx.send(f"Could not update alerts for {symbol_upper}. This might be due to an internal error, or the values are already set as requested.", ephemeral=True)

    @commands.hybrid_command(name="stock_chart", description="Generate a price chart for a stock symbol over a timespan.")
    @discord.app_commands.describe(
        symbol="The stock symbol (e.g., AAPL, MSFT)",
        timespan=f"The timespan for the chart. Default '1M'. Options: {', '.join(SUPPORTED_TIMESPAN.keys())}"
    )
    async def stock_chart(self, ctx: commands.Context, symbol: str, timespan: str = "1M"):
        """
        Generates and displays a stock price chart for a given symbol and timespan.

        Usage examples:
        `!stock_chart AAPL 1M`
        `/stock_chart symbol:TSLA timespan:6M`
        `/stock_chart symbol:MSFT timespan:1D`
        """
        symbol_upper = symbol.upper()
        timespan_upper = timespan.upper()

        if timespan_upper not in SUPPORTED_TIMESPAN:
            await ctx.send(
                f"Invalid timespan '{timespan}'. Supported timespans are: {', '.join(SUPPORTED_TIMESPAN.keys())}",
                ephemeral=True
            )
            return

        await ctx.defer(ephemeral=False) # Acknowledge interaction, as fetching and charting can take time

        config = SUPPORTED_TIMESPAN[timespan_upper]
        api_func = config["func"]
        api_params = config["params"].copy() # Use a copy to avoid modifying the original dict
        display_label = config["label"]

        logger.info(f"Fetching chart data for {symbol_upper}, timespan {timespan_upper} using {api_func.__name__} with params {api_params}")

        # Call the appropriate Alpha Vantage function
        if config["is_intraday"]:
            time_series_data = api_func(symbol_upper, interval=api_params['interval'], outputsize=api_params['outputsize'])
        else: # Daily
            time_series_data = api_func(symbol_upper, outputsize=api_params['outputsize'])


        if not time_series_data:
            await ctx.send(f"Could not retrieve time series data for {symbol_upper} ({display_label}). The symbol might be invalid or there's no data for the period.", ephemeral=True)
            return
        if isinstance(time_series_data, dict) and "error" in time_series_data:
            error_type = time_series_data["error"]
            error_message = time_series_data.get("message", "An unspecified API error occurred.")
            if error_type == "api_limit":
                await ctx.send(f"Could not retrieve chart data for {symbol_upper}: Alpha Vantage API limit reached. Please try again later. ({error_message})", ephemeral=True)
            elif error_type == "config_error":
                logger.error(f"Stock chart config error for {symbol_upper}: {error_message}")
                await ctx.send(f"Could not retrieve chart data for {symbol_upper} due to a server configuration issue.", ephemeral=True)
            elif error_type == "api_error": # e.g. invalid symbol
                 await ctx.send(f"Could not retrieve chart data for {symbol_upper}: {error_message}", ephemeral=True)
            else:
                logger.error(f"Stock chart: Unknown error type '{error_type}' for {symbol_upper}: {error_message}")
                await ctx.send(f"Error fetching chart data for {symbol_upper}. An unexpected error occurred with the data provider.", ephemeral=True)
            return
        
        if not isinstance(time_series_data, list) or not time_series_data:
            await ctx.send(f"No valid time series data points found for {symbol_upper} ({display_label}) to generate a chart.", ephemeral=True)
            return

        # For YTD, we need to filter data to be from the start of the current year
        if timespan_upper == "YTD":
            try:
                from datetime import datetime
                current_year_start = datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                
                filtered_data = []
                for ts_str, price in time_series_data:
                    try:
                        dt_obj = None
                        if ' ' in ts_str:
                            dt_obj = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
                        else:
                            dt_obj = datetime.strptime(ts_str, '%Y-%m-%d')
                        
                        if dt_obj >= current_year_start:
                            filtered_data.append((ts_str, price))
                    except ValueError:
                        logger.warning(f"Could not parse timestamp '{ts_str}' for YTD filtering. Skipping.")
                        continue
                
                time_series_data = filtered_data
                if not time_series_data:
                    await ctx.send(f"No data found for {symbol_upper} since the start of this year for the YTD chart.", ephemeral=True)
                    return
            except Exception as e:
                logger.error(f"Error filtering YTD data for {symbol_upper}: {e}")
                await ctx.send(f"An error occurred while processing YTD data for {symbol_upper}.", ephemeral=True)
                return


        logger.info(f"Generating chart URL for {symbol_upper} ({display_label}) with {len(time_series_data)} data points.")
        chart_url = generate_stock_chart_url(symbol_upper, display_label, time_series_data)

        if chart_url:
            embed = discord.Embed(
                title=f"📈 Stock Chart for {symbol_upper} ({display_label})",
                color=discord.Color.blue()
            )
            embed.set_image(url=chart_url)
            embed.set_footer(text="Chart generated using QuickChart.io | Data from Alpha Vantage")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"Sorry, I couldn't generate the chart for {symbol_upper} ({display_label}) at this time.", ephemeral=True)

    @commands.hybrid_command(name="stock_news", description="Get recent news for a stock symbol.")
    @discord.app_commands.describe(symbol="The stock symbol (e.g., AAPL, MSFT)")
    async def stock_news(self, ctx: commands.Context, *, symbol: str):
        """
        Fetches and displays recent news articles for a given stock symbol.

        Usage examples:
        `!stock_news AAPL`
        `/stock_news symbol:TSLA`
        """
        await ctx.defer(ephemeral=True) # Acknowledge interaction, news fetching can take a moment
        upper_symbol = symbol.upper()
        
        # Limit to 3-5 articles, client default is 5, so we'll use that.
        news_data = alpha_vantage_client.get_stock_news(upper_symbol, limit=5)

        if news_data is None:
            await ctx.send(f"📰 No news found for {upper_symbol}, or an error occurred while fetching.", ephemeral=True)
            return
        
        if isinstance(news_data, dict) and "error" in news_data:
            error_type = news_data["error"]
            error_message = news_data.get("message", "An unspecified error occurred.")
            if error_type == "api_limit":
                await ctx.send(f"Could not retrieve news for {upper_symbol}: {error_message}", ephemeral=True)
            elif error_type == "config_error":
                logger.error(f"Stock news configuration error for {upper_symbol}: {error_message}")
                await ctx.send(f"Could not retrieve news for {upper_symbol} due to a server configuration issue. Please notify the bot administrator.", ephemeral=True)
            elif error_type == "api_error":
                await ctx.send(f"Could not retrieve news for {upper_symbol}: {error_message}", ephemeral=True)
            else:
                logger.error(f"Stock news: Unknown error type '{error_type}' for {upper_symbol}: {error_message}")
                await ctx.send(f"Error fetching news for {upper_symbol}. An unexpected error occurred with the data provider.", ephemeral=True)
            return

        if not isinstance(news_data, list) or not news_data:
            await ctx.send(f"📰 No news articles found for {upper_symbol} at this time.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📰 Recent News for {upper_symbol}",
            color=discord.Color.blue()
        )
        embed.set_footer(text="News provided by Alpha Vantage. Summaries may be truncated.")

        for i, article in enumerate(news_data):
            if i >= 5: # Should be handled by API client limit, but as a safeguard
                break

            title = article.get("title", "No Title")
            url = article.get("url", None)
            source = article.get("source", "N/A")
            time_published = article.get("time_published", "N/A")
            summary = article.get("summary", "No summary available.")
            sentiment_label = article.get("sentiment_label", "N/A")
            # sentiment_score = article.get("sentiment_score", "N/A") # Not displaying score for brevity

            # Truncate summary if too long for an embed field
            if len(summary) > 250:
                summary = summary[:247] + "..."
            
            field_title = f"{title}"
            if url:
                field_title = f"🔗 [{title}]({url})"
            else:
                field_title = f"{title}" # No link if URL is missing

            field_value = f"**Source:** {source}\n" \
                          f"**Published:** {time_published}\n" \
                          f"**Sentiment:** {sentiment_label}\n" \
                          f"**Summary:** {summary}"
            
            embed.add_field(name=field_title[:256], value=field_value[:1024], inline=False) # Ensure field limits

        if not embed.fields: # Should not happen if news_data was populated
            await ctx.send(f"📰 No news articles could be formatted for {upper_symbol}.", ephemeral=True)
            return
            
        await ctx.send(embed=embed, ephemeral=False) # Send publicly if successful

    @commands.hybrid_command(name="my_portfolio", description="View your stock portfolio performance.")
    async def my_portfolio(self, ctx: commands.Context):
        """
        Displays your stock portfolio, including total value, overall gain/loss,
        and performance of individual holdings.
        Requires stocks to be tracked with quantity and purchase price.

        Usage examples:
        `!my_portfolio`
        `/my_portfolio`
        """
        user_id = ctx.author.id
        tracked_stocks_all = get_user_tracked_stocks(user_id)

        portfolio_stocks = [
            s for s in tracked_stocks_all
            if s.get("quantity") is not None and s.get("purchase_price") is not None
        ]

        if not portfolio_stocks:
            await ctx.send(
                "You have no stocks with portfolio data (quantity and purchase price). "
                "Use `/track_stock <symbol> quantity=<qty> purchase_price=<price>` to add them.",
                ephemeral=True
            )
            return

        embed = discord.Embed(title="💰 Your Stock Portfolio", color=discord.Color.gold())
        embed.set_footer(text="Data provided by Alpha Vantage. Prices may be delayed.")

        overall_cost_basis = 0
        overall_market_value = 0
        api_call_count = 0
        individual_holdings_details = [] # Store list of dicts

        # Message to inform user about potential delay
        status_msg = None # Initialize status_msg
        if len(portfolio_stocks) > 1: # Only show if fetching multiple prices
            status_msg = await ctx.send(f"Fetching current prices for {len(portfolio_stocks)} holdings... this may take a moment due to API rate limits.", ephemeral=True)

        for stock_data in portfolio_stocks:
            symbol = stock_data["symbol"]
            quantity = stock_data["quantity"]
            purchase_price = stock_data["purchase_price"]

            if api_call_count > 0:
                await asyncio.sleep(13) # Alpha Vantage: ~5 calls/min, so ~12s interval + buffer

            current_price_data = alpha_vantage_client.get_stock_price(symbol)
            api_call_count += 1

            current_price = None
            api_error_for_stock = False

            if current_price_data and "05. price" in current_price_data:
                try:
                    current_price = float(current_price_data["05. price"])
                except (ValueError, TypeError):
                    logger.error(f"Portfolio: Could not parse current price for {symbol}. Data: {current_price_data}")
                    api_error_for_stock = True
            else:
                error_info = "Unknown API error"
                if isinstance(current_price_data, dict) and "error" in current_price_data:
                    error_info = current_price_data.get("message", current_price_data["error"])
                elif current_price_data is None:
                    error_info = "No data received (possible network issue or invalid symbol)"
                logger.warning(f"Portfolio: Could not fetch price for {symbol}. Info: {error_info}")
                api_error_for_stock = True

            cost_basis = quantity * purchase_price
            market_value = 0.0 # Ensure float
            gain_loss = 0.0 # Ensure float
            gain_loss_pct_str = "N/A"

            overall_cost_basis += cost_basis

            if current_price is not None and not api_error_for_stock:
                market_value = quantity * current_price
                overall_market_value += market_value
                gain_loss = market_value - cost_basis
                if cost_basis != 0:
                    gain_loss_pct = (gain_loss / cost_basis) * 100
                    gain_loss_pct_str = f"{gain_loss_pct:+.2f}%"
                else:
                    gain_loss_pct_str = "N/A (zero cost basis)"
            elif api_error_for_stock:
                market_value = "N/A (API Error)"
                gain_loss = "N/A"
                gain_loss_pct_str = "N/A"
                current_price = "N/A (API Error)"

            individual_holdings_details.append({
                "symbol": symbol,
                "quantity": quantity,
                "purchase_price": purchase_price,
                "current_price": current_price,
                "cost_basis": cost_basis,
                "market_value": market_value,
                "gain_loss": gain_loss,
                "gain_loss_pct_str": gain_loss_pct_str
            })

        overall_gain_loss = overall_market_value - overall_cost_basis
        overall_gain_loss_pct_str = "N/A"
        if overall_cost_basis != 0 and isinstance(overall_market_value, (int, float)) and overall_market_value > 0 : # Check if market_value is numeric
            overall_gain_loss_pct = (overall_gain_loss / overall_cost_basis) * 100
            overall_gain_loss_pct_str = f"{overall_gain_loss_pct:+.2f}%"
        elif overall_cost_basis == 0 and isinstance(overall_market_value, (int, float)) and overall_market_value > 0:
             overall_gain_loss_pct_str = "+∞%"
        elif overall_cost_basis == 0 and isinstance(overall_market_value, (int, float)) and overall_market_value == 0:
             overall_gain_loss_pct_str = "N/A"
        # If overall_market_value is a string (due to API error for all stocks), it remains "N/A"

        summary_color = discord.Color.default()
        if isinstance(overall_gain_loss, (int,float)): # Check if numeric before comparison
            if overall_gain_loss > 0: summary_color = discord.Color.green()
            elif overall_gain_loss < 0: summary_color = discord.Color.red()

        embed.color = summary_color

        overall_market_value_display = f"${overall_market_value:,.2f}" if isinstance(overall_market_value, (int, float)) else str(overall_market_value)
        overall_gain_loss_display = f"${overall_gain_loss:,.2f}" if isinstance(overall_gain_loss, (int, float)) else str(overall_gain_loss)


        embed.add_field(
            name="📈 Overall Portfolio Summary",
            value=(
                f"**Total Market Value:** {overall_market_value_display}\n"
                f"**Total Cost Basis:** ${overall_cost_basis:,.2f}\n"
                f"**Total Gain/Loss:** {overall_gain_loss_display} ({overall_gain_loss_pct_str})"
            ),
            inline=False
        )

        holdings_text_parts = []
        for item in individual_holdings_details:
            symbol_header = f"--- **{item['symbol']}** ---"
            
            current_price_display = f"${item['current_price']:,.2f}" if isinstance(item['current_price'], (int, float)) else str(item['current_price'])
            market_value_display = f"${item['market_value']:,.2f}" if isinstance(item['market_value'], (int, float)) else str(item['market_value'])
            gain_loss_display_val = f"${item['gain_loss']:+,.2f}" if isinstance(item['gain_loss'], (int, float)) else str(item['gain_loss'])

            gain_loss_emoji = ""
            if isinstance(item['gain_loss'], (int, float)):
                if item['gain_loss'] > 0: gain_loss_emoji = "🔼 "
                elif item['gain_loss'] < 0: gain_loss_emoji = "🔽 "
            
            details = (
                f"{symbol_header}\n"
                f"Quantity: `{item['quantity']}` @ Avg Cost: `${item['purchase_price']:,.2f}`\n"
                f"Cost Basis: `${item['cost_basis']:,.2f}`\n"
                f"Current Price: `{current_price_display}`\n"
                f"Market Value: `{market_value_display}`\n"
                f"Gain/Loss: {gain_loss_emoji}`{gain_loss_display_val} ({item['gain_loss_pct_str']})`"
            )
            holdings_text_parts.append(details)

        if holdings_text_parts:
            current_field_value = ""
            field_count = 0
            for part_idx, part in enumerate(holdings_text_parts):
                field_name = "Individual Holdings"
                if field_count > 0 : # Check if previous field was also "Individual Holdings"
                    field_name = "Individual Holdings (Continued)"

                if len(current_field_value) + len(part) + 2 > 1024:
                    embed.add_field(name=field_name, value=current_field_value, inline=False)
                    current_field_value = part
                    field_count +=1
                else:
                    if current_field_value:
                        current_field_value += f"\n\n{part}"
                    else:
                        current_field_value = part
            
            if current_field_value: # Add the last part
                field_name = "Individual Holdings"
                if field_count > 0:
                    field_name = "Individual Holdings (Continued)"
                embed.add_field(name=field_name, value=current_field_value, inline=False)
        else:
            embed.add_field(name="Individual Holdings", value="No holdings data to display.", inline=False)

        if status_msg: # Check if status_msg was defined
            try:
                await status_msg.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                logger.warning(f"Could not delete portfolio status message: {e}")

        await ctx.send(embed=embed, ephemeral=False)

async def setup(bot):
    await bot.add_cog(Stocks(bot))
    print("Stocks Cog has been loaded and stock alert task initialized.")