# cogs/stocks.py

import discord
import asyncio
import logging
import typing
from discord.ext import commands, tasks
from api_clients import alpha_vantage_client
from api_clients.alpha_vantage_client import get_daily_time_series, get_intraday_time_series
from api_clients import yahoo_finance_client
from utils.chart_utils import generate_stock_chart_url
from data_manager import DataManager
from utils.paginator import BasePaginatorView

# Configure logging for this cog
logger = logging.getLogger(__name__)
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

STOCK_CHECK_INTERVAL_MINUTES = 60

SUPPORTED_TIMESPAN = {
    "1D": {"func": get_intraday_time_series, "params": {'interval': '15min', 'outputsize': 'compact'}, "label": "1 Day", "is_intraday": True},
    "5D": {"func": get_intraday_time_series, "params": {'interval': '60min', 'outputsize': 'compact'}, "label": "5 Days", "is_intraday": True},
    "1M": {"func": get_daily_time_series, "params": {'outputsize': 'compact'}, "label": "1 Month", "is_intraday": False},
    "3M": {"func": get_daily_time_series, "params": {'outputsize': 'compact'}, "label": "3 Months", "is_intraday": False},
    "6M": {"func": get_daily_time_series, "params": {'outputsize': 'full'}, "label": "6 Months", "is_intraday": False},
    "YTD": {"func": get_daily_time_series, "params": {'outputsize': 'full'}, "label": "Year-to-Date", "is_intraday": False},
    "1Y": {"func": get_daily_time_series, "params": {'outputsize': 'full'}, "label": "1 Year", "is_intraday": False},
    "MAX": {"func": get_daily_time_series, "params": {'outputsize': 'full'}, "label": "Max Available", "is_intraday": False},
}

class MyStocksPaginatorView(BasePaginatorView):
    def __init__(self, *, timeout=300, user_id: int, items: list, bot_instance, db_manager, items_per_page: int = 5):
        super().__init__(timeout=timeout, user_id=user_id, items=items, items_per_page=items_per_page)
        self.bot = bot_instance
        self.db_manager = db_manager

    async def _edit_message(self, interaction: discord.Interaction):
        await interaction.response.defer() # Acknowledge button click immediately
        embed = await self._get_embed_for_current_page()
        await interaction.message.edit(embed=embed, view=self)
        self.message = interaction.message

    async def _get_embed_for_current_page(self) -> discord.Embed:
        self._update_button_states()

        start_index = self.current_page * self.items_per_page
        end_index = start_index + self.items_per_page
        page_subs = self.items[start_index:end_index]

        embed_title = f"📊 Your Tracked Stocks ({len(self.items)})"
        if self.total_pages > 1:
            embed_title += f" (Page {self.current_page + 1}/{self.total_pages})"
        
        embed = discord.Embed(title=embed_title, color=discord.Color.purple())
        embed.set_footer(text="Data provided by Alpha Vantage. Prices may be delayed. Alerts shown are active.")

        if not page_subs:
            embed.description = "No stocks to display on this page."
            return embed

        description_lines = []
        api_call_count = 0
        
        # We use a small delay between calls to avoid rate limits, but here we are inside a view update.
        # A long delay might be bad UX, but necessary for free API.
        # With 5 items per page, max delay is ~60s if we wait 12s each. That's too long.
        # Alpha Vantage free tier is 5 calls/minute.
        # If we paginate, user might click "Next" faster than 1 min.
        # We should probably just show cached data or warn about limits.
        # Or, fetch in parallel if we have premium (not assumed).
        # For free tier, we must limit rate.
        # We'll try to fetch, but if limit hit, we show "Limit Reached".
        
        for i, stock_item in enumerate(page_subs):
            symbol_upper = stock_item['symbol'].upper()
            stock_display = f"**{symbol_upper}**:"
            
            quantity = stock_item.get('quantity')
            purchase_price = stock_item.get('purchase_price')
            if quantity is not None and purchase_price is not None:
                stock_display += f" ({quantity} @ ${purchase_price:,.2f})"
            
            if api_call_count > 0:
                await asyncio.sleep(12) # Wait 12s to respect 5 calls/min limit (60/5 = 12)
            
            price_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_price, symbol_upper)
            api_call_count += 1

            if price_data:
                if "error" in price_data:
                    error_type = price_data["error"]
                    if error_type == "api_limit": 
                        stock_display += f" ⚠️ Price: API limit."
                    else: 
                        stock_display += f" ❌ Price: N/A"
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
                else: 
                    stock_display += " ❌ Price: N/A (Format)"
            else: 
                stock_display += " ❌ Price: N/A (Fetch)"

            alert_info = await self.bot.loop.run_in_executor(None, self.db_manager.get_stock_alert, self.user_id, symbol_upper)
            alert_texts = []
            if alert_info:
                if alert_info.get('active_above') and alert_info.get('target_above') is not None:
                    alert_texts.append(f"Price > ${float(alert_info['target_above']):.2f}")
                if alert_info.get('active_below') and alert_info.get('target_below') is not None:
                    alert_texts.append(f"Price < ${float(alert_info['target_below']):.2f}")
                if alert_info.get('dpc_above_active') and alert_info.get('dpc_above_target') is not None:
                    alert_texts.append(f"DPC > +{float(alert_info['dpc_above_target']):.2f}%")
                if alert_info.get('dpc_below_active') and alert_info.get('dpc_below_target') is not None:
                    alert_texts.append(f"DPC < -{float(alert_info['dpc_below_target']):.2f}%")
            
            if alert_texts:
                stock_display += f" | Alerts: {'; '.join(alert_texts)}"
            
            description_lines.append(stock_display)

        if not description_lines:
            embed.description = "Could not retrieve information for your tracked stocks."
        else:
            embed.description = "\n".join(description_lines)
            
        return embed

