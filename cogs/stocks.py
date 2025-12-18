# cogs/stocks.py

import discord
import asyncio # Added for rate limiting
import functools # Added for partial
import logging # For background task logging
import typing # For type hinting
import re
import datetime
import io
from discord.ext import commands, tasks
from api_clients import alpha_vantage_client
from api_clients.alpha_vantage_client import get_daily_time_series, get_intraday_time_series # Added
from api_clients import yahoo_finance_client # Added Yahoo Finance support
from api_clients import google_news_rss_client
from utils.chart_utils import generate_stock_chart_url, get_stock_chart_image # Added
from data_manager import DataManager # Import DataManager class
import config
from utils.article_utils import extract_readable_text_from_html, clamp_text, looks_like_html
# Individual function imports from data_manager are no longer needed if using an instance

# Gemini (google-genai)
try:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore
except Exception:  # pragma: no cover
    genai = None
    types = None

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
        self.db_manager = bot.db_manager # Get the DataManager instance from the bot
        self.unique_stocks_queue = []
        self.current_queue_index = 0
        self.check_stock_alerts.start() # Start the background task
        self.check_portfolio_analysis_schedules.start()

        # Gemini client for analysis (optional)
        self._gemini_client = None
        if getattr(config, "GEMINI_API_KEY", None) and genai is not None:
            try:
                self._gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
            except Exception:
                self._gemini_client = None

    def cog_unload(self):
        self.check_stock_alerts.cancel() # Ensure the task is cancelled on cog unload
        self.check_portfolio_analysis_schedules.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        print("Stocks Cog is ready.")
        logger.info("Stocks Cog is ready and stock alert monitoring task is running.")

    def _time_hhmm_pattern(self):
        return re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

    async def _is_user_in_dnd(self, user_id: int) -> bool:
        """
        Best-effort DND check using existing preference keys.
        """
        if not self.db_manager:
            return False
        try:
            dnd_enabled = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_enabled", False)
            if not dnd_enabled:
                return False
            dnd_start_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_start_time", "00:00")
            dnd_end_str = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "dnd_end_time", "00:00")
            try:
                start_t = datetime.datetime.strptime(str(dnd_start_str), "%H:%M").time()
                end_t = datetime.datetime.strptime(str(dnd_end_str), "%H:%M").time()
            except Exception:
                return False
            now_t = datetime.datetime.now().time()
            if start_t == end_t:
                return False
            if start_t < end_t:
                return start_t <= now_t < end_t
            return now_t >= start_t or now_t < end_t
        except Exception:
            return False

    async def _dm_user(self, user_id: int, *, embed: discord.Embed) -> bool:
        user = self.bot.get_user(int(user_id))
        if not user:
            try:
                user = await self.bot.fetch_user(int(user_id))
            except Exception:
                return False
        try:
            await user.send(embed=embed)
            return True
        except Exception:
            return False

    async def _fetch_stock_news_any_provider(self, symbol: str, limit: int = 5) -> typing.Optional[typing.List[dict]]:
        """
        Prefer Google News RSS (free/no key), fallback to Yahoo.
        """
        sym = str(symbol or "").strip().upper()
        limit = max(1, min(25, int(limit)))
        if not sym:
            return None

        rss_news = await self.bot.loop.run_in_executor(None, google_news_rss_client.get_stock_news, sym, limit)
        if isinstance(rss_news, list) and rss_news:
            return rss_news

        # Yahoo fallback (sometimes has better direct ticker linkage)
        yf_news = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_news, sym, limit)
        return yf_news if isinstance(yf_news, list) and yf_news else None

    def _news_to_brief_text(self, symbol: str, items: typing.List[dict]) -> str:
        sym = str(symbol or "").upper()
        lines = [f"### {sym}"]
        for it in (items or [])[:10]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "Untitled").strip()
            src = str(it.get("source") or "").strip()
            ts = str(it.get("time_published") or "").strip()
            sent = str(it.get("sentiment_label") or "").strip()
            url = str(it.get("url") or "").strip()
            summary = str(it.get("summary") or "").strip()
            if len(summary) > 280:
                summary = summary[:277] + "..."
            meta = " | ".join([x for x in [src, ts, sent] if x])
            if url:
                lines.append(f"- {title} ({meta}) [{url}]")
            else:
                lines.append(f"- {title} ({meta})")
            if summary:
                lines.append(f"  - {summary}")
        return "\n".join(lines)

    def _fetch_article_text_blocking(self, url: str, *, timeout_s: int = 12) -> str:
        """
        Blocking HTTP fetch + extraction. Run in executor.
        Returns extracted article text or empty string.
        """
        if not isinstance(url, str) or not url.strip():
            return ""
        u = url.strip()
        try:
            import requests

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            r = requests.get(u, headers=headers, timeout=timeout_s, allow_redirects=True)
            ct = r.headers.get("content-type", "") if hasattr(r, "headers") else ""
            text = r.text if getattr(r, "text", None) is not None else ""
            if not text:
                return ""

            if not looks_like_html(ct, text):
                return clamp_text(text, max_chars=12000)

            extracted = extract_readable_text_from_html(text)
            return extracted
        except Exception:
            return ""

    async def _fetch_article_text(self, url: str) -> str:
        return await asyncio.get_running_loop().run_in_executor(None, lambda: self._fetch_article_text_blocking(url))

    async def _news_to_fulltext_context(
        self,
        symbol: str,
        items: typing.List[dict],
        *,
        max_articles: int,
        per_article_chars: int,
    ) -> str:
        sym = str(symbol or "").upper()
        out: list[str] = [f"### {sym}"]
        items2 = [it for it in (items or []) if isinstance(it, dict)][:max_articles]

        # Fetch article texts concurrently but bounded, since this uses executor threads.
        sem = asyncio.Semaphore(5)

        async def _fetch_one(u: str) -> str:
            if not u:
                return ""
            async with sem:
                return await self._fetch_article_text(u)

        urls = [str(it.get("url") or "").strip() for it in items2]
        fetched = await asyncio.gather(*[_fetch_one(u) for u in urls], return_exceptions=True)

        for idx, it in enumerate(items2):
            title = str(it.get("title") or "Untitled").strip()
            src = str(it.get("source") or "").strip()
            ts = str(it.get("time_published") or "").strip()
            sent = str(it.get("sentiment_label") or "").strip()
            url = str(it.get("url") or "").strip()
            summary = str(it.get("summary") or "").strip()

            meta = " | ".join([x for x in [src, ts, sent] if x])
            out.append(f"- {title} ({meta})")
            if url:
                out.append(f"  URL: {url}")

            article_text = ""
            try:
                v = fetched[idx]
                if isinstance(v, str):
                    article_text = v
            except Exception:
                article_text = ""

            if article_text:
                article_text = clamp_text(article_text, max_chars=per_article_chars)
                out.append("  ARTICLE:")
                out.append("  " + article_text.replace("\n", "\n  "))
            else:
                if summary:
                    out.append("  SUMMARY:")
                    out.append("  " + clamp_text(summary, max_chars=min(2000, per_article_chars)).replace("\n", " "))
                else:
                    out.append("  SUMMARY: (unavailable)")

        return "\n".join(out).strip()

    async def _gemini_summarize(self, prompt: str, *, max_output_tokens: int = 900) -> str:
        if self._gemini_client is None or types is None:
            raise RuntimeError("Gemini is not configured (missing GEMINI_API_KEY or google-genai)")
        cfg = types.GenerateContentConfig(temperature=0.2, max_output_tokens=int(max_output_tokens))
        resp = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._gemini_client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=cfg,
            ),
        )
        return str(getattr(resp, "text", "") or "").strip()

    @tasks.loop(minutes=STOCK_CHECK_INTERVAL_MINUTES)
    async def check_stock_alerts(self):
        if not self.db_manager:
            logger.error("StocksCog: DataManager (db_manager) not available. Cannot check stock alerts.")
            return

        logger.info("Stock alert check task running...")
        all_user_alerts_map = await self.bot.loop.run_in_executor(None, self.db_manager.get_all_active_alerts_for_monitoring) # Returns dict {user_id: {symbol: alert_details}}

        if not all_user_alerts_map:
            logger.info("No active stock alerts to monitor.")
            return

        # Extract all unique symbols from the map
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

        # Update queue if the set of unique symbols has changed
        if set(latest_unique_symbols) != set(self.unique_stocks_queue):
            logger.info(f"Unique stock list changed. Old: {self.unique_stocks_queue}, New: {latest_unique_symbols}")
            self.unique_stocks_queue = latest_unique_symbols
            self.current_queue_index = 0 # Reset index if list changes

        if not self.unique_stocks_queue:
            logger.info("Stock monitoring queue is empty after update.")
            return
        
        if self.current_queue_index >= len(self.unique_stocks_queue):
            self.current_queue_index = 0

        symbol_to_check = self.unique_stocks_queue[self.current_queue_index]
        logger.info(f"Selected stock for current check: {symbol_to_check}")

        self.current_queue_index = (self.current_queue_index + 1) % len(self.unique_stocks_queue)
        
        await asyncio.sleep(2) # Small delay
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

        # Iterate through users who have alerts for this specific symbol_to_check
        for user_id_str, user_specific_alerts_dict in all_user_alerts_map.items():
            if symbol_to_check not in user_specific_alerts_dict:
                continue # This user doesn't have an alert for the current symbol

            alert_details = user_specific_alerts_dict[symbol_to_check] # This is the dict of alert conditions
            user_id_int = int(user_id_str) # Convert string user_id to int
            
            discord_user_obj = await self.bot.fetch_user(user_id_int)
            if not discord_user_obj:
                logger.warning(f"Could not find user {user_id_int} for alert on {symbol_to_check}.")
                continue

            triggered_message = None
            deactivate_direction = None

            # --- Price Target Checks ---
            # alert_details keys are like 'target_above', 'active_above', etc.
            if alert_details.get('active_above') and alert_details.get('target_above') is not None:
                if current_price > float(alert_details['target_above']): # Ensure comparison with float
                    triggered_message = f"📈 **Price Alert!** {symbol_to_check} has risen above your target of ${float(alert_details['target_above']):.2f}. Current price: ${current_price:.2f}"
                    deactivate_direction = "above"
            
            if not triggered_message and alert_details.get('active_below') and alert_details.get('target_below') is not None:
                if current_price < float(alert_details['target_below']): # Ensure comparison with float
                    triggered_message = f"📉 **Price Alert!** {symbol_to_check} has fallen below your target of ${float(alert_details['target_below']):.2f}. Current price: ${current_price:.2f}"
                    deactivate_direction = "below"

            # --- Daily Percentage Change (DPC) Target Checks ---
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
    async def before_check_stock_alerts(self):
        await self.bot.wait_until_ready()
        logger.info("Stock alert monitoring task is waiting for bot to be ready...")

    # -------------------------
    # Scheduled portfolio analysis (DM)
    # -------------------------
    @tasks.loop(minutes=1)
    async def check_portfolio_analysis_schedules(self):
        if not self.db_manager:
            return
        if self._gemini_client is None:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        current_time = now.strftime("%H:%M")  # UTC HH:MM
        rows = await self.bot.loop.run_in_executor(None, self.db_manager.get_portfolio_analysis_schedules_for_time, current_time)
        if not rows:
            return

        for r in rows or []:
            try:
                uid = int(r.get("user_id"))
            except Exception:
                continue

            try:
                if await self._is_user_in_dnd(uid):
                    continue
            except Exception:
                pass

            tracked = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, uid)
            symbols = [str(s.get("symbol") or "").upper() for s in (tracked or []) if isinstance(s, dict) and s.get("symbol")]
            symbols = [s for s in symbols if s]
            if not symbols:
                continue

            symbols = symbols[:8]
            briefs: list[str] = []
            for sym in symbols:
                news = await self._fetch_stock_news_any_provider(sym, limit=5)
                if not news:
                    briefs.append(f"### {sym}\n- No news found.")
                else:
                    briefs.append(await self._news_to_fulltext_context(sym, news, max_articles=5, per_article_chars=3500))

            context = "\n\n".join(briefs)
            prompt = (
                "You are a financial news assistant. Create a portfolio news briefing.\n"
                "Rules:\n"
                "- Do NOT provide investment advice. Do not tell the user to buy/sell.\n"
                "- Focus on what changed in the news and what to watch next.\n"
                "- Keep it short (<= 12 bullets total), and group by symbol.\n\n"
                f"Portfolio symbols: {', '.join(symbols)}\n\n"
                "Use the included full article text when present, otherwise use summaries.\n\n"
                f"{context}\n"
            )
            try:
                text = await self._gemini_summarize(prompt)
            except Exception:
                continue
            if not text:
                continue

            embed = discord.Embed(
                title="📬 Portfolio analysis (scheduled)",
                description=(text[:3900] + ("…" if len(text) > 3900 else "")),
                color=discord.Color.dark_gold(),
                timestamp=now,
            )
            embed.set_footer(text="Gemini 3 Flash • Not financial advice • Data: Google News RSS (with Yahoo fallback)")
            await self._dm_user(uid, embed=embed)

    @check_portfolio_analysis_schedules.before_loop
    async def before_check_portfolio_analysis_schedules(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="stock_price", description="Get the current price of a stock.")
    @discord.app_commands.describe(symbol="The stock symbol (e.g., AAPL, MSFT, LPP.WA)")
    async def stock_price(self, ctx: commands.Context, *, symbol: str):
        """
        Fetches and displays the current price and other relevant information for a given stock symbol.
        Supports both US stocks (Alpha Vantage) and international stocks like Polish stocks (Yahoo Finance).

        Usage examples:
        `!stock_price AAPL`
        `/stock_price symbol:TSLA`
        `/stock_price symbol:LPP` (Polish stock, auto-converted to LPP.WA)
        `/stock_price symbol:LPP.WA` (Polish stock, explicit format)
        """
        await ctx.defer(ephemeral=True)
        
        logger.info(f"[STOCK_PRICE_DEBUG] Command received for symbol: {symbol}")
        upper_symbol = symbol.upper()
        
        # First try Alpha Vantage (for US stocks)
        logger.info(f"[STOCK_PRICE_DEBUG] Attempting Alpha Vantage for {upper_symbol}")
        price_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_price, upper_symbol)
        data_source = "Alpha Vantage"
        logger.info(f"[STOCK_PRICE_DEBUG] Alpha Vantage raw response for {upper_symbol}: {price_data}")

        # If Alpha Vantage fails or has API limits, try Yahoo Finance as fallback
        if not price_data or "error" in price_data:
            if price_data and price_data.get("error") == "api_limit":
                logger.info(f"[STOCK_PRICE_DEBUG] Alpha Vantage API limit for {upper_symbol}. Falling back to Yahoo.")
            else:
                logger.info(f"[STOCK_PRICE_DEBUG] Alpha Vantage failed for {upper_symbol} (Data: {price_data}). Falling back to Yahoo.")
            
            # Try Yahoo Finance
            logger.info(f"[STOCK_PRICE_DEBUG] Attempting Yahoo Finance for {upper_symbol} (normalized to {yahoo_finance_client.normalize_symbol(upper_symbol)})")
            price_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_price, upper_symbol)
            data_source = "Yahoo Finance"
            logger.info(f"[STOCK_PRICE_DEBUG] Yahoo Finance raw response for {yahoo_finance_client.normalize_symbol(upper_symbol)}: {price_data}")
            
            # If Yahoo Finance also fails, we're out of options
            if not price_data:
                logger.info(f"[STOCK_PRICE_DEBUG] Yahoo Finance also failed for {yahoo_finance_client.normalize_symbol(upper_symbol)}. No more APIs to try.")
        
        # Check if we have valid data from any source
        if not price_data:
            logger.error(f"[STOCK_PRICE_DEBUG] All APIs failed for {upper_symbol}.")
            embed = discord.Embed(
                title="❌ Stock Not Found",
                description=f"Could not retrieve data for **{upper_symbol}** from Alpha Vantage or Yahoo Finance.\n\n" +
                           f"Please check the symbol and try again.\n\n" +
                           f"💡 **Tip**: For Polish stocks, try adding `.WA` suffix (e.g., `{upper_symbol}.WA`)",
                color=discord.Color.red()
            )
            await ctx.followup.send(embed=embed)
            return

        if price_data: # This block now processes data from AV or YF
            if "error" in price_data: # This error is now from the *second* attempt if AV failed
                error_type = price_data["error"]
                error_message = price_data.get("message", "An unspecified error occurred.")
                logger.error(f"[STOCK_PRICE_DEBUG] Final data source ({data_source}) reported error for {upper_symbol}: Type: {error_type}, Msg: {error_message}")
                if error_type == "api_limit":
                    await ctx.followup.send(f"Could not retrieve price for {upper_symbol}: {error_message}")
                elif error_type == "config_error":
                    print(f"Stock price configuration error for {upper_symbol}: {error_message}") # Log server-side
                    await ctx.followup.send(f"Could not retrieve price for {upper_symbol} due to a server configuration issue. Please notify the bot administrator.")
                elif error_type == "api_error":
                    await ctx.followup.send(f"Could not retrieve price for {upper_symbol}: {error_message}")
                else: # Unknown error type in dictionary
                    print(f"Stock price: Unknown error type '{error_type}' for {upper_symbol}: {error_message}")
                    await ctx.followup.send(f"Error fetching data for {upper_symbol}. An unexpected error occurred with the data provider.")
            elif "01. symbol" in price_data and "05. price" in price_data: # Success from either AV or YF
                logger.info(f"[STOCK_PRICE_DEBUG] Successfully processed data for {upper_symbol} from {data_source}.")
                stock_symbol_from_api = price_data['01. symbol']
                
                # Get currency from the API response, default to USD for Alpha Vantage
                currency = price_data.get('currency', 'USD')
                
                # Determine currency symbol for display
                currency_symbols = {
                    'USD': '$',
                    'PLN': 'zł',
                    'EUR': '€',
                    'GBP': '£',
                    'CAD': 'C$',
                    'JPY': '¥'
                }
                currency_symbol = currency_symbols.get(currency, currency)
                
                # Helper to safely get and format numbers
                def get_formatted_value(key, prefix="", suffix="", is_numeric=True, is_currency=False, is_volume=False):
                    value = price_data.get(key)
                    if value is None or value == "":
                        return "N/A"
                    try:
                        if is_numeric:
                            num_value = float(value.rstrip('%')) # Remove % for change percent
                            if is_currency:
                                return f"{num_value:,.2f} {currency_symbol}" if currency == 'PLN' else f"{currency_symbol}{num_value:,.2f}"
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

                price = get_formatted_value('05. price', is_currency=True)
                change_val_str = price_data.get('09. change', '0') # Default to '0' for float conversion
                change_percent_val_str = price_data.get('10. change percent', '0%') # Default to '0%' for float conversion
                
                change_display = get_formatted_value('09. change')
                change_percent_display = get_formatted_value('10. change percent')

                day_high = get_formatted_value('03. high', is_currency=True)
                day_low = get_formatted_value('04. low', is_currency=True)
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
                
                # Add exchange info for international stocks
                if currency != 'USD' and 'exchange' in price_data:
                    embed.add_field(name="🏢 Exchange", value=price_data['exchange'], inline=True)
                
                embed.set_footer(text=f"Data provided by {data_source}")
                await ctx.send(embed=embed)
            else: # price_data is a dictionary, but not a known error type and not a success structure
                logger.error(f"[STOCK_PRICE_DEBUG] Unexpected data structure for {upper_symbol} from {data_source}: {price_data}")
                print(f"Stock price: Unexpected data structure for {symbol.upper()}: {price_data}")
                await ctx.send(f"Error fetching data for {symbol.upper()}. Unexpected data format received from the provider.")
        else: # price_data is None (e.g., network issue, client-side timeout before API response) - This implies BOTH AV and YF returned None
            logger.error(f"[STOCK_PRICE_DEBUG] Both Alpha Vantage and Yahoo Finance returned None for {upper_symbol}.")
            await ctx.send(f"Error fetching data for {symbol.upper()}. Could not connect to the data provider or the symbol is invalid.")


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

        await ctx.defer(ephemeral=True)
        
        # Attempt to add/update the stock
        # The self.db_manager.add_tracked_stock now handles the logic of adding vs updating
        # and whether portfolio data is new, updated, or absent.
        success = await self.bot.loop.run_in_executor(None, self.db_manager.add_tracked_stock, user_id, upper_symbol, quantity, purchase_price)

        if success:
            if quantity is not None and purchase_price is not None:
                await ctx.send(f"Successfully tracking {upper_symbol} with {quantity} shares at ${purchase_price:,.2f} each. Portfolio data updated.", ephemeral=True)
            else:
                # Check if it was already tracked with portfolio data that's being kept, or just simple tracking
                tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id) # Returns list of dicts
                existing_stock_info = next((s for s in tracked_stocks_list if s['symbol'] == upper_symbol), None)
                if existing_stock_info and existing_stock_info.get('quantity') is not None:
                     await ctx.send(f"Successfully tracking {upper_symbol}. Existing portfolio data (Quantity: {existing_stock_info['quantity']}, Price: ${existing_stock_info.get('purchase_price', 0):,.2f}) is maintained.", ephemeral=True)
                else:
                    await ctx.send(f"Successfully started tracking {upper_symbol} (no portfolio data provided/updated).", ephemeral=True)
        else:
            # This 'else' from db_manager.add_tracked_stock usually means a DB operation failure
            # or if quantity/price was partially provided for a new stock.
            await ctx.send(f"Could not track {upper_symbol}. This might be due to invalid quantity/price format for a new stock, or a database error.", ephemeral=True)

    @commands.hybrid_command(name="untrack_stock", description="Stop tracking a stock symbol.")
    @discord.app_commands.describe(symbol="The stock symbol to untrack (e.g., AAPL, MSFT)")
    async def untrack_stock(self, ctx: commands.Context, *, symbol: str):
        """
        Allows a user to stop tracking a stock symbol.

        Usage examples:
        `!untrack_stock MSFT`
        `/untrack_stock symbol:NVDA`
        """
        await ctx.defer(ephemeral=True)
        
        upper_symbol = symbol.upper()
        user_id = ctx.author.id
        if await self.bot.loop.run_in_executor(None, self.db_manager.remove_tracked_stock, user_id, upper_symbol): # Returns True on successful commit
            await ctx.send(f"Successfully stopped tracking {upper_symbol}.", ephemeral=True)
        else:
            # This now implies a DB operation failure, as "not found" doesn't make the DB operation fail.
            # To check if it was found, we'd query before deleting.
            await ctx.send(f"Could not untrack {upper_symbol}. It might not be in your list or a database error occurred.", ephemeral=True)

    @commands.hybrid_command(name="my_tracked_stocks", description="Lists your tracked stock symbols.")
    async def my_tracked_stocks(self, ctx: commands.Context):
        """
        Lists all stock symbols you are currently tracking, along with their current prices.
        Note: Due to API rate limits, fetching prices for many stocks may take some time.

        Usage examples:
        `!my_tracked_stocks`
        `/my_tracked_stocks`
        """
        await ctx.defer(ephemeral=True)
        
        user_id = ctx.author.id
        tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id) # Returns list of dicts

        if not tracked_stocks_list:
            await ctx.send("You are not tracking any stocks. Use `/track_stock <symbol>` to add some!", ephemeral=True)
            return

        embed = discord.Embed(title=f"📊 Your Tracked Stocks ({len(tracked_stocks_list)})", color=discord.Color.purple())
        embed.set_footer(text="Data provided by Alpha Vantage. Prices may be delayed. Alerts shown are active.")
        
        description_lines = []
        api_call_count = 0
        max_calls_for_prices = 3

        if len(tracked_stocks_list) > max_calls_for_prices:
             await ctx.send(f"Displaying basic info for {len(tracked_stocks_list)} stocks. For current prices of more than {max_calls_for_prices} stocks, please use `/stock_price <symbol>` individually to manage API rate limits.", ephemeral=True)

        for i, stock_item in enumerate(tracked_stocks_list): # stock_item is a dict
            symbol_upper = stock_item['symbol'].upper()
            stock_display = f"**{symbol_upper}**:"
            
            # Portfolio info if available
            quantity = stock_item.get('quantity')
            purchase_price = stock_item.get('purchase_price')
            if quantity is not None and purchase_price is not None:
                stock_display += f" ({quantity} @ ${purchase_price:,.2f})"
            
            if i < max_calls_for_prices:
                if api_call_count > 0:
                    await asyncio.sleep(13)
                
                price_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_price, symbol_upper)
                api_call_count += 1

                if price_data:
                    if "error" in price_data:
                        error_type = price_data["error"]
                        error_message = price_data.get("message", "Unknown error")
                        if error_type == "api_limit": stock_display += f" ⚠️ Price: API limit."
                        else: stock_display += f" ❌ Price: N/A"
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
                    else: stock_display += " ❌ Price: N/A (Format)"
                else: stock_display += " ❌ Price: N/A (Fetch)"
            elif i == max_calls_for_prices:
                stock_display += " (Price check skipped)"

            alert_info = await self.bot.loop.run_in_executor(None, self.db_manager.get_stock_alert, user_id, symbol_upper)
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

        def parse_percentage(value_str: str, param_name: str) -> typing.Optional[typing.Union[float, str]]: # Added Union for "clear_marker"
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

        def parse_price(value_str: str, param_name: str) -> typing.Optional[typing.Union[float, str]]: # Added Union
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
    async def stock_chart(self, ctx: commands.Context, symbol: str, timespan: str = "1M"):
        """
        Generates and displays a stock price chart for a given symbol and timespan.
        Usage examples:
        `!stock_chart AAPL 1M`
        `/stock_chart symbol:TSLA timespan:6M`
        `/stock_chart symbol:MSFT timespan:1D`
        """
        # Normalize symbol for Yahoo Finance compatibility first
        normalized_symbol = yahoo_finance_client.normalize_symbol(symbol.upper())
        symbol_for_display = symbol.upper() # For messages and chart title
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

        logger.info(f"Fetching chart data for {symbol_for_display} (normalized: {normalized_symbol}), timespan {timespan_upper} using Alpha Vantage first.")
        data_source = "Alpha Vantage" # Default data source

        # Call the appropriate Alpha Vantage function
        if config["is_intraday"]:
            time_series_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_intraday_time_series, symbol_for_display, api_params['interval'], api_params['outputsize'])
        else: # Daily
            time_series_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_daily_time_series, symbol_for_display, api_params['outputsize'])

        # Check if Alpha Vantage failed (no data, error dict, or empty list), then try Yahoo Finance
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
            
            # Map timespan to Yahoo Finance period
            yahoo_period = "max" # Default fallback
            if timespan_upper == "1D": yahoo_period = "1d" # Intraday
            elif timespan_upper == "5D": yahoo_period = "5d" # Intraday
            elif timespan_upper == "1M": yahoo_period = "1mo"
            elif timespan_upper == "3M": yahoo_period = "3mo"
            elif timespan_upper == "6M": yahoo_period = "6mo"
            elif timespan_upper == "YTD": yahoo_period = "ytd"
            elif timespan_upper == "1Y": yahoo_period = "1y"
            elif timespan_upper == "MAX": yahoo_period = "max"

            if config["is_intraday"]:
                # Map Alpha Vantage interval to approximate Yahoo interval
                av_interval = api_params['interval']
                if av_interval == "1min": yahoo_interval = "1m"
                elif av_interval == "5min": yahoo_interval = "5m"
                elif av_interval == "15min": yahoo_interval = "15m"
                elif av_interval == "30min": yahoo_interval = "30m"
                elif av_interval == "60min": yahoo_interval = "60m" # or 1h
                else: yahoo_interval = "60m" # Default
                # Yahoo intraday needs '1d' or '5d' period usually, but for 1D/5D timespan we set it above.
                # If user requested intraday via timespan param logic:
                time_series_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_intraday_time_series, normalized_symbol, yahoo_interval)
            else: # Daily
                time_series_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_daily_time_series, normalized_symbol, "compact", yahoo_period)
        
        # Post-fetch processing (common for both AV and YF data)
        if not time_series_data:
            await ctx.send(f"Could not retrieve time series data for {symbol_for_display} ({display_label}) from any provider. The symbol might be invalid or there's no data.", ephemeral=True)
            return
        if isinstance(time_series_data, dict) and "error" in time_series_data: # Should only be AV at this point if YF also failed with dict error (unlikely for YF client)
            error_message = time_series_data.get("message", "An unspecified API error occurred.")
            await ctx.send(f"Error fetching chart data for {symbol_for_display} ({display_label}): {error_message}", ephemeral=True)
            return
        
        if not isinstance(time_series_data, list) or not time_series_data:
            await ctx.send(f"No valid time series data points found for {symbol_for_display} ({display_label}) from any provider to generate a chart.", ephemeral=True)
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
                    await ctx.send(f"No data found for {symbol_for_display} since the start of this year for the YTD chart.", ephemeral=True)
                    return
            except Exception as e:
                logger.error(f"Error filtering YTD data for {symbol_for_display}: {e}")
                await ctx.send(f"An error occurred while processing YTD data for {symbol_for_display}.", ephemeral=True)
                return


        logger.info(f"Generating chart image for {symbol_for_display} ({display_label}) with {len(time_series_data)} data points.")
        image_bytes = await self.bot.loop.run_in_executor(None, get_stock_chart_image, symbol_for_display, display_label, time_series_data)

        if image_bytes:
            file = discord.File(image_bytes, filename="chart.png")
            embed = discord.Embed(
                title=f"📈 Stock Chart for {symbol_for_display} ({display_label})",
                color=discord.Color.blue()
            )
            embed.set_image(url="attachment://chart.png")
            embed.set_footer(text=f"Chart generated using QuickChart.io | Data from {data_source}")
            await ctx.send(file=file, embed=embed)
        else:
            await ctx.send(f"Sorry, I couldn't generate the chart for {symbol_for_display} ({display_label}) at this time.", ephemeral=True)

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
        news_data = await self._fetch_stock_news_any_provider(upper_symbol, limit=5)

        if news_data is None:
            await ctx.send(f"📰 No news found for {upper_symbol}, or an error occurred while fetching.", ephemeral=True)
            return

        if not isinstance(news_data, list) or not news_data:
            await ctx.send(f"📰 No news articles found for {upper_symbol} at this time.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📰 Recent News for {upper_symbol}",
            color=discord.Color.blue()
        )
        embed.set_footer(text="News provided by Google News RSS (best-effort).")

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

    @commands.hybrid_command(
        name="stock_analyze",
        description="Use Gemini to analyze and summarize recent stock news (or your whole tracked list).",
    )
    @discord.app_commands.describe(
        symbol="Optional stock symbol. If omitted, analyzes your tracked stocks.",
        limit="Max articles per stock (default 8, max 25).",
        public="Post analysis publicly in the channel (default: False).",
        detail="short|medium|long (controls depth + length; default: medium).",
        max_symbols="For portfolio mode: how many tracked symbols to include (default 25, max 50).",
        include_sources="Attach a sources file (URLs + extracted text). Default: True.",
    )
    async def stock_analyze(
        self,
        ctx: commands.Context,
        symbol: typing.Optional[str] = None,
        limit: int = 5,
        public: bool = False,
        detail: str = "medium",
        max_symbols: int = 25,
        include_sources: bool = True,
    ):
        is_dm = ctx.guild is None
        ephemeral = (not public) if not is_dm else False
        await ctx.defer(ephemeral=ephemeral)
        # defaults/caps (user has plenty of model context; the real constraint is API/scraping/Discord)
        if limit == 5:
            limit = 8
        limit = max(1, min(25, int(limit)))
        max_symbols = max(1, min(50, int(max_symbols)))

        if self._gemini_client is None:
            await ctx.send("❌ Gemini is not configured by the bot owner (missing `GEMINI_API_KEY`).", ephemeral=ephemeral)
            return

        detail_l = str(detail or "medium").strip().lower()
        if detail_l not in {"short", "medium", "long"}:
            detail_l = "medium"

        # Tune caps: longer detail => more Gemini output + more article text per item.
        # More generous defaults since user isn't token-constrained.
        detail_to_tokens = {"short": 1400, "medium": 3200, "long": 6000}
        detail_to_article_chars = {"short": 5000, "medium": 12000, "long": 25000}
        max_tokens = int(detail_to_tokens[detail_l])
        per_article_chars = int(detail_to_article_chars[detail_l])

        user_id = ctx.author.id
        symbols: typing.List[str]
        if symbol and str(symbol).strip():
            symbols = [str(symbol).strip().upper()]
        else:
            tracked = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
            symbols = [str(s.get("symbol") or "").upper() for s in (tracked or []) if isinstance(s, dict) and s.get("symbol")]
            symbols = [s for s in symbols if s]
            if not symbols:
                await ctx.send(
                    "You are not tracking any stocks. Use `/track_stock <symbol>` first, or pass a `symbol` to `/stock_analyze`.",
                    ephemeral=ephemeral,
                )
                return

        trimmed = False
        if not symbol:
            # Portfolio mode: allow more symbols, but still cap to avoid huge latency/scrape failures.
            if len(symbols) > max_symbols:
                symbols = symbols[:max_symbols]
                trimmed = True

        briefs: list[str] = []
        for sym in symbols:
            news = await self._fetch_stock_news_any_provider(sym, limit=limit)
            if not news:
                briefs.append(f"### {sym}\n- No news found.")
                continue
            briefs.append(await self._news_to_fulltext_context(sym, news, max_articles=limit, per_article_chars=per_article_chars))

        context = "\n\n".join(briefs)
        scope_line = "single stock" if symbol else "portfolio"
        prompt = (
            "You are a financial news assistant. Summarize and analyze the following news headlines for the user.\n"
            "Rules:\n"
            "- Do NOT provide investment advice. Do not tell the user to buy/sell.\n"
            "- Focus on: key events, catalysts, risks, and what to watch next.\n"
            "- If sentiment labels are present, mention whether sentiment is broadly positive/negative/mixed.\n"
            "- Keep it concise and structured.\n\n"
            f"Scope: {scope_line}\n"
            f"Symbols: {', '.join(symbols)}\n\n"
            "News (includes full article text when accessible; otherwise fall back to provider summary):\n"
            f"{context}\n\n"
            "Output format:\n"
            "1) Executive summary (3-6 bullets)\n"
            "2) By symbol (2-4 bullets each)\n"
            "3) Watchlist / questions to investigate next\n"
        )

        try:
            text = await self._gemini_summarize(prompt, max_output_tokens=max_tokens)
        except Exception as e:
            await ctx.send(f"⚠️ Could not run Gemini analysis right now: {e}", ephemeral=ephemeral)
            return

        if not text:
            await ctx.send("⚠️ Gemini returned an empty response.", ephemeral=ephemeral)
            return

        title_model = "Gemini 3 Flash (preview)"
        title = "🧠 Stock analysis"
        embed = discord.Embed(
            title=title,
            description=(text[:3900] + ("…" if len(text) > 3900 else "")),
            color=discord.Color.dark_gold(),
        )
        embed.add_field(name="Model", value=title_model, inline=True)
        embed.add_field(name="Scope", value=("single" if symbol else "portfolio"), inline=True)
        embed.add_field(name="Detail", value=detail_l, inline=True)
        embed.add_field(name="Symbols", value=(", ".join(symbols)[:1024] or "n/a"), inline=False)
        footer = "Not financial advice."
        if trimmed:
            footer += f" Only the first {len(symbols)} symbols were analyzed."
        embed.set_footer(text=footer)

        files: list[discord.File] = []

        # Always attach full report to avoid Discord embed limits.
        buf = io.BytesIO(text.encode("utf-8", errors="ignore"))
        files.append(discord.File(fp=buf, filename="stock_analysis.txt"))

        # Optionally attach sources (what Gemini read). Clamp to ~3MB to avoid Discord attachment limits.
        if include_sources:
            src_txt = (
                "SOURCES (best-effort extracted article text; may be truncated)\n"
                f"Symbols: {', '.join(symbols)}\n\n"
                + context
            )
            src_bytes = src_txt.encode("utf-8", errors="ignore")
            max_src_bytes = 3_000_000
            if len(src_bytes) > max_src_bytes:
                src_bytes = src_bytes[: max_src_bytes - 10] + b"\n...(truncated)"
            files.append(discord.File(fp=io.BytesIO(src_bytes), filename="stock_sources.txt"))

        await ctx.send(embed=embed, files=files, ephemeral=ephemeral)

    @commands.hybrid_command(name="set_portfolio_currency", description="Set your preferred currency for portfolio display.")
    @discord.app_commands.describe(currency="The currency code (e.g., USD, EUR, PLN)")
    async def set_portfolio_currency(self, ctx: commands.Context, currency: str):
        """
        Sets your preferred currency for portfolio valuation.
        All stocks will be converted to this currency in !my_portfolio.
        
        Usage:
        `!set_portfolio_currency USD`
        `!set_portfolio_currency EUR`
        """
        currency = currency.upper()
        valid_currencies = ["USD", "EUR", "PLN", "GBP", "CAD", "JPY", "AUD"]
        if currency not in valid_currencies:
            await ctx.send(f"⚠️ Unsupported currency. Please choose from: {', '.join(valid_currencies)}", ephemeral=True)
            return

        user_id = ctx.author.id
        success = await self.bot.loop.run_in_executor(None, self.db_manager.set_user_preference, user_id, "portfolio_currency", currency)
        
        if success:
            await ctx.send(f"✅ Portfolio currency set to **{currency}**.", ephemeral=True)
        else:
            await ctx.send("❌ Failed to save preference.", ephemeral=True)

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

        # Get preferred currency
        pref_currency = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "portfolio_currency", "USD")
        
        currency_symbols = {
            'USD': '$', 'PLN': 'zł', 'EUR': '€', 'GBP': '£', 'CAD': 'C$', 'JPY': '¥', 'AUD': 'A$'
        }
        pref_currency_symbol = currency_symbols.get(pref_currency, pref_currency)

        embed = discord.Embed(title=f"💰 Your Stock Portfolio ({pref_currency})", color=discord.Color.gold())
        embed.set_footer(text="Data provided by Alpha Vantage/Yahoo Finance. Prices may be delayed.")

        overall_cost_basis = 0.0
        overall_market_value = 0.0
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
            # stored_currency = stock_data.get("currency", "USD") # Assuming stored price is in this currency

            if api_call_count > 0:
                await asyncio.sleep(2) # Short delay to be nice to APIs

            # Fetch current price
            current_price_data = await self.bot.loop.run_in_executor(None, alpha_vantage_client.get_stock_price, symbol)
            data_source = "Alpha Vantage"
            api_call_count += 1

            # Fallback to Yahoo
            if not current_price_data or "error" in current_price_data:
                current_price_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_price, symbol)
                data_source = "Yahoo Finance"

            current_price = None
            stock_currency = "USD"
            api_error_for_stock = False

            if current_price_data and "05. price" in current_price_data:
                try:
                    current_price = float(current_price_data["05. price"])
                    stock_currency = current_price_data.get('currency', 'USD')
                except (ValueError, TypeError):
                    api_error_for_stock = True
            else:
                api_error_for_stock = True

            # Conversion Logic
            exchange_rate = 1.0
            if not api_error_for_stock and stock_currency != pref_currency:
                # Need conversion
                pair = f"{stock_currency}{pref_currency}=X" # e.g. PLNUSD=X
                # Special case for USD
                if stock_currency == 'USD': pair = f"{pref_currency}=X" # This is wrong usually. 
                # Yahoo convention: BaseQuote=X. EURUSD=X means 1 EUR = x USD.
                
                # Check Yahoo conventions or use a more robust way?
                # Standard: EURUSD=X, GBPUSD=X, PLNUSD=X (Wait, PLNUSD might not exist, usually USDPLN=X)
                # Let's rely on get_stock_price(pair)
                
                pair_symbol = f"{stock_currency}{pref_currency}=X"
                fx_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_price, pair_symbol)
                
                if not fx_data:
                     # Try inverse
                     pair_symbol_inv = f"{pref_currency}{stock_currency}=X"
                     fx_data_inv = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_price, pair_symbol_inv)
                     if fx_data_inv and "05. price" in fx_data_inv:
                         exchange_rate = 1.0 / float(fx_data_inv["05. price"])
                elif "05. price" in fx_data:
                    exchange_rate = float(fx_data["05. price"])
                
                # Apply conversion
                current_price = current_price * exchange_rate
                # Note: purchase_price is historical. We should ideally convert it at historical rate.
                # But we don't have purchase date. So we have to assume purchase_price was entered in the SAME currency as the stock trades in?
                # OR purchase_price is in 'currency' column of tracked_stocks.
                # Let's assume purchase_price is in the stock's native currency for now, and convert it using CURRENT rate (approx) 
                # OR we should convert cost basis using current rate to see "value if sold now vs cost if bought now"? 
                # No, Cost Basis should be fixed in portfolio currency. 
                # Limitation: We don't know historical FX. 
                # COMPROMISE: Convert purchase_price using CURRENT FX rate. 
                # This preserves the % gain/loss of the asset itself, but ignores FX gain/loss.
                purchase_price = purchase_price * exchange_rate 

            cost_basis = quantity * purchase_price
            market_value = 0.0
            gain_loss = 0.0
            gain_loss_pct_str = "N/A"

            if current_price is not None and not api_error_for_stock:
                market_value = quantity * current_price
                overall_cost_basis += cost_basis
                overall_market_value += market_value
                
                gain_loss = market_value - cost_basis
                if cost_basis != 0:
                    gain_loss_pct = (gain_loss / cost_basis) * 100
                    gain_loss_pct_str = f"{gain_loss_pct:+.2f}%"
            elif api_error_for_stock:
                market_value = "N/A (API Error)"

            individual_holdings_details.append({
                "symbol": symbol,
                "quantity": quantity,
                "current_price": current_price if not api_error_for_stock else "N/A",
                "market_value": market_value,
                "gain_loss": gain_loss if not api_error_for_stock else "N/A",
                "gain_loss_pct_str": gain_loss_pct_str,
                "source": data_source
            })

        overall_gain_loss = overall_market_value - overall_cost_basis
        overall_gain_loss_pct_str = "N/A"
        if overall_cost_basis != 0:
            overall_gain_loss_pct = (overall_gain_loss / overall_cost_basis) * 100
            overall_gain_loss_pct_str = f"{overall_gain_loss_pct:+.2f}%"

        summary_color = discord.Color.green() if overall_gain_loss >= 0 else discord.Color.red()
        embed.color = summary_color

        embed.add_field(
            name="📈 Overall Portfolio Summary",
            value=(
                f"**Total Market Value:** {pref_currency_symbol}{overall_market_value:,.2f}\n"
                f"**Total Cost Basis:** {pref_currency_symbol}{overall_cost_basis:,.2f}\n"
                f"**Total Gain/Loss:** {pref_currency_symbol}{overall_gain_loss:,.2f} ({overall_gain_loss_pct_str})"
            ),
            inline=False
        )

        holdings_text_parts = []
        for item in individual_holdings_details:
            symbol_header = f"--- **{item['symbol']}** ---"
            
            if isinstance(item['current_price'], (int, float)):
                price_disp = f"{pref_currency_symbol}{item['current_price']:,.2f}"
                val_disp = f"{pref_currency_symbol}{item['market_value']:,.2f}"
                gl_disp = f"{pref_currency_symbol}{item['gain_loss']:+,.2f}"
                gl_emoji = "🔼 " if item['gain_loss'] > 0 else "🔽 "
            else:
                price_disp = "N/A"
                val_disp = "N/A"
                gl_disp = "N/A"
                gl_emoji = ""

            details = (
                f"{symbol_header}\n"
                f"Qty: `{item['quantity']}`\n"
                f"Price: `{price_disp}` | Value: `{val_disp}`\n"
                f"G/L: {gl_emoji}`{gl_disp} ({item['gain_loss_pct_str']})`"
            )
            holdings_text_parts.append(details)

        # Chunking fields
        current_field_value = ""
        field_count = 0
        for part in holdings_text_parts:
            if len(current_field_value) + len(part) + 2 > 1024:
                embed.add_field(name="Holdings" if field_count == 0 else "Holdings (Cont.)", value=current_field_value, inline=False)
                current_field_value = part
                field_count += 1
            else:
                current_field_value += ("\n\n" if current_field_value else "") + part
        
        if current_field_value:
            embed.add_field(name="Holdings" if field_count == 0 else "Holdings (Cont.)", value=current_field_value, inline=False)

        if status_msg:
            try: await status_msg.delete()
            except: pass
            
        await ctx.send(embed=embed, ephemeral=False)

    @commands.hybrid_command(name="stock_debug", description="Debug stock API connections for a symbol.")
    @discord.app_commands.describe(symbol="The stock symbol to debug (e.g., LPP, AAPL)")
    async def stock_debug(self, ctx: commands.Context, *, symbol: str):
        """
        Debug command to test both Alpha Vantage and Yahoo Finance APIs for a symbol.
        This helps diagnose issues with stock price lookups.
        """
        if not await self._is_admin_or_owner(ctx):
            await ctx.send("This command is restricted to bot administrators.", ephemeral=True)
            return
            
        upper_symbol = symbol.upper()
        
        embed = discord.Embed(title=f"🔧 Stock API Debug for {upper_symbol}", color=discord.Color.blue())
        
        # Test Alpha Vantage
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
        
        # Test Yahoo Finance
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
        
        # Overall recommendation
        if (av_result and "01. symbol" in av_result) or (yf_result and "01. symbol" in yf_result):
            recommendation = "✅ At least one API is working - stock_price command should succeed"
        else:
            recommendation = "❌ Both APIs failed - stock_price command will fail"
        
        embed.add_field(name="📋 Recommendation", value=recommendation, inline=False)
        embed.set_footer(text="This is a diagnostic command for troubleshooting")
        
        await ctx.send(embed=embed, ephemeral=True)
    
    async def _is_admin_or_owner(self, ctx) -> bool:
        """Check if user is bot owner or has admin permissions"""
        # Check if user is bot owner
        app_info = await self.bot.application_info()
        if ctx.author.id == app_info.owner.id:
            return True
        
        # Check if user has admin permissions in the guild
        if hasattr(ctx.author, 'guild_permissions') and ctx.author.guild_permissions.administrator:
            return True
            
        return False

    @commands.command(name="stock_alert_set", aliases=["alert_set"])
    async def stock_alert_set(self, ctx: commands.Context, symbol: str, direction: str, target: float):
        """
        Simple command to set a stock price alert.
        
        Usage:
        !stock_alert_set ASML below 700
        !stock_alert_set AAPL above 150
        !alert_set TSLA below 200
        """
        user_id = ctx.author.id
        symbol_upper = symbol.upper()
        direction_lower = direction.lower()
        
        # Check if stock is tracked
        tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
        if not any(s['symbol'] == symbol_upper for s in tracked_stocks_list):
            await ctx.send(f"❌ You are not tracking {symbol_upper}. Please use `!track_stock {symbol_upper}` first.")
            return
            
        # Validate direction
        if direction_lower not in ['above', 'below']:
            await ctx.send(f"❌ Direction must be 'above' or 'below', not '{direction}'.")
            return
            
        # Validate target price
        if target <= 0:
            await ctx.send(f"❌ Target price must be positive, not {target}.")
            return
            
        # Set the alert
        try:
            if direction_lower == 'above':
                func = functools.partial(self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=target, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=False, clear_below=False,
                    clear_dpc_above=False, clear_dpc_below=False
                )
                success = await self.bot.loop.run_in_executor(None, func)
                if success:
                    await ctx.send(f"✅ Alert set for {symbol_upper}: notify when price goes **above ${target:.2f}**")
                else:
                    await ctx.send(f"❌ Failed to set alert for {symbol_upper}. It might already be set to this value.")
            else:  # below
                func = functools.partial(self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=None, target_below=target,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=False, clear_below=False,
                    clear_dpc_above=False, clear_dpc_below=False
                )
                success = await self.bot.loop.run_in_executor(None, func)
                if success:
                    await ctx.send(f"✅ Alert set for {symbol_upper}: notify when price goes **below ${target:.2f}**")
                else:
                    await ctx.send(f"❌ Failed to set alert for {symbol_upper}. It might already be set to this value.")
                    
        except Exception as e:
            await ctx.send(f"❌ Error setting alert: {str(e)}")

    @commands.hybrid_command(name="portfolio_chart", description="Generate a chart of your portfolio's historical performance.")
    @discord.app_commands.describe(timespan=f"The timespan for the chart. Default '1M'. Options: {', '.join(SUPPORTED_TIMESPAN.keys())}")
    async def portfolio_chart(self, ctx: commands.Context, timespan: str = "1M"):
        """
        Generates a chart showing the historical value of your current portfolio over the specified timespan.
        Note: This assumes your current holdings were held throughout the period (reconstructs history).
        """
        await ctx.defer(ephemeral=False)
        
        user_id = ctx.author.id
        timespan_upper = timespan.upper()
        if timespan_upper not in SUPPORTED_TIMESPAN:
            await ctx.send(f"Invalid timespan. Options: {', '.join(SUPPORTED_TIMESPAN.keys())}", ephemeral=True)
            return

        # 1. Get Portfolio
        tracked_stocks_all = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
        portfolio_stocks = [s for s in tracked_stocks_all if s.get("quantity") is not None]
        
        if not portfolio_stocks:
            await ctx.send("No stocks with quantity found in your portfolio.", ephemeral=True)
            return

        # 2. Get Preference
        pref_currency = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_preference, user_id, "portfolio_currency", "USD")

        # 3. Fetch History for each stock
        config = SUPPORTED_TIMESPAN[timespan_upper]
        # Always use Daily for portfolio history to reduce noise and align easier, unless 1D/5D?
        # 1D portfolio chart is hard because intraday alignment is messy.
        # Let's support 1M+ for now effectively, or map others to Daily.
        # Actually, if user asks for 1D/5D, we should try intraday if possible, but let's stick to daily for robustness first for portfolio.
        # Users usually want to see "Growth since X".
        # If timespan is intraday (1D, 5D), let's fallback to 'daily' logic or warn.
        # Let's force Daily for portfolio chart for simplicity and reliability.
        
        api_params = config["params"].copy()
        yahoo_period = "1mo" # Default
        if timespan_upper == "1D": yahoo_period = "1d"; interval = "15m" # approximations
        elif timespan_upper == "5D": yahoo_period = "5d"; interval = "60m"
        elif timespan_upper == "1M": yahoo_period = "1mo"
        elif timespan_upper == "3M": yahoo_period = "3mo"
        elif timespan_upper == "6M": yahoo_period = "6mo"
        elif timespan_upper == "YTD": yahoo_period = "ytd"
        elif timespan_upper == "1Y": yahoo_period = "1y"
        elif timespan_upper == "MAX": yahoo_period = "max"

        # We will use Yahoo Finance for batch/history because it's easier to get aligned daily data without strict rate limits of AV free tier.
        # Also AV doesn't support "last 1 year" easily without full output size which is huge.
        
        stock_histories = {} # symbol -> {date_str: price}
        conversion_rates = {} # symbol -> rate (approx current) OR {date_str: rate}

        for stock in portfolio_stocks:
            symbol = stock['symbol'].upper()
            # Fetch history
            # Use Yahoo Finance explicitly for portfolio history construction
            hist_data = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_daily_time_series, symbol, "compact", yahoo_period)
            
            if not hist_data:
                # Try AV as fallback? AV daily full is heavy.
                # Stick to Yahoo for history chart.
                logger.warning(f"Could not fetch history for {symbol}")
                continue

            # Normalize to dict
            stock_histories[symbol] = {item[0]: item[1] for item in hist_data}
            
            # Determine currency conversion
            # We need to know the stock's currency to convert.
            # get_stock_price fetches metadata including currency.
            # We can optimize by checking one current price call or assuming we know it?
            # Let's just fetch current info once to get currency.
            info = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_stock_price, symbol)
            stock_curr = info.get('currency', 'USD') if info else 'USD'

            if stock_curr != pref_currency:
                # Fetch FX history
                pair = f"{stock_curr}{pref_currency}=X"
                fx_hist = await self.bot.loop.run_in_executor(None, yahoo_finance_client.get_daily_time_series, pair, "compact", yahoo_period)
                if fx_hist:
                    conversion_rates[symbol] = {item[0]: item[1] for item in fx_hist}
                else:
                    # Fallback: constant rate
                    logger.warning(f"Could not fetch FX history for {pair}. Using 1.0")
                    conversion_rates[symbol] = 1.0
            else:
                conversion_rates[symbol] = 1.0

        if not stock_histories:
             await ctx.send("Could not retrieve historical data for any of your stocks.", ephemeral=True)
             return

        # 4. Align and Sum
        all_dates = set()
        for s in stock_histories:
            all_dates.update(stock_histories[s].keys())
        
        sorted_dates = sorted(list(all_dates))
        portfolio_series = []

        # Helper to find closest previous date's value (fill forward)
        # Keys must be symbols (strings), not stock objects (dicts)
        last_known_prices = {s['symbol'].upper(): 0.0 for s in portfolio_stocks}
        last_known_fx = {s['symbol'].upper(): 1.0 for s in portfolio_stocks}

        for date_str in sorted_dates:
            daily_total = 0.0
            
            for stock in portfolio_stocks:
                sym = stock['symbol'].upper()
                qty = stock['quantity']
                
                if sym not in stock_histories: continue

                # Update Price
                if date_str in stock_histories[sym]:
                    last_known_prices[sym] = stock_histories[sym][date_str]
                
                # Update FX
                if isinstance(conversion_rates.get(sym), dict):
                    if date_str in conversion_rates[sym]:
                        last_known_fx[sym] = conversion_rates[sym][date_str]
                    # else keep last known
                elif isinstance(conversion_rates.get(sym), (int, float)):
                     last_known_fx[sym] = conversion_rates[sym]
                
                # Calc value
                val = last_known_prices[sym] * last_known_fx[sym] * qty
                daily_total += val
            
            if daily_total > 0:
                portfolio_series.append((date_str, daily_total))

        # 5. Generate Chart
        if not portfolio_series:
             await ctx.send("Insufficient data to generate portfolio chart.", ephemeral=True)
             return
             
        display_label = config["label"]
        image_bytes = await self.bot.loop.run_in_executor(None, get_stock_chart_image, "Portfolio Value", f"{display_label} ({pref_currency})", portfolio_series)

        if image_bytes:
            file = discord.File(image_bytes, filename="portfolio_chart.png")
            embed = discord.Embed(title=f"📈 Portfolio Performance ({display_label})", color=discord.Color.gold())
            embed.set_image(url="attachment://portfolio_chart.png")
            embed.set_footer(text=f"Valuation in {pref_currency}. Data: Yahoo Finance.")
            await ctx.send(file=file, embed=embed)
        else:
            await ctx.send("Failed to generate chart image.", ephemeral=True)

    @commands.command(name="stock_alert_clear", aliases=["alert_clear"])
    async def stock_alert_clear(self, ctx: commands.Context, symbol: str, direction: str = "all"):
        """
        Clear stock alerts.
        
        Usage:
        !stock_alert_clear ASML below
        !stock_alert_clear AAPL above  
        !stock_alert_clear TSLA all
        """
        user_id = ctx.author.id
        symbol_upper = symbol.upper()
        direction_lower = direction.lower()
        
        # Check if stock is tracked
        tracked_stocks_list = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_tracked_stocks, user_id)
        if not any(s['symbol'] == symbol_upper for s in tracked_stocks_list):
            await ctx.send(f"❌ You are not tracking {symbol_upper}.")
            return
            
        # Validate direction
        if direction_lower not in ['above', 'below', 'all']:
            await ctx.send(f"❌ Direction must be 'above', 'below', or 'all', not '{direction}'.")
            return
            
        try:
            if direction_lower == 'all':
                func = functools.partial(self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=None, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=True, clear_below=True,
                    clear_dpc_above=True, clear_dpc_below=True
                )
                success = await self.bot.loop.run_in_executor(None, func)
                if success:
                    await ctx.send(f"✅ All alerts cleared for {symbol_upper}")
                else:
                    await ctx.send(f"❌ Failed to clear alerts for {symbol_upper}")
            elif direction_lower == 'above':
                func = functools.partial(self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=None, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=True, clear_below=False,
                    clear_dpc_above=False, clear_dpc_below=False
                )
                success = await self.bot.loop.run_in_executor(None, func)
                if success:
                    await ctx.send(f"✅ 'Above' alert cleared for {symbol_upper}")
                else:
                    await ctx.send(f"❌ Failed to clear 'above' alert for {symbol_upper}")
            else:  # below
                func = functools.partial(self.db_manager.add_stock_alert,
                    user_id, symbol_upper,
                    target_above=None, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=False, clear_below=True,
                    clear_dpc_above=False, clear_dpc_below=False
                )
                success = await self.bot.loop.run_in_executor(None, func)
                if success:
                    await ctx.send(f"✅ 'Below' alert cleared for {symbol_upper}")
                else:
                    await ctx.send(f"❌ Failed to clear 'below' alert for {symbol_upper}")
                    
        except Exception as e:
            await ctx.send(f"❌ Error clearing alert: {str(e)}")

    # NOTE: Do not alias to "sync" because `bot.py` already registers a global prefix command named "sync".
    @commands.command(name="sync_commands")
    async def sync_commands(self, ctx: commands.Context):
        """
        Manually syncs the bot's slash commands with Discord.
        Useful if new commands are not showing up immediately.
        """
        if not await self._is_admin_or_owner(ctx):
            # `ephemeral` is not supported for plain prefix commands (ctx.send).
            await ctx.send("This command is restricted to bot administrators.")
            return
            
        await ctx.typing()
        try:
            synced = await self.bot.tree.sync()
            await ctx.send(f"✅ Synced {len(synced)} commands.")
            logger.info(f"Synced {len(synced)} commands via manual trigger.")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
            await ctx.send(f"❌ Failed to sync commands: {e}")

    # -------------------------
    # Schedule management commands
    # -------------------------
    @commands.hybrid_group(
        name="stock_analysis_schedule",
        invoke_without_command=True,
        description="Manage scheduled portfolio analysis DMs (UTC).",
    )
    async def stock_analysis_schedule(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self.stock_analysis_schedule_list(ctx)

    @stock_analysis_schedule.command(name="add", description="Add a UTC time (HH:MM) to receive a daily portfolio analysis DM.")
    @discord.app_commands.describe(time="UTC time HH:MM (e.g., 08:00)")
    async def stock_analysis_schedule_add(self, ctx: commands.Context, time: str):
        await ctx.defer(ephemeral=True)
        if not self._time_hhmm_pattern().match(str(time or "").strip()):
            await ctx.send("❌ Invalid time format. Use HH:MM (e.g., 08:00).", ephemeral=True)
            return
        await self.bot.loop.run_in_executor(None, self.db_manager.add_portfolio_analysis_schedule, ctx.author.id, str(time).strip())
        await ctx.send(f"✅ Scheduled portfolio analysis at **{str(time).strip()}** UTC.", ephemeral=True)

    @stock_analysis_schedule.command(name="remove", description="Remove a UTC time (HH:MM) or 'all'.")
    @discord.app_commands.describe(time="UTC time HH:MM or 'all'")
    async def stock_analysis_schedule_remove(self, ctx: commands.Context, time: str):
        await ctx.defer(ephemeral=True)
        t = str(time or "").strip().lower()
        if t in {"all", "*", "clear"}:
            await self.bot.loop.run_in_executor(None, self.db_manager.clear_portfolio_analysis_schedules, ctx.author.id)
            await ctx.send("✅ Removed **all** portfolio analysis schedules.", ephemeral=True)
            return
        if not self._time_hhmm_pattern().match(str(time or "").strip()):
            await ctx.send("❌ Invalid time format. Use HH:MM (e.g., 08:00) or `all`.", ephemeral=True)
            return
        await self.bot.loop.run_in_executor(None, self.db_manager.remove_portfolio_analysis_schedule, ctx.author.id, str(time).strip())
        await ctx.send(f"✅ Removed schedule for **{str(time).strip()}** UTC.", ephemeral=True)

    @stock_analysis_schedule.command(name="list", description="List your scheduled portfolio analysis times (UTC).")
    async def stock_analysis_schedule_list(self, ctx: commands.Context):
        await ctx.defer(ephemeral=True)
        rows = await self.bot.loop.run_in_executor(None, self.db_manager.get_user_portfolio_analysis_schedules, ctx.author.id)
        times = [r.get("schedule_time") for r in (rows or []) if isinstance(r, dict) and r.get("schedule_time")]
        if not times:
            await ctx.send("You have no scheduled portfolio analyses. Add one with `/stock_analysis_schedule add 08:00`.", ephemeral=True)
            return
        await ctx.send("🕒 Scheduled portfolio analysis times (UTC): " + ", ".join(f"`{t}`" for t in times), ephemeral=True)

async def setup(bot):
    await bot.add_cog(Stocks(bot))
    print("Stocks Cog has been loaded and stock alert task initialized.")
