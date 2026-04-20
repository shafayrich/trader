"""
Streamlit Trading Bot - EMA Crossover Strategy
Uses Alpaca Paper Trading, Yahoo Finance data, and Telegram alerts.
No terminal required – everything is configured via the web UI.
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import time
import threading
from datetime import datetime, timedelta
import alpaca_trade_api as tradeapi

# ------------------------------
# Page configuration
# ------------------------------
st.set_page_config(page_title="EMA Crossover Trading Bot", layout="wide")
st.title("📈 EMA Crossover Trading Bot")
st.markdown("Configure your bot below and start trading in Alpaca **Paper** environment.")

# ------------------------------
# Session state initialisation
# ------------------------------
if "bot_running" not in st.session_state:
    st.session_state.bot_running = False
if "thread" not in st.session_state:
    st.session_state.thread = None
if "status_message" not in st.session_state:
    st.session_state.status_message = "Bot is idle."
if "latest_price" not in st.session_state:
    st.session_state.latest_price = None
if "ema_9" not in st.session_state:
    st.session_state.ema_9 = None
if "ema_50" not in st.session_state:
    st.session_state.ema_50 = None
if "chart_data" not in st.session_state:
    st.session_state.chart_data = pd.DataFrame(columns=["Close", "EMA_9", "EMA_50"])

# ------------------------------
# Sidebar – User Configuration
# ------------------------------
st.sidebar.header("🔐 API Configuration")

# Alpaca credentials (masked)
alpaca_api_key = st.sidebar.text_input(
    "Alpaca API Key",
    type="password",
    placeholder="PK...",
    help="Your Alpaca Paper Trading API Key"
)
alpaca_secret_key = st.sidebar.text_input(
    "Alpaca Secret Key",
    type="password",
    placeholder="...",
    help="Your Alpaca Paper Trading Secret Key"
)

# Telegram credentials (masked)
telegram_token = st.sidebar.text_input(
    "Telegram Bot Token",
    type="password",
    placeholder="123456:ABC...",
    help="Token from @BotFather"
)
telegram_chat_id = st.sidebar.text_input(
    "Telegram Chat ID",
    placeholder="123456789",
    help="Your numeric Chat ID (not username)"
)

# Trading instrument
ticker = st.sidebar.text_input(
    "Stock Ticker",
    value="AAPL",
    help="Enter a valid Yahoo Finance ticker (e.g., AAPL, 2222.SR)"
).upper().strip()

# Start / Stop buttons
col1, col2 = st.sidebar.columns(2)
start_btn = col1.button("▶️ Start Bot", disabled=st.session_state.bot_running)
stop_btn = col2.button("⏹️ Stop Bot", disabled=not st.session_state.bot_running)

# ------------------------------
# Telegram Alert Function
# ------------------------------
def send_telegram_alert(message: str):
    """Send a message to the configured Telegram chat."""
    if not telegram_token or not telegram_chat_id:
        return False, "Telegram credentials missing."
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {
        "chat_id": telegram_chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True, None
        else:
            return False, response.json().get("description", "Unknown error")
    except Exception as e:
        return False, str(e)

# ------------------------------
# Trading Logic (runs in background thread)
# ------------------------------
def trading_loop():
    """
    Main trading loop: fetches data every minute, computes EMAs,
    checks for crossovers, executes trades via Alpaca Paper API,
    and updates Streamlit session state for the UI.
    """
    # ----- Validate Alpaca credentials -----
    if not alpaca_api_key or not alpaca_secret_key:
        st.session_state.status_message = "❌ Alpaca API keys missing."
        st.session_state.bot_running = False
        return

    try:
        api = tradeapi.REST(
            alpaca_api_key,
            alpaca_secret_key,
            base_url="https://paper-api.alpaca.markets",
            api_version="v2"
        )
        # Test connection
        account = api.get_account()
        if account.status != "ACTIVE":
            st.session_state.status_message = "❌ Alpaca account not active."
            st.session_state.bot_running = False
            return
    except Exception as e:
        st.session_state.status_message = f"❌ Alpaca connection failed: {e}"
        st.session_state.bot_running = False
        return

    st.session_state.status_message = f"✅ Bot running for {ticker} – checking every minute."

    # Initialise variables for crossover detection
    position = None            # 'long' or None
    prev_ema9 = None
    prev_ema50 = None
    last_crossover = None

    # Create a placeholder for status updates inside the loop
    loop_status = st.empty()

    # Main loop – runs until bot_running becomes False
    while st.session_state.bot_running:
        try:
            # ----- 1. Fetch recent price data from Yahoo Finance -----
            end_date = datetime.now()
            start_date = end_date - timedelta(days=60)  # enough for EMA50
            data = yf.download(ticker, start=start_date, end=end_date, progress=False)

            if data.empty:
                loop_status.error(f"No data for {ticker}. Check ticker symbol.")
                st.session_state.status_message = f"❌ No data for {ticker}"
                time.sleep(60)
                continue

            # ----- 2. Compute EMAs -----
            data['EMA_9'] = data['Close'].ewm(span=9, adjust=False).mean()
            data['EMA_50'] = data['Close'].ewm(span=50, adjust=False).mean()

            latest = data.iloc[-1]
            current_price = latest['Close']
            ema9 = latest['EMA_9']
            ema50 = latest['EMA_50']

            # ----- 3. Update session state for UI display -----
            st.session_state.latest_price = round(current_price, 2)
            st.session_state.ema_9 = round(ema9, 2)
            st.session_state.ema_50 = round(ema50, 2)

            # Store last 200 points for chart
            chart_df = data[['Close', 'EMA_9', 'EMA_50']].tail(200)
            st.session_state.chart_data = chart_df

            # ----- 4. Crossover detection -----
            if prev_ema9 is not None and prev_ema50 is not None:
                # Bullish crossover: EMA9 crosses above EMA50
                if prev_ema9 <= prev_ema50 and ema9 > ema50:
                    last_crossover = "BULLISH"
                    if position != "long":
                        try:
                            api.submit_order(
                                symbol=ticker,
                                qty=1,
                                side="buy",
                                type="market",
                                time_in_force="day"
                            )
                            position = "long"
                            alert_msg = (
                                f"<b>🚀 BULLISH CROSSOVER</b>\n"
                                f"Ticker: {ticker}\n"
                                f"Price: ${current_price:.2f}\n"
                                f"EMA9 crossed above EMA50.\n"
                                f"<b>BUY order executed (Paper).</b>"
                            )
                            success, err = send_telegram_alert(alert_msg)
                            if not success:
                                loop_status.warning(f"Trade executed but Telegram failed: {err}")
                            else:
                                loop_status.success(alert_msg.replace("<b>", "").replace("</b>", ""))
                        except Exception as e:
                            loop_status.error(f"Buy order failed: {e}")
                    else:
                        loop_status.info(f"Bullish crossover detected but already long.")

                # Bearish crossover: EMA9 crosses below EMA50
                elif prev_ema9 >= prev_ema50 and ema9 < ema50:
                    last_crossover = "BEARISH"
                    if position == "long":
                        try:
                            api.submit_order(
                                symbol=ticker,
                                qty=1,
                                side="sell",
                                type="market",
                                time_in_force="day"
                            )
                            position = None
                            alert_msg = (
                                f"<b>🔻 BEARISH CROSSOVER</b>\n"
                                f"Ticker: {ticker}\n"
                                f"Price: ${current_price:.2f}\n"
                                f"EMA9 crossed below EMA50.\n"
                                f"<b>SELL order executed (Paper).</b>"
                            )
                            success, err = send_telegram_alert(alert_msg)
                            if not success:
                                loop_status.warning(f"Trade executed but Telegram failed: {err}")
                            else:
                                loop_status.success(alert_msg.replace("<b>", "").replace("</b>", ""))
                        except Exception as e:
                            loop_status.error(f"Sell order failed: {e}")
                    else:
                        loop_status.info(f"Bearish crossover detected but no position to sell.")
                else:
                    loop_status.info(f"Monitoring {ticker} | Price: ${current_price:.2f} | EMA9: {ema9:.2f} | EMA50: {ema50:.2f}")
            else:
                loop_status.info(f"Initialising... Price: ${current_price:.2f} | EMA9: {ema9:.2f} | EMA50: {ema50:.2f}")

            # Update previous values
            prev_ema9 = ema9
            prev_ema50 = ema50

            # Update main status message
            st.session_state.status_message = f"✅ Bot running for {ticker} | Last check: {datetime.now().strftime('%H:%M:%S')}"

            # ----- 5. Wait 1 minute before next iteration -----
            for _ in range(60):
                if not st.session_state.bot_running:
                    break
                time.sleep(1)

        except Exception as e:
            loop_status.error(f"Loop error: {e}")
            st.session_state.status_message = f"⚠️ Error: {e} – will retry in 1 min"
            time.sleep(60)

    # Clean up when loop ends
    st.session_state.status_message = "⏸️ Bot stopped."
    st.session_state.bot_running = False
    st.session_state.thread = None
    loop_status.empty()


# ------------------------------
# Button Handlers
# ------------------------------
if start_btn and not st.session_state.bot_running:
    # Validate required fields before starting
    if not alpaca_api_key or not alpaca_secret_key:
        st.error("Please provide both Alpaca API Key and Secret Key.")
    elif not telegram_token or not telegram_chat_id:
        st.error("Please provide both Telegram Bot Token and Chat ID.")
    elif not ticker:
        st.error("Please enter a stock ticker.")
    else:
        st.session_state.bot_running = True
        st.session_state.thread = threading.Thread(target=trading_loop, daemon=True)
        st.session_state.thread.start()
        st.success("Bot started! Check the status area below.")
        # Send a startup notification (non‑blocking)
        success, err = send_telegram_alert(f"🤖 Trading bot started for {ticker} (Paper trading).")
        if not success:
            st.warning(f"Telegram notification failed: {err}")

if stop_btn and st.session_state.bot_running:
    st.session_state.bot_running = False
    if st.session_state.thread is not None:
        st.session_state.thread.join(timeout=5)
    st.session_state.status_message = "⏹️ Bot stopped by user."
    success, _ = send_telegram_alert(f"🛑 Trading bot stopped manually for {ticker}.")
    st.info("Bot stopped.")

# ------------------------------
# Main Dashboard – Real-time Visuals
# ------------------------------
st.header("📊 Live Market Dashboard")

# Status message (updates from the bot thread)
status_placeholder = st.empty()
if st.session_state.bot_running:
    status_placeholder.success(st.session_state.status_message)
else:
    status_placeholder.info(st.session_state.status_message)

# Metrics row
col1, col2, col3 = st.columns(3)
col1.metric("Latest Price", f"${st.session_state.latest_price}" if st.session_state.latest_price else "N/A")
col2.metric("EMA 9", st.session_state.ema_9 if st.session_state.ema_9 else "N/A")
col3.metric("EMA 50", st.session_state.ema_50 if st.session_state.ema_50 else "N/A")

# Chart placeholder – auto-updating
st.subheader(f"{ticker} Price & EMAs")
chart_placeholder = st.empty()

# Continuously update the chart from session_state.chart_data
if not st.session_state.chart_data.empty:
    chart_placeholder.line_chart(st.session_state.chart_data)
else:
    chart_placeholder.info("Waiting for data...")

# Footer with instructions
st.markdown("---")
st.caption("⚙️ The bot checks for crossovers every minute. Use the sidebar to start/stop. All trades are executed in Alpaca **Paper** environment.")