class Stocks(commands.Cog):
    def __init__(self, bot: commands.Bot, db_manager: DataManager) -> None:
        self.bot = bot
        self.db_manager = db_manager
        self.unique_stocks_queue: typing.List[str] = []
        self.current_queue_index = 0
        self.check_stock_alerts.start()

    async def send_response(self, ctx, content=None, embed=None, embeds=None, ephemeral=True, wait=False):
        kwargs = {}
        if content is not None: kwargs['content'] = content
        if embed is not None: kwargs['embed'] = embed
        if embeds is not None: kwargs['embeds'] = embeds
        
        if ctx.interaction:
            kwargs['ephemeral'] = ephemeral
            kwargs['wait'] = wait
            return await ctx.interaction.followup.send(**kwargs)
        else:
            return await ctx.send(**kwargs)

    def cog_unload(self) -> None:
        self.check_stock_alerts.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        print("Stocks Cog is ready.")
        logger.info("Stocks Cog is ready and stock alert monitoring task is running.")

    async def stock_symbol_autocomplete(self, interaction: discord.Interaction, current: str) -> typing.List[discord.app_commands.Choice[str]]:
        """
        Autocomplete for general stock symbols (popular + tracked).
        Does not search remote API to avoid rate limits.
        """
        user_id = interaction.user.id
        tracked_stocks = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
        tracked_symbols = [s['symbol'] for s in tracked_stocks]
        
        popular_stocks = ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA", "NVDA", "META", "NFLX", "AMD", "INTC", "SPY", "VOO", "QQQ", "IWM", "DIA", "BRK.B", "JPM", "JNJ", "V", "PG", "UNH", "HD", "MA", "DIS", "PYPL"]
        
        all_candidates = sorted(list(set(tracked_symbols + popular_stocks)))
        
        current_upper = current.upper()
        
        if not current:
             return [discord.app_commands.Choice(name=s, value=s) for s in all_candidates[:25]]
        
        filtered = [s for s in all_candidates if current_upper in s]
        return [discord.app_commands.Choice(name=s, value=s) for s in filtered[:25]]

    async def tracked_stock_symbol_autocomplete(self, interaction: discord.Interaction, current: str) -> typing.List[discord.app_commands.Choice[str]]:
        """
        Autocomplete strictly for stocks the user is currently tracking.
        """
        user_id = interaction.user.id
        tracked_stocks = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
        tracked_symbols = sorted([s['symbol'] for s in tracked_stocks])
        
        current_upper = current.upper()
        
        if not current:
             return [discord.app_commands.Choice(name=s, value=s) for s in tracked_symbols[:25]]
        
        filtered = [s for s in tracked_symbols if current_upper in s]
        return [discord.app_commands.Choice(name=s, value=s) for s in filtered[:25]]

    @tasks.loop(minutes=STOCK_CHECK_INTERVAL_MINUTES)
    async def check_stock_alerts(self) -> None:
        if not self.db_manager:
            logger.error("StocksCog: DataManager (db_manager) not available. Cannot check stock alerts.")
            return

        logger.info("Stock alert check task running...")
        all_user_alerts_map = await self.bot.loop.run_in_executor(None, self.db_manager.get_all_active_alerts_for_monitoring)

        if not all_user_alerts_map:
            logger.info("No active stock alerts to monitor.")
            return

        all_symbols_with_alerts = set()
        for user_id_str, stock_alerts_dict in all_user_alerts_map.items():
            for symbol_str in stock_alerts_dict.keys():
                all_symbols_with_alerts.add(symbol_str)
        
        latest_unique_symbols = sorted(list(all_symbols_with_alerts))

        if not latest_unique_symbols:
            logger.info("No unique symbols from active alerts.")
            self.unique_stocks_queue = []
            self.current_queue_index = 0
            return

        if set(latest_unique_symbols) != set(self.unique_stocks_queue):
            logger.info(f"Unique stock list changed. Old: {self.unique_stocks_queue}, New: {latest_unique_symbols}")
            self.unique_stocks_queue = latest_unique_symbols
            self.current_queue_index = 0

        if not self.unique_stocks_queue:
            logger.info("Stock monitoring queue is empty after update.")
            return
        
        if self.current_queue_index >= len(self.unique_stocks_queue):
            self.current_queue_index = 0

        symbol_to_check = self.unique_stocks_queue[self.current_queue_index]
        logger.info(f"Selected stock for current check: {symbol_to_check}")

        self.current_queue_index = (self.current_queue_index + 1) % len(self.unique_stocks_queue)
        
        await asyncio.sleep(2)
        price_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_price, symbol_to_check)

        if not price_data or "error" in price_data or "05. price" not in price_data or "08. previous close" not in price_data:
            error_msg = price_data.get("message", "Unknown API error or invalid/incomplete data") if isinstance(price_data, dict) else "No data received"
            logger.error(f"Could not fetch complete price data for {symbol_to_check} during alert check: {error_msg}")
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

        for user_id_str, user_specific_alerts_dict in all_user_alerts_map.items():
            if symbol_to_check not in user_specific_alerts_dict:
                continue

            alert_details = user_specific_alerts_dict[symbol_to_check]
            user_id_int = int(user_id_str)
            
            discord_user_obj = await self.bot.fetch_user(user_id_int)
            if not discord_user_obj:
                logger.warning(f"Could not find user {user_id_int} for alert on {symbol_to_check}.")
                continue

            triggered_message = None
            deactivate_direction = None

            if alert_details.get('active_above') and alert_details.get('target_above') is not None:
                if current_price > float(alert_details['target_above']):
                    triggered_message = f"📈 **Price Alert!** {symbol_to_check} has risen above your target of ${float(alert_details['target_above']):.2f}. Current price: ${current_price:.2f}"
                    deactivate_direction = "above"
            
            if not triggered_message and alert_details.get('active_below') and alert_details.get('target_below') is not None:
                if current_price < float(alert_details['target_below']):
                    triggered_message = f"📉 **Price Alert!** {symbol_to_check} has fallen below your target of ${float(alert_details['target_below']):.2f}. Current price: ${current_price:.2f}"
                    deactivate_direction = "below"

            if not triggered_message and previous_close_price != 0:
                percentage_change = ((current_price - previous_close_price) / previous_close_price) * 100
                logger.info(f"DPC calc for {symbol_to_check} (User {user_id_int}): Current: {current_price}, Prev Close: {previous_close_price}, Change: {percentage_change:.2f}%")

                if alert_details.get('dpc_above_active') and alert_details.get('dpc_above_target') is not None:
                    if percentage_change > float(alert_details['dpc_above_target']):
                        triggered_message = f"📈 **DPC Alert!** {symbol_to_check} is up +{percentage_change:.2f}% today (currently ${current_price:.2f}), meeting your +{float(alert_details['dpc_above_target']):.2f}% target."
                        deactivate_direction = "dpc_above"
                
                if not triggered_message and alert_details.get('dpc_below_active') and alert_details.get('dpc_below_target') is not None:
                    if percentage_change < 0 and abs(percentage_change) > float(alert_details['dpc_below_target']):
                         triggered_message = f"📉 **DPC Alert!** {symbol_to_check} is down {percentage_change:.2f}% today (currently ${current_price:.2f}), meeting your -{float(alert_details['dpc_below_target']):.2f}% target."
                         deactivate_direction = "dpc_below"
            elif not triggered_message and previous_close_price == 0:
                logger.warning(f"Cannot calculate DPC for {symbol_to_check} as previous_close_price is 0.")

            if triggered_message and deactivate_direction:
                try:
                    await discord_user_obj.send(triggered_message)
                    logger.info(f"Sent alert DM to user {user_id_int} for {symbol_to_check} ({deactivate_direction} target). Message: {triggered_message}")
                    await self.bot.loop.run_in_executor(None, self.db_manager.deactivate_stock_alert_target, user_id_int, symbol_to_check, deactivate_direction)
                    logger.info(f"Deactivated {deactivate_direction} alert for user {user_id_int}, stock {symbol_to_check}.")
                except discord.Forbidden:
                    logger.warning(f"Could not send DM to user {user_id_int} (DM disabled or bot blocked).")
                except Exception as e:
                    logger.error(f"Error sending DM or deactivating alert for user {user_id_int}, {symbol_to_check}: {e}")
        logger.info(f"Finished alert check for {symbol_to_check}.")

    @check_stock_alerts.before_loop
    async def before_check_stock_alerts(self) -> None:
        await self.bot.wait_until_ready()
        logger.info("Stock alert monitoring task is waiting for bot to be ready...")

    @commands.hybrid_command(name="stock_price", description="Get the current price of a stock.")
    @discord.app_commands.describe(symbol="The stock symbol (e.g., AAPL, MSFT, LPP.WA)")
    @discord.app_commands.autocomplete(symbol=stock_symbol_autocomplete)
    async def stock_price(self, ctx: commands.Context, *, symbol: str) -> None:
        """
        Fetches and displays the current price and other relevant information for a given stock symbol.
        Supports both US stocks (Alpha Vantage) and international stocks like Polish stocks (Yahoo Finance).
        """
        await ctx.defer(ephemeral=True)
        
        logger.info(f"[STOCK_PRICE_DEBUG] Command received for symbol: {symbol}")
        upper_symbol = symbol.upper()
        
        logger.info(f"[STOCK_PRICE_DEBUG] Attempting Alpha Vantage for {upper_symbol}")
        price_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_price, upper_symbol)
        data_source = "Alpha Vantage"
        logger.info(f"[STOCK_PRICE_DEBUG] Alpha Vantage raw response for {upper_symbol}: {price_data}")

        if not price_data or "error" in price_data:
            if price_data and price_data.get("error") == "api_limit":
                logger.info(f"[STOCK_PRICE_DEBUG] Alpha Vantage API limit for {upper_symbol}. Falling back to Yahoo.")
            else:
                logger.info(f"[STOCK_PRICE_DEBUG] Alpha Vantage failed for {upper_symbol} (Data: {price_data}). Falling back to Yahoo.")
            
            logger.info(f"[STOCK_PRICE_DEBUG] Attempting Yahoo Finance for {upper_symbol} (normalized to {yahoo_finance_client.normalize_symbol(upper_symbol)})")
            price_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_price, upper_symbol)
            data_source = "Yahoo Finance"
            logger.info(f"[STOCK_PRICE_DEBUG] Yahoo Finance raw response for {yahoo_finance_client.normalize_symbol(upper_symbol)}: {price_data}")
            
            if not price_data:
                logger.info(f"[STOCK_PRICE_DEBUG] Yahoo Finance also failed for {yahoo_finance_client.normalize_symbol(upper_symbol)}. No more APIs to try.")
        
        if not price_data:
            logger.error(f"[STOCK_PRICE_DEBUG] All APIs failed for {upper_symbol}.")
            embed = discord.Embed(
                title="❌ Stock Not Found",
                description=f"Could not retrieve data for **{upper_symbol}** from Alpha Vantage or Yahoo Finance.\n\n" +
                           f"Please check the symbol and try again.\n\n" +
                           f"💡 **Tip**: For Polish stocks, try adding `.WA` suffix (e.g., `{upper_symbol}.WA`)",
                color=discord.Color.red()
            )
            await self.send_response(ctx,embed=embed)
            return

        if price_data:
            if "error" in price_data:
                error_type = price_data["error"]
                error_message = price_data.get("message", "An unspecified error occurred.")
                logger.error(f"[STOCK_PRICE_DEBUG] Final data source ({data_source}) reported error for {upper_symbol}: Type: {error_type}, Msg: {error_message}")
                if error_type == "api_limit":
                    await self.send_response(ctx,f"Could not retrieve price for {upper_symbol}: {error_message}")
                elif error_type == "config_error":
                    print(f"Stock price configuration error for {upper_symbol}: {error_message}")
                    await self.send_response(ctx,f"Could not retrieve price for {upper_symbol} due to a server configuration issue. Please notify the bot administrator.")
                elif error_type == "api_error":
                    await self.send_response(ctx,f"Could not retrieve price for {upper_symbol}: {error_message}")
                else:
                    print(f"Stock price: Unknown error type '{error_type}' for {upper_symbol}: {error_message}")
                    await self.send_response(ctx,f"Error fetching data for {upper_symbol}. An unexpected error occurred with the data provider.")
            elif "01. symbol" in price_data and "05. price" in price_data:
                logger.info(f"[STOCK_PRICE_DEBUG] Successfully processed data for {upper_symbol} from {data_source}.")
                stock_symbol_from_api = price_data['01. symbol']
                
                currency = price_data.get('currency', 'USD')
                currency_symbols = {'USD': '$', 'PLN': 'zł', 'EUR': '€', 'GBP': '£', 'CAD': 'C$', 'JPY': '¥'}
                currency_symbol = currency_symbols.get(currency, currency)
                
                def get_formatted_value(key, prefix="", suffix="", is_numeric=True, is_currency=False, is_volume=False):
                    value = price_data.get(key)
                    if value is None or value == "":
                        return "N/A"
                    try:
                        if is_numeric:
                            num_value = float(value.rstrip('%'))
                            if is_currency:
                                return f"{num_value:,.2f} {currency_symbol}" if currency == 'PLN' else f"{currency_symbol}{num_value:,.2f}"
                            elif is_volume:
                                return f"{prefix}{int(num_value):,}{suffix}"
                            if key == '09. change':
                                return f"{value}"
                            if key == '10. change percent':
                                return f"{value}"
                            return f"{prefix}{num_value:,.2f}{suffix}"
                        return f"{prefix}{value}{suffix}"
                    except ValueError:
                        return "N/A"
                
                price = get_formatted_value('05. price', is_currency=True)
                change_val_str = price_data.get('09. change', '0')
                
                change_display = get_formatted_value('09. change')
                change_percent_display = get_formatted_value('10. change percent')

                day_high = get_formatted_value('03. high', is_currency=True)
                day_low = get_formatted_value('04. low', is_currency=True)
                volume = get_formatted_value('06. volume', is_volume=True)

                embed_color = discord.Color.light_grey()
                trend_emoji = "📊"

                try:
                    change_float = float(change_val_str)
                    if change_float > 0:
                        embed_color = discord.Color.green()
                        trend_emoji = "📈"
                    elif change_float < 0:
                        embed_color = discord.Color.red()
                        trend_emoji = "📉"
                except ValueError:
                    pass

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
                
                if currency != 'USD' and 'exchange' in price_data:
                    embed.add_field(name="🏢 Exchange", value=price_data['exchange'], inline=True)
                
                embed.set_footer(text=f"Data provided by {data_source}")
                await ctx.send(embed=embed)
            else:
                logger.error(f"[STOCK_PRICE_DEBUG] Unexpected data structure for {upper_symbol} from {data_source}: {price_data}")
                print(f"Stock price: Unexpected data structure for {symbol.upper()}: {price_data}")
                await ctx.send(f"Error fetching data for {symbol.upper()}. Unexpected data format received from the provider.")
        else:
            logger.error(f"[STOCK_PRICE_DEBUG] Both Alpha Vantage and Yahoo Finance returned None for {upper_symbol}.")
            await ctx.send(f"Error fetching data for {symbol.upper()}. Could not connect to the data provider or the symbol is invalid.")


    @commands.hybrid_command(name="track_stock", description="Track a stock symbol, optionally with quantity and purchase price.")
    @discord.app_commands.describe(
        symbol="The stock symbol to track (e.g., AAPL, MSFT)",
        quantity="Number of shares (e.g., 10.5)",
        purchase_price="Price per share at purchase (e.g., 150.75)"
    )
    @discord.app_commands.autocomplete(symbol=stock_symbol_autocomplete)
    async def track_stock(self, ctx: commands.Context, symbol: str, quantity: typing.Optional[float] = None, purchase_price: typing.Optional[float] = None) -> None:
        """
        Allows a user to start tracking a stock symbol.
        Optionally, users can provide quantity and purchase price for portfolio tracking.
        """
        upper_symbol = symbol.upper()
        user_id = ctx.author.id

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

        await ctx.defer(ephemeral=True)
        
        success = await self.bot.loop.run_in_executor(None, self.db_manager.add_tracked_stock, user_id, upper_symbol, quantity, purchase_price)

        if success:
            if quantity is not None and purchase_price is not None:
                await ctx.send(f"Successfully tracking {upper_symbol} with {quantity} shares at ${purchase_price:,.2f} each. Portfolio data updated.", ephemeral=True)
            else:
                tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
                existing_stock_info = next((s for s in tracked_stocks_list if s['symbol'] == upper_symbol), None)
                if existing_stock_info and existing_stock_info.get('quantity') is not None:
                     await ctx.send(f"Successfully tracking {upper_symbol}. Existing portfolio data (Quantity: {existing_stock_info['quantity']}, Price: ${existing_stock_info.get('purchase_price', 0):,.2f}) is maintained.", ephemeral=True)
                else:
                    await ctx.send(f"Successfully started tracking {upper_symbol} (no portfolio data provided/updated).", ephemeral=True)
        else:
            await ctx.send(f"Could not track {upper_symbol}. This might be due to invalid quantity/price format for a new stock, or a database error.", ephemeral=True)

    @commands.hybrid_command(name="untrack_stock", description="Stop tracking a stock symbol.")
    @discord.app_commands.describe(symbol="The stock symbol to untrack (e.g., AAPL, MSFT)")
    @discord.app_commands.autocomplete(symbol=tracked_stock_symbol_autocomplete)
    async def untrack_stock(self, ctx: commands.Context, *, symbol: str) -> None:
        """
        Allows a user to stop tracking a stock symbol.
        """
        await ctx.defer(ephemeral=True)
        
        upper_symbol = symbol.upper()
        user_id = ctx.author.id
        if await self.bot.loop.run_in_executor(None, self.db_manager.remove_tracked_stock, user_id, upper_symbol):
            await ctx.send(f"Successfully stopped tracking {upper_symbol}.", ephemeral=True)
        else:
            await ctx.send(f"Could not untrack {upper_symbol}. It might not be in your list or a database error occurred.", ephemeral=True)

    @commands.hybrid_command(name="my_tracked_stocks", description="Lists your tracked stock symbols.")
    async def my_tracked_stocks(self, ctx: commands.Context) -> None:
        """
        Lists all stock symbols you are currently tracking, along with their current prices.
        Note: Due to API rate limits, fetching prices for many stocks may take some time.
        """
        await ctx.defer(ephemeral=True)
        
        user_id = ctx.author.id
        tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)

        if not tracked_stocks_list:
            await ctx.send("You are not tracking any stocks. Use `/track_stock <symbol>` to add some!", ephemeral=True)
            return

        # Check if there are too many stocks, notify user about time.
        if len(tracked_stocks_list) > 5:
             await ctx.send(f"You have {len(tracked_stocks_list)} tracked stocks. fetching prices might take a while due to API rate limits.", ephemeral=True)

        view = MyStocksPaginatorView(user_id=user_id, items=tracked_stocks_list, bot_instance=self.bot, db_manager=self.db_manager)
        await view.start(ctx, ephemeral=True)

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
    @discord.app_commands.autocomplete(symbol=tracked_stock_symbol_autocomplete)
    async def stock_alert(self, ctx: commands.Context,
                          symbol: str,
                          above_target: typing.Optional[str] = None,
                          below_target: typing.Optional[str] = None,
                          dpc_above_target: typing.Optional[str] = None,
                          dpc_below_target: typing.Optional[str] = None) -> None:
        """
        Sets, updates, or clears price and daily percentage change (DPC) alerts for a tracked stock.
        """
        await ctx.defer(ephemeral=True)
        
        user_id = ctx.author.id
        symbol_upper = symbol.upper()

        tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
        if not any(s['symbol'] == symbol_upper for s in tracked_stocks_list):
            await ctx.send(f"You are not tracking {symbol_upper}. Please use `/track_stock {symbol_upper}` first.", ephemeral=True)
            return

        if above_target is None and below_target is None and dpc_above_target is None and dpc_below_target is None:
            current_alert_info = await self.bot.loop.run_in_executor(None, self.db_manager.get_stock_alert, user_id, symbol_upper)
            if current_alert_info:
                alerts_display = []
                if current_alert_info.get('active_above') and current_alert_info.get('target_above') is not None:
                    alerts_display.append(f"Price Above: ${float(current_alert_info['target_above']):.2f}")
                if current_alert_info.get('active_below') and current_alert_info.get('target_below') is not None:
                    alerts_display.append(f"Price Below: ${float(current_alert_info['target_below']):.2f}")
                if current_alert_info.get('dpc_above_active') and current_alert_info.get('dpc_above_target') is not None:
                    alerts_display.append(f"DPC Above: +{float(current_alert_info['dpc_above_target']):.2f}%")
                if current_alert_info.get('dpc_below_active') and current_alert_info.get('dpc_below_target') is not None:
                    alerts_display.append(f"DPC Below: -{float(current_alert_info['dpc_below_target']):.2f}%")
                
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

        def parse_percentage(value_str: str, param_name: str) -> typing.Optional[typing.Union[float, str]]:
            if value_str is None: return None
            value_str_lower = value_str.lower()
            if value_str_lower == 'clear':
                return "clear_marker"
            try:
                val = float(value_str_lower.rstrip('%'))
                if val <= 0:
                    raise ValueError("Percentage target must be positive.")
                return val
            except ValueError:
                raise ValueError(f"Invalid format for {param_name}")

        def parse_price(value_str: str, param_name: str) -> typing.Optional[typing.Union[float, str]]:
            if value_str is None: return None
            value_str_lower = value_str.lower()
            if value_str_lower == 'clear':
                return "clear_marker"
            try:
                val = float(value_str_lower)
                if val <= 0:
                    raise ValueError("Price target must be positive.")
                return val
            except ValueError:
                raise ValueError(f"Invalid format for {param_name}")

        parse_errors = []

        try:
            if above_target is not None:
                parsed_val = parse_price(above_target, "above_target")
                if parsed_val == "clear_marker": clear_above_flag = True
                else: target_above_val = parsed_val
            
            if below_target is not None:
                parsed_val = parse_price(below_target, "below_target")
                if parsed_val == "clear_marker": clear_below_flag = True
                else: target_below_val = parsed_val

            if dpc_above_target is not None:
                parsed_val = parse_percentage(dpc_above_target, "dpc_above_target")
                if parsed_val == "clear_marker": clear_dpc_above_flag = True
                else: dpc_above_val = parsed_val

            if dpc_below_target is not None:
                parsed_val = parse_percentage(dpc_below_target, "dpc_below_target")
                if parsed_val == "clear_marker": clear_dpc_below_flag = True
                else: dpc_below_val = parsed_val
        except ValueError as ve:
            parse_errors.append(str(ve))
        
        if parse_errors:
            await ctx.send("\n".join(parse_errors), ephemeral=True)
            return

        current_alert = await self.bot.loop.run_in_executor(None, self.db_manager.get_stock_alert, user_id, symbol_upper) or {}
        
        final_above = target_above_val if target_above_val is not None else (None if clear_above_flag else (float(current_alert.get('target_above')) if current_alert.get('target_above') is not None else None))
        final_below = target_below_val if target_below_val is not None else (None if clear_below_flag else (float(current_alert.get('target_below')) if current_alert.get('target_below') is not None else None))

        if final_above is not None and final_below is not None and final_above <= final_below:
            await ctx.send(f"Error: 'Above' target (${final_above:.2f}) must be greater than 'Below' target (${final_below:.2f}). Alert not set/updated.", ephemeral=True)
            return
        
        success = await self.bot.loop.run_in_executor(None, self.db_manager.add_stock_alert,
            user_id, symbol_upper,
            target_above_val, target_below_val,
            dpc_above_val, dpc_below_val,
            clear_above_flag, clear_below_flag,
            clear_dpc_above_flag, clear_dpc_below_flag
        )

        if success:
            if target_above_val is not None: action_summary.append(f"Above target set to ${target_above_val:.2f}")
            if clear_above_flag: action_summary.append("Above target cleared")
            if target_below_val is not None: action_summary.append(f"Below target set to ${target_below_val:.2f}")
            if clear_below_flag: action_summary.append("Below target cleared")
            if dpc_above_val is not None: action_summary.append(f"DPC Above target set to +{dpc_above_val:.2f}%")
            if clear_dpc_above_flag: action_summary.append("DPC Above target cleared")
            if dpc_below_val is not None: action_summary.append(f"DPC Below target set to -{dpc_below_val:.2f}%")
            if clear_dpc_below_flag: action_summary.append("DPC Below target cleared")

            if not action_summary:
                 await ctx.send(f"Alerts for {symbol_upper} processed. No specific changes to report, but settings were applied.", ephemeral=True)
            else:
                await ctx.send(f"Alerts for {symbol_upper} updated: {'; '.join(action_summary)}.", ephemeral=True)
        else:
            await ctx.send(f"No changes made to alerts for {symbol_upper}. Values might be the same as current, or a database error occurred.", ephemeral=True)

    @commands.hybrid_command(name="stock_chart", description="Generate a price chart for a stock symbol over a timespan.")
    @discord.app_commands.describe(
        symbol="The stock symbol (e.g., AAPL, MSFT)",
        timespan=f"The timespan for the chart. Default '1M'. Options: {', '.join(SUPPORTED_TIMESPAN.keys())}"
    )
    @discord.app_commands.autocomplete(symbol=stock_symbol_autocomplete)
    async def stock_chart(self, ctx: commands.Context, symbol: str, timespan: str = "1M") -> None:
        """
        Generates and displays a stock price chart for a given symbol and timespan.
        """
        normalized_symbol = yahoo_finance_client.normalize_symbol(symbol.upper())
        symbol_for_display = symbol.upper()
        timespan_upper = timespan.upper()

        if timespan_upper not in SUPPORTED_TIMESPAN:
            await ctx.send(
                f"Invalid timespan '{timespan}'. Supported timespans are: {', '.join(SUPPORTED_TIMESPAN.keys())}",
                ephemeral=True
            )
            return

        await ctx.defer(ephemeral=False)

        config = SUPPORTED_TIMESPAN[timespan_upper]
        api_params = config["params"].copy()
        display_label = config["label"]

        logger.info(f"Fetching chart data for {symbol_for_display} (normalized: {normalized_symbol}), timespan {timespan_upper} using Alpha Vantage first.")
        data_source = "Alpha Vantage"

        if config["is_intraday"]:
            time_series_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_intraday_time_series, symbol_for_display, api_params['interval'], api_params['outputsize'])
        else:
            time_series_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_daily_time_series, symbol_for_display, api_params['outputsize'])

        alpha_vantage_failed = False
        if not time_series_data:
            logger.warning(f"Alpha Vantage: No time series data for {symbol_for_display} ({display_label}).")
            alpha_vantage_failed = True
        elif isinstance(time_series_data, dict) and "error" in time_series_data:
            logger.warning(f"Alpha Vantage: API error for {symbol_for_display} ({display_label}): {time_series_data.get('message')}")
            alpha_vantage_failed = True
        elif isinstance(time_series_data, list) and not time_series_data:
            logger.warning(f"Alpha Vantage: Empty list returned for {symbol_for_display} ({display_label}).")
            alpha_vantage_failed = True

        if alpha_vantage_failed:
            logger.info(f"Attempting to fetch chart data for {normalized_symbol} via Yahoo Finance.")
            data_source = "Yahoo Finance"
            yahoo_outputsize = "compact" if api_params.get('outputsize') == "compact" else "full"

            if config["is_intraday"]:
                av_interval = api_params['interval']
                if av_interval == "1min": yahoo_interval = "1m"
                elif av_interval == "5min": yahoo_interval = "5m"
                elif av_interval == "15min": yahoo_interval = "15m"
                elif av_interval == "30min": yahoo_interval = "30m"
                elif av_interval == "60min": yahoo_interval = "60m"
                else: yahoo_interval = "60m"
                time_series_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_intraday_time_series, normalized_symbol, yahoo_interval)
            else:
                time_series_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_daily_time_series, normalized_symbol, yahoo_outputsize)
        
        if not time_series_data:
            await ctx.send(f"Could not retrieve time series data for {symbol_for_display} ({display_label}) from any provider. The symbol might be invalid or there's no data.", ephemeral=True)
            return
        if isinstance(time_series_data, dict) and "error" in time_series_data:
            error_message = time_series_data.get("message", "An unspecified API error occurred.")
            await ctx.send(f"Error fetching chart data for {symbol_for_display} ({display_label}): {error_message}", ephemeral=True)
            return
        
        if not isinstance(time_series_data, list) or not time_series_data:
            await ctx.send(f"No valid time series data points found for {symbol_for_display} ({display_label}) from any provider to generate a chart.", ephemeral=True)
            return

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
                        continue
                
                time_series_data = filtered_data
                if not time_series_data:
                    await ctx.send(f"No data found for {symbol_for_display} since the start of this year for the YTD chart.", ephemeral=True)
                    return
            except Exception as e:
                logger.error(f"Error filtering YTD data for {symbol_for_display}: {e}")
                await ctx.send(f"An error occurred while processing YTD data for {symbol_for_display}.", ephemeral=True)
                return

        logger.info(f"Generating chart URL for {symbol_for_display} ({display_label}) with {len(time_series_data)} data points.")
        chart_url = generate_stock_chart_url(symbol_for_display, display_label, time_series_data)

        if chart_url:
            embed = discord.Embed(
                title=f"📈 Stock Chart for {symbol_for_display} ({display_label})",
                color=discord.Color.blue()
            )
            embed.set_image(url=chart_url)
            embed.set_footer(text=f"Chart generated using QuickChart.io | Data from {data_source}")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"Sorry, I couldn't generate the chart for {symbol_for_display} ({display_label}) at this time.", ephemeral=True)

    @commands.hybrid_command(name="stock_news", description="Get recent news for a stock symbol.")
    @discord.app_commands.describe(symbol="The stock symbol (e.g., AAPL, MSFT)")
    @discord.app_commands.autocomplete(symbol=stock_symbol_autocomplete)
    async def stock_news(self, ctx: commands.Context, *, symbol: str) -> None:
        """
        Fetches and displays recent news articles for a given stock symbol.
        """
        await ctx.defer(ephemeral=True)
        upper_symbol = symbol.upper()
        
        news_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_news, upper_symbol, 5)

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
            if i >= 5:
                break

            title = article.get("title", "No Title")
            url = article.get("url", None)
            source = article.get("source", "N/A")
            time_published = article.get("time_published", "N/A")
            summary = article.get("summary", "No summary available.")
            sentiment_label = article.get("sentiment_label", "N/A")

            if len(summary) > 250:
                summary = summary[:247] + "..."
            
            field_title = f"{title}"
            if url:
                field_title = f"🔗 [{title}]({url})"
            else:
                field_title = f"{title}"

            field_value = f"**Source:** {source}\n" \
                          f"**Published:** {time_published}\n" \
                          f"**Sentiment:** {sentiment_label}\n" \
                          f"**Summary:** {summary}"
            
            embed.add_field(name=field_title[:256], value=field_value[:1024], inline=False)

        if not embed.fields:
            await ctx.send(f"📰 No news articles could be formatted for {upper_symbol}.", ephemeral=True)
            return
            
        await ctx.send(embed=embed, ephemeral=False)

    @commands.hybrid_command(name="my_portfolio", description="View your stock portfolio performance.")
    async def my_portfolio(self, ctx: commands.Context) -> None:
        """
        Displays your stock portfolio, including total value, overall gain/loss,
        and performance of individual holdings.
        """
        await ctx.defer(ephemeral=True)
        
        user_id = ctx.author.id
        tracked_stocks_all = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)

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
        individual_holdings_details = []

        status_msg = None
        if len(portfolio_stocks) > 1:
            status_msg = await ctx.send(f"Fetching current prices for {len(portfolio_stocks)} holdings... this may take a moment due to API rate limits.", ephemeral=True)

        for stock_data in portfolio_stocks:
            symbol = stock_data["symbol"]
            quantity = stock_data["quantity"]
            purchase_price = stock_data["purchase_price"]

            if api_call_count > 0:
                await asyncio.sleep(13)

            current_price_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_price, symbol)
            data_source = "Alpha Vantage"
            api_call_count += 1

            if not current_price_data or "error" in current_price_data:
                current_price_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_price, symbol)
                data_source = "Yahoo Finance"

            current_price = None
            currency = "USD"
            api_error_for_stock = False

            if current_price_data and "05. price" in current_price_data:
                try:
                    current_price = float(current_price_data["05. price"])
                    currency = current_price_data.get('currency', 'USD')
                except (ValueError, TypeError):
                    logger.error(f"Portfolio: Could not parse current price for {symbol}. Data: {current_price_data}")
                    api_error_for_stock = True
            else:
                api_error_for_stock = True

            currency_symbols = {'USD': '$', 'PLN': 'zł', 'EUR': '€', 'GBP': '£', 'CAD': 'C$', 'JPY': '¥'}
            currency_symbol = currency_symbols.get(currency, currency)

            cost_basis = quantity * purchase_price
            market_value = 0.0
            gain_loss = 0.0
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
                "currency": currency,
                "currency_symbol": currency_symbol,
                "cost_basis": cost_basis,
                "market_value": market_value,
                "gain_loss": gain_loss,
                "gain_loss_pct_str": gain_loss_pct_str,
                "data_source": data_source
            })

        overall_gain_loss = overall_market_value - overall_cost_basis
        overall_gain_loss_pct_str = "N/A"
        if overall_cost_basis != 0 and isinstance(overall_market_value, (int, float)) and overall_market_value > 0 :
            overall_gain_loss_pct = (overall_gain_loss / overall_cost_basis) * 100
            overall_gain_loss_pct_str = f"{overall_gain_loss_pct:+.2f}%"
        elif overall_cost_basis == 0 and isinstance(overall_market_value, (int, float)) and overall_market_value > 0:
             overall_gain_loss_pct_str = "+∞%"
        elif overall_cost_basis == 0 and isinstance(overall_market_value, (int, float)) and overall_market_value == 0:
             overall_gain_loss_pct_str = "N/A"

        summary_color = discord.Color.default()
        if isinstance(overall_gain_loss, (int,float)):
            if overall_gain_loss > 0: summary_color = discord.Color.green()
            elif overall_gain_loss < 0: summary_color = discord.Color.red()

        embed.color = summary_color

        currencies_used = set(item.get('currency', 'USD') for item in individual_holdings_details if isinstance(item.get('current_price'), (int, float)))
        mixed_currencies = len(currencies_used) > 1
        
        if mixed_currencies:
            overall_market_value_display = f"${overall_market_value:,.2f}" if isinstance(overall_market_value, (int, float)) else str(overall_market_value)
            overall_gain_loss_display = f"${overall_gain_loss:,.2f}" if isinstance(overall_gain_loss, (int, float)) else str(overall_gain_loss)
            currency_note = " (mixed currencies, totals approximate)"
            cost_basis_display = f"${overall_cost_basis:,.2f}"
        else:
            single_currency = list(currencies_used)[0] if currencies_used else 'USD'
            summary_currency_symbol = currency_symbols.get(single_currency, single_currency)
            
            if single_currency == 'PLN':
                overall_market_value_display = f"{overall_market_value:,.2f} {summary_currency_symbol}" if isinstance(overall_market_value, (int, float)) else str(overall_market_value)
                overall_gain_loss_display = f"{overall_gain_loss:,.2f} {summary_currency_symbol}" if isinstance(overall_gain_loss, (int, float)) else str(overall_gain_loss)
                cost_basis_display = f"{overall_cost_basis:,.2f} {summary_currency_symbol}"
            else:
                overall_market_value_display = f"{summary_currency_symbol}{overall_market_value:,.2f}" if isinstance(overall_market_value, (int, float)) else str(overall_market_value)
                overall_gain_loss_display = f"{summary_currency_symbol}{overall_gain_loss:,.2f}" if isinstance(overall_gain_loss, (int, float)) else str(overall_gain_loss)
                cost_basis_display = f"{summary_currency_symbol}{overall_cost_basis:,.2f}"
            currency_note = ""

        embed.add_field(
            name="📈 Overall Portfolio Summary",
            value=(
                f"**Total Market Value:** {overall_market_value_display}{currency_note}\n"
                f"**Total Cost Basis:** {cost_basis_display}\n"
                f"**Total Gain/Loss:** {overall_gain_loss_display} ({overall_gain_loss_pct_str})"
            ),
            inline=False
        )

        holdings_text_parts = []
        for item in individual_holdings_details:
            symbol_header = f"--- **{item['symbol']}** ---"
            
            currency_symbol = item.get('currency_symbol', '$')
            
            if isinstance(item['current_price'], (int, float)):
                if item.get('currency') == 'PLN':
                    current_price_display = f"{item['current_price']:,.2f} {currency_symbol}"
                else:
                    current_price_display = f"{currency_symbol}{item['current_price']:,.2f}"
            else:
                current_price_display = str(item['current_price'])
            
            if isinstance(item['market_value'], (int, float)):
                if item.get('currency') == 'PLN':
                    market_value_display = f"{item['market_value']:,.2f} {currency_symbol}"
                else:
                    market_value_display = f"{currency_symbol}{item['market_value']:,.2f}"
            else:
                market_value_display = str(item['market_value'])
            
            if isinstance(item['gain_loss'], (int, float)):
                if item.get('currency') == 'PLN':
                    gain_loss_display_val = f"{item['gain_loss']:+,.2f} {currency_symbol}"
                else:
                    gain_loss_display_val = f"{currency_symbol}{item['gain_loss']:+,.2f}"
            else:
                gain_loss_display_val = str(item['gain_loss'])

            if item.get('currency') == 'PLN':
                purchase_price_display = f"{item['purchase_price']:,.2f} {currency_symbol}"
                cost_basis_display = f"{item['cost_basis']:,.2f} {currency_symbol}"
            else:
                purchase_price_display = f"{currency_symbol}{item['purchase_price']:,.2f}"
                cost_basis_display = f"{currency_symbol}{item['cost_basis']:,.2f}"

            gain_loss_emoji = ""
            if isinstance(item['gain_loss'], (int, float)):
                if item['gain_loss'] > 0: gain_loss_emoji = "🔼 "
                elif item['gain_loss'] < 0: gain_loss_emoji = "🔽 "
            
            details = (
                f"{symbol_header}\n"
                f"Quantity: `{item['quantity']}` @ Avg Cost: `{purchase_price_display}`\n"
                f"Cost Basis: `{cost_basis_display}`\n"
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
                if field_count > 0 :
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
            
            if current_field_value:
                field_name = "Individual Holdings"
                if field_count > 0:
                    field_name = "Individual Holdings (Continued)"
                embed.add_field(name=field_name, value=current_field_value, inline=False)
        else:
            embed.add_field(name="Individual Holdings", value="No holdings data to display.", inline=False)

        if status_msg:
            try:
                await status_msg.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                logger.warning(f"Could not delete portfolio status message: {e}")

        data_sources_used = set(item.get('data_source', 'Alpha Vantage') for item in individual_holdings_details)
        if len(data_sources_used) > 1:
            footer_text = f"Data provided by {' & '.join(sorted(data_sources_used))}. Prices may be delayed."
        else:
            footer_text = f"Data provided by {list(data_sources_used)[0] if data_sources_used else 'Alpha Vantage'}. Prices may be delayed."
        embed.set_footer(text=footer_text)

        await ctx.send(embed=embed, ephemeral=False)

    @commands.hybrid_command(name="stock_debug", description="Debug stock API connections for a symbol.")
    @discord.app_commands.describe(symbol="The stock symbol to debug (e.g., LPP, AAPL)")
    @discord.app_commands.autocomplete(symbol=stock_symbol_autocomplete)
    async def stock_debug(self, ctx: commands.Context, *, symbol: str) -> None:
        """
        Debug command to test both Alpha Vantage and Yahoo Finance APIs for a symbol.
        """
        if not await self._is_admin_or_owner(ctx):
            await ctx.send("This command is restricted to bot administrators.", ephemeral=True)
            return
            
        upper_symbol = symbol.upper()
        
        embed = discord.Embed(title=f"🔧 Stock API Debug for {upper_symbol}", color=discord.Color.blue())
        
        av_result = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_price, upper_symbol)
        
        if av_result is None:
            av_status = "❌ Failed - No data returned"
        elif "error" in av_result:
            av_status = f"❌ Error - {av_result.get('error')}: {av_result.get('message', 'Unknown error')}"
        elif "01. symbol" in av_result and "05. price" in av_result:
            av_status = f"✅ Success - Price: ${av_result['05. price']}"
        else:
            av_status = f"⚠️ Unexpected format - {av_result}"
        
        embed.add_field(name="🔍 Alpha Vantage Test", value=av_status, inline=False)
        
        normalized_symbol = yahoo_finance_client.normalize_symbol(upper_symbol)
        yf_result = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_price, normalized_symbol)
        
        if yf_result is None:
            yf_status = "❌ Failed - No data returned"
        elif "error" in yf_result:
            yf_status = f"❌ Error - {yf_result.get('error')}: {yf_result.get('message', 'Unknown error')}"
        elif "01. symbol" in yf_result and "05. price" in yf_result:
            currency = yf_result.get('currency', 'USD')
            exchange = yf_result.get('exchange', 'Unknown')
            yf_status = f"✅ Success - Price: {yf_result['05. price']} {currency} ({exchange})"
        else:
            yf_status = f"⚠️ Unexpected format - {yf_result}"
        
        embed.add_field(name=f"🔍 Yahoo Finance Test ({upper_symbol} → {normalized_symbol})", value=yf_status, inline=False)
        
        if (av_result and "01. symbol" in av_result) or (yf_result and "01. symbol" in yf_result):
            recommendation = "✅ At least one API is working - stock_price command should succeed"
        else:
            recommendation = "❌ Both APIs failed - stock_price command will fail"
        
        embed.add_field(name="📋 Recommendation", value=recommendation, inline=False)
        embed.set_footer(text="This is a diagnostic command for troubleshooting")
        
        await ctx.send(embed=embed, ephemeral=True)
    
    async def _is_admin_or_owner(self, ctx: commands.Context) -> bool:
        app_info = await self.bot.application_info()
        if ctx.author.id == app_info.owner.id:
            return True
        
        if hasattr(ctx.author, 'guild_permissions') and ctx.author.guild_permissions.administrator:
            return True
            
        return False

    @commands.command(name="stock_alert_set", aliases=["alert_set"])
    async def stock_alert_set(self, ctx: commands.Context, symbol: str, direction: str, target: float) -> None:
        """
        Simple command to set a stock price alert.
        """
        user_id = ctx.author.id
        symbol_upper = symbol.upper()
        direction_lower = direction.lower()
        
        tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
        if not any(s['symbol'] == symbol_upper for s in tracked_stocks_list):
            await ctx.send(f"❌ You are not tracking {symbol_upper}. Please use `!track_stock {symbol_upper}` first.")
            return
            
        if direction_lower not in ['above', 'below']:
            await ctx.send(f"❌ Direction must be 'above' or 'below', not '{direction}'.")
            return
            
        if target <= 0:
            await ctx.send(f"❌ Target price must be positive, not {target}.")
            return
            
        try:
            if direction_lower == 'above':
                success = await self.bot.loop.run_in_executor(None, self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=target, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=False, clear_below=False,
                    clear_dpc_above=False, clear_dpc_below=False
                )
                if success:
                    await ctx.send(f"✅ Alert set for {symbol_upper}: notify when price goes **above ${target:.2f}**")
                else:
                    await ctx.send(f"❌ Failed to set alert for {symbol_upper}. It might already be set to this value.")
            else:
                success = await self.bot.loop.run_in_executor(None, self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=None, target_below=target,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=False, clear_below=False,
                    clear_dpc_above=False, clear_dpc_below=False
                )
                if success:
                    await ctx.send(f"✅ Alert set for {symbol_upper}: notify when price goes **below ${target:.2f}**")
                else:
                    await ctx.send(f"❌ Failed to set alert for {symbol_upper}. It might already be set to this value.")
                    
        except Exception as e:
            await ctx.send(f"❌ Error setting alert: {str(e)}")

    @commands.command(name="stock_alert_clear", aliases=["alert_clear"])
    async def stock_alert_clear(self, ctx: commands.Context, symbol: str, direction: str = "all") -> None:
        """
        Clear stock alerts.
        """
        user_id = ctx.author.id
        symbol_upper = symbol.upper()
        direction_lower = direction.lower()
        
        tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
        if not any(s['symbol'] == symbol_upper for s in tracked_stocks_list):
            await ctx.send(f"❌ You are not tracking {symbol_upper}.")
            return
            
        if direction_lower not in ['above', 'below', 'all']:
            await ctx.send(f"❌ Direction must be 'above', 'below', or 'all', not '{direction}'.")
            return
            
        try:
            if direction_lower == 'all':
                success = await self.bot.loop.run_in_executor(None, self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=None, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=True, clear_below=True,
                    clear_dpc_above=True, clear_dpc_below=True
                )
                if success:
                    await ctx.send(f"✅ All alerts cleared for {symbol_upper}")
                else:
                    await ctx.send(f"❌ Failed to clear alerts for {symbol_upper}")
            elif direction_lower == 'above':
                success = await self.bot.loop.run_in_executor(None, self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=None, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=True, clear_below=False,
                    clear_dpc_above=False, clear_dpc_below=False
                )
                if success:
                    await ctx.send(f"✅ 'Above' alert cleared for {symbol_upper}")
                else:
                    await ctx.send(f"❌ Failed to clear 'above' alert for {symbol_upper}")
            else:
                success = await self.bot.loop.run_in_executor(None, self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=None, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=False, clear_below=True,
                    clear_dpc_above=False, clear_dpc_below=False
                )
                if success:
                    await ctx.send(f"✅ 'Below' alert cleared for {symbol_upper}")
                else:
                    await ctx.send(f"❌ Failed to clear 'below' alert for {symbol_upper}")
                    
        except Exception as e:
            await ctx.send(f"❌ Error clearing alert: {str(e)}")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Stocks(bot, db_manager=bot.db_manager))
    print("Stocks Cog has been loaded and stock alert task initialized.")
