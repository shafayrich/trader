"""
Streamlit Trading Bot - EMA Crossover Strategy
Uses Alpaca Paper Trading, Yahoo Finance data, and Telegram alerts.
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import time
import threading
from datetime import datetime, timedelta
import alpaca_trade_api as tradeapi

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
# Sidebar
# ------------------------------
st.sidebar.header("🔐 API Configuration")

alpaca_api_key = st.sidebar.text_input("Alpaca API Key", type="password", placeholder="PK...")
alpaca_secret_key = st.sidebar.text_input("Alpaca Secret Key", type="password", placeholder="...")
telegram_token = st.sidebar.text_input("Telegram Bot Token", type="password", placeholder="123456:ABC...")
telegram_chat_id = st.sidebar.text_input("Telegram Chat ID", placeholder="123456789")
ticker = st.sidebar.text_input("Stock Ticker", value="AAPL").upper().strip()

col1, col2 = st.sidebar.columns(2)
start_btn = col1.button("▶️ Start Bot", disabled=st.session_state.bot_running)
stop_btn = col2.button("⏹️ Stop Bot", disabled=not st.session_state.bot_running)

# ------------------------------
# Telegram Alert
# ------------------------------
def send_telegram_alert(message: str):
    if not telegram_token or not telegram_chat_id:
        return False, "Telegram credentials missing."
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {"chat_id": telegram_chat_id, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True, None
        else:
            return False, response.json().get("description", "Unknown error")
    except Exception as e:
        return False, str(e)

# ------------------------------
# Trading Loop (Background Thread)
# ------------------------------
def trading_loop():
    # --- Validate Alpaca credentials FIRST ---
    if not alpaca_api_key or not alpaca_secret_key:
        st.session_state.status_message = "❌ Alpaca API keys missing. Please enter them in the sidebar."
        st.session_state.bot_running = False
        return

    try:
        api = tradeapi.REST(
            alpaca_api_key,
            alpaca_secret_key,
            base_url="https://paper-api.alpaca.markets",
            api_version="v2"
        )
        account = api.get_account()
        if account.status != "ACTIVE":
            st.session_state.status_message = "❌ Alpaca account not active. Check your paper trading account."
            st.session_state.bot_running = False
            return
    except Exception as e:
        st.session_state.status_message = f"❌ Alpaca connection failed: {e}"
        st.session_state.bot_running = False
        return

    st.session_state.status_message = f"✅ Bot running for {ticker} – checking every minute."

    position = None
    prev_ema9 = None
    prev_ema50 = None
    loop_status = st.empty()

    while st.session_state.bot_running:
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=60)
            data = yf.download(ticker, start=start_date, end=end_date, progress=False)

            if data.empty:
                loop_status.error(f"No data for {ticker}. Check ticker symbol.")
                time.sleep(60)
                continue

            data['EMA_9'] = data['Close'].ewm(span=9, adjust=False).mean()
            data['EMA_50'] = data['Close'].ewm(span=50, adjust=False).mean()

            latest = data.iloc[-1]
            current_price = latest['Close']
            ema9 = latest['EMA_9']
            ema50 = latest['EMA_50']

            st.session_state.latest_price = round(current_price, 2)
            st.session_state.ema_9 = round(ema9, 2)
            st.session_state.ema_50 = round(ema50, 2)
            st.session_state.chart_data = data[['Close', 'EMA_9', 'EMA_50']].tail(200)

            if prev_ema9 is not None and prev_ema50 is not None:
                if prev_ema9 <= prev_ema50 and ema9 > ema50:
                    if position != "long":
                        try:
                            api.submit_order(symbol=ticker, qty=1, side="buy", type="market", time_in_force="day")
                            position = "long"
                            alert_msg = (f"<b>🚀 BULLISH CROSSOVER</b>\nTicker: {ticker}\nPrice: ${current_price:.2f}\n"
                                         f"EMA9 crossed above EMA50.\n<b>BUY order executed (Paper).</b>")
                            success, err = send_telegram_alert(alert_msg)
                            if not success:
                                loop_status.warning(f"Trade executed but Telegram failed: {err}")
                            else:
                                loop_status.success(alert_msg.replace("<b>", "").replace("</b>", ""))
                        except Exception as e:
                            loop_status.error(f"Buy order failed: {e}")
                    else:
                        loop_status.info("Bullish crossover but already long.")
                elif prev_ema9 >= prev_ema50 and ema9 < ema50:
                    if position == "long":
                        try:
                            api.submit_order(symbol=ticker, qty=1, side="sell", type="market", time_in_force="day")
                            position = None
                            alert_msg = (f"<b>🔻 BEARISH CROSSOVER</b>\nTicker: {ticker}\nPrice: ${current_price:.2f}\n"
                                         f"EMA9 crossed below EMA50.\n<b>SELL order executed (Paper).</b>")
                            success, err = send_telegram_alert(alert_msg)
                            if not success:
                                loop_status.warning(f"Trade executed but Telegram failed: {err}")
                            else:
                                loop_status.success(alert_msg.replace("<b>", "").replace("</b>", ""))
                        except Exception as e:
                            loop_status.error(f"Sell order failed: {e}")
                    else:
                        loop_status.info("Bearish crossover but no position.")
                else:
                    loop_status.info(f"Monitoring {ticker} | Price: ${current_price:.2f} | EMA9: {ema9:.2f} | EMA50: {ema50:.2f}")
            else:
                loop_status.info(f"Initialising... Price: ${current_price:.2f} | EMA9: {ema9:.2f} | EMA50: {ema50:.2f}")

            prev_ema9 = ema9
            prev_ema50 = ema50
            st.session_state.status_message = f"✅ Bot running for {ticker} | Last check: {datetime.now().strftime('%H:%M:%S')}"

            for _ in range(60):
                if not st.session_state.bot_running:
                    break
                time.sleep(1)
        except Exception as e:
            loop_status.error(f"Loop error: {e}")
            time.sleep(60)

    st.session_state.status_message = "⏸️ Bot stopped."
    st.session_state.bot_running = False
    st.session_state.thread = None
    loop_status.empty()

# ------------------------------
# Start / Stop Handlers
# ------------------------------
if start_btn and not st.session_state.bot_running:
    if not alpaca_api_key or not alpaca_secret_key:
        st.error("❌ Alpaca API keys are required.")
    elif not telegram_token or not telegram_chat_id:
        st.error("❌ Telegram credentials are required.")
    elif not ticker:
        st.error("❌ Please enter a ticker symbol.")
    else:
        st.session_state.bot_running = True
        st.session_state.thread = threading.Thread(target=trading_loop, daemon=True)
        st.session_state.thread.start()
        st.success("Bot started! Check the status below.")
        # Optional startup Telegram notification (non‑blocking)
        send_telegram_alert(f"🤖 Trading bot started for {ticker} (Paper trading).")

if stop_btn and st.session_state.bot_running:
    st.session_state.bot_running = False
    if st.session_state.thread is not None:
        st.session_state.thread.join(timeout=5)
    st.session_state.status_message = "⏹️ Bot stopped by user."
    send_telegram_alert(f"🛑 Trading bot stopped manually for {ticker}.")
    st.info("Bot stopped.")

# ------------------------------
# Main Dashboard
# ------------------------------
st.header("📊 Live Market Dashboard")

# Show the current status message (this will update when the bot thread runs)
status_placeholder = st.empty()
if st.session_state.bot_running:
    status_placeholder.success(st.session_state.status_message)
else:
    if "❌" in st.session_state.status_message:
        status_placeholder.error(st.session_state.status_message)
    else:
        status_placeholder.info(st.session_state.status_message)

col1, col2, col3 = st.columns(3)
col1.metric("Latest Price", f"${st.session_state.latest_price}" if st.session_state.latest_price else "N/A")
col2.metric("EMA 9", st.session_state.ema_9 if st.session_state.ema_9 else "N/A")
col3.metric("EMA 50", st.session_state.ema_50 if st.session_state.ema_50 else "N/A")

st.subheader(f"{ticker} Price & EMAs")
chart_placeholder = st.empty()
if not st.session_state.chart_data.empty:
    chart_placeholder.line_chart(st.session_state.chart_data)
else:
    chart_placeholder.info("Waiting for data...")

st.markdown("---")
st.caption("⚙️ The bot checks for crossovers every minute. Use the sidebar to start/stop. All trades are executed in Alpaca **Paper** environment.")
