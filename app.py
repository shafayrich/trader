"""
Streamlit Trading Bot – EMA Crossover Strategy
Modern UI with dashboard + setup guide tabs.
Background thread handles trading; UI auto‑refreshes without freezing.
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
# Page Configuration (Modern, Wide)
# ------------------------------
st.set_page_config(
    page_title="EMA Crossover Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ------------------------------
# Custom CSS for a cleaner look
# ------------------------------
st.markdown("""
<style>
    .stMetric {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .stButton button {
        width: 100%;
        border-radius: 8px;
        font-weight: 500;
    }
    .status-box {
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)

# ------------------------------
# Session State Initialisation
# ------------------------------
if "bot_running" not in st.session_state:
    st.session_state.bot_running = False
if "thread" not in st.session_state:
    st.session_state.thread = None
if "status_message" not in st.session_state:
    st.session_state.status_message = "⚪ Bot is idle."
if "market_status" not in st.session_state:
    st.session_state.market_status = "Unknown"
if "latest_price" not in st.session_state:
    st.session_state.latest_price = None
if "ema_9" not in st.session_state:
    st.session_state.ema_9 = None
if "ema_50" not in st.session_state:
    st.session_state.ema_50 = None
if "chart_data" not in st.session_state:
    st.session_state.chart_data = pd.DataFrame(columns=["Close", "EMA_9", "EMA_50"])
if "loop_log" not in st.session_state:
    st.session_state.loop_log = "Waiting to start..."

# ------------------------------
# Sidebar – API Configuration
# ------------------------------
with st.sidebar:
    st.image("https://streamlit.io/images/brand/streamlit-mark-color.png", width=50)
    st.title("⚙️ Bot Config")

    with st.expander("🔐 Alpaca Paper Trading", expanded=True):
        alpaca_api_key = st.text_input(
            "API Key",
            type="password",
            placeholder="PK...",
            help="Find this in your Alpaca Paper Dashboard"
        )
        alpaca_secret_key = st.text_input(
            "Secret Key",
            type="password",
            placeholder="...",
            help="Keep this secret!"
        )

    with st.expander("📱 Telegram Alerts", expanded=True):
        telegram_token = st.text_input(
            "Bot Token",
            type="password",
            placeholder="123456:ABC...",
            help="From @BotFather"
        )
        telegram_chat_id = st.text_input(
            "Chat ID",
            placeholder="123456789",
            help="Your numeric Telegram Chat ID"
        )

    ticker = st.text_input(
        "📊 Stock Ticker",
        value="AAPL",
        help="Yahoo Finance symbol (e.g., AAPL, TSLA, 2222.SR)"
    ).upper().strip()

    st.markdown("---")
    col1, col2 = st.columns(2)
    start_btn = col1.button("▶️ Start Bot", use_container_width=True, disabled=st.session_state.bot_running)
    stop_btn = col2.button("⏹️ Stop Bot", use_container_width=True, disabled=not st.session_state.bot_running)

    st.markdown("---")
    st.caption("💡 Bot runs in background. Keep this tab open.")

# ------------------------------
# Helper Functions
# ------------------------------
def send_telegram_alert(message: str):
    """Send a message to Telegram. Returns (success, error_message)."""
    if not telegram_token or not telegram_chat_id:
        return False, "Missing credentials"
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {"chat_id": telegram_chat_id, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True, None
        else:
            return False, resp.json().get("description", "Unknown error")
    except Exception as e:
        return False, str(e)

def get_market_status(api):
    """Return (is_open, next_open, next_close) from Alpaca clock."""
    try:
        clock = api.get_clock()
        return clock.is_open, clock.next_open, clock.next_close
    except:
        return False, None, None

# ------------------------------
# Trading Loop (Background Thread)
# ------------------------------
def trading_loop():
    # Validate Alpaca connection
    if not alpaca_api_key or not alpaca_secret_key:
        st.session_state.status_message = "❌ Alpaca API keys missing"
        st.session_state.bot_running = False
        return
    try:
        api = tradeapi.REST(
            alpaca_api_key,
            alpaca_secret_key,
            base_url="https://paper-api.alpaca.markets",
            api_version="v2"
        )
        acc = api.get_account()
        if acc.status != "ACTIVE":
            st.session_state.status_message = "❌ Alpaca account not active"
            st.session_state.bot_running = False
            return
    except Exception as e:
        st.session_state.status_message = f"❌ Alpaca error: {e}"
        st.session_state.bot_running = False
        return

    # Initial market check
    is_open, _, _ = get_market_status(api)
    market_text = "🟢 Open" if is_open else "🔴 Closed"
    st.session_state.market_status = market_text
    st.session_state.status_message = f"✅ Running – {ticker} | Market {market_text}"

    position = None
    prev_ema9 = prev_ema50 = None

    while st.session_state.bot_running:
        try:
            # Market clock update
            is_open, next_open, next_close = get_market_status(api)
            market_text = "🟢 Open" if is_open else "🔴 Closed"
            st.session_state.market_status = market_text

            # Fetch data (Yahoo Finance works even when market closed)
            end = datetime.now()
            start = end - timedelta(days=60)
            data = yf.download(ticker, start=start, end=end, progress=False)
            if data.empty:
                st.session_state.loop_log = f"⚠️ No data for {ticker}"
                time.sleep(60)
                continue

            data['EMA_9'] = data['Close'].ewm(span=9, adjust=False).mean()
            data['EMA_50'] = data['Close'].ewm(span=50, adjust=False).mean()

            latest = data.iloc[-1]
            price = latest['Close']
            ema9 = latest['EMA_9']
            ema50 = latest['EMA_50']

            # Update UI values
            st.session_state.latest_price = round(price, 2)
            st.session_state.ema_9 = round(ema9, 2)
            st.session_state.ema_50 = round(ema50, 2)
            st.session_state.chart_data = data[['Close', 'EMA_9', 'EMA_50']].tail(200)

            # Crossover detection
            if prev_ema9 and prev_ema50:
                if prev_ema9 <= prev_ema50 and ema9 > ema50:   # Bullish
                    st.session_state.loop_log = "🚀 Bullish crossover detected"
                    if is_open and position != "long":
                        try:
                            api.submit_order(symbol=ticker, qty=1, side="buy", type="market", time_in_force="day")
                            position = "long"
                            alert = f"<b>🚀 BULLISH</b> – {ticker} @ ${price:.2f}\nBUY order placed (Paper)."
                            send_telegram_alert(alert)
                            st.session_state.loop_log = "✅ Buy order executed"
                        except Exception as e:
                            st.session_state.loop_log = f"❌ Buy failed: {e}"
                    elif not is_open:
                        st.session_state.loop_log = "📴 Market closed – crossover ignored"
                elif prev_ema9 >= prev_ema50 and ema9 < ema50:  # Bearish
                    st.session_state.loop_log = "🔻 Bearish crossover detected"
                    if is_open and position == "long":
                        try:
                            api.submit_order(symbol=ticker, qty=1, side="sell", type="market", time_in_force="day")
                            position = None
                            alert = f"<b>🔻 BEARISH</b> – {ticker} @ ${price:.2f}\nSELL order placed (Paper)."
                            send_telegram_alert(alert)
                            st.session_state.loop_log = "✅ Sell order executed"
                        except Exception as e:
                            st.session_state.loop_log = f"❌ Sell failed: {e}"
                    elif not is_open:
                        st.session_state.loop_log = "📴 Market closed – crossover ignored"
                else:
                    st.session_state.loop_log = f"Monitoring – Price: ${price:.2f}"
            else:
                st.session_state.loop_log = f"Initialising – Price: ${price:.2f}"

            prev_ema9, prev_ema50 = ema9, ema50
            st.session_state.status_message = f"✅ Running – {ticker} | Market {market_text} | {datetime.now().strftime('%H:%M:%S')}"

            # Wait 60 seconds, but check for stop signal every second
            for _ in range(60):
                if not st.session_state.bot_running:
                    break
                time.sleep(1)

        except Exception as e:
            st.session_state.loop_log = f"⚠️ Loop error: {e}"
            time.sleep(60)

    # Cleanup
    st.session_state.status_message = "⏹️ Bot stopped"
    st.session_state.bot_running = False
    st.session_state.thread = None

# ------------------------------
# Start / Stop Handlers
# ------------------------------
if start_btn and not st.session_state.bot_running:
    if not alpaca_api_key or not alpaca_secret_key:
        st.error("❌ Alpaca API keys are required.")
    elif not telegram_token or not telegram_chat_id:
        st.error("❌ Telegram credentials are required.")
    elif not ticker:
        st.error("❌ Enter a valid ticker.")
    else:
        st.session_state.bot_running = True
        st.session_state.thread = threading.Thread(target=trading_loop, daemon=True)
        st.session_state.thread.start()
        st.success("Bot started! Dashboard updates every 5 seconds.")
        send_telegram_alert(f"🤖 Bot started for {ticker} (Paper trading)")

if stop_btn and st.session_state.bot_running:
    st.session_state.bot_running = False
    if st.session_state.thread:
        st.session_state.thread.join(timeout=5)
    st.session_state.status_message = "⏹️ Bot stopped by user"
    send_telegram_alert(f"🛑 Bot stopped for {ticker}")
    st.info("Bot stopped.")

# ------------------------------
# Main UI with Tabs
# ------------------------------
tab1, tab2 = st.tabs(["📊 Dashboard", "⚙️ Setup Guide"])

with tab1:
    # Header with status
    col_status, col_market = st.columns([3, 1])
    with col_status:
        if "❌" in st.session_state.status_message:
            st.error(st.session_state.status_message)
        elif "✅" in st.session_state.status_message:
            st.success(st.session_state.status_message)
        else:
            st.info(st.session_state.status_message)
    with col_market:
        if st.session_state.market_status == "🟢 Open":
            st.success(f"Market: {st.session_state.market_status}")
        elif st.session_state.market_status == "🔴 Closed":
            st.warning(f"Market: {st.session_state.market_status}")
        else:
            st.info("Market: Checking...")

    # Metrics Row (3 cards)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("💵 Latest Price", f"${st.session_state.latest_price}" if st.session_state.latest_price else "—")
    with col2:
        st.metric("📈 EMA 9", st.session_state.ema_9 if st.session_state.ema_9 else "—")
    with col3:
        st.metric("📉 EMA 50", st.session_state.ema_50 if st.session_state.ema_50 else "—")

    # Chart
    st.subheader(f"📊 {ticker} – Price & EMAs")
    chart_placeholder = st.empty()
    if not st.session_state.chart_data.empty:
        chart_placeholder.line_chart(st.session_state.chart_data)
    else:
        chart_placeholder.info("Waiting for market data...")

    # Live Log (real‑time status from loop)
    st.caption("📋 Live Log")
    log_placeholder = st.empty()
    log_placeholder.code(st.session_state.loop_log, language="text")

    # Footer note
    st.markdown("---")
    st.caption("⚙️ Bot checks every minute. Trades only when market is open.")

with tab2:
    st.header("📘 How to Set Up Your Trading Bot")
    st.markdown("""
    Follow these three steps to get your API credentials. You only need to do this once.
    """)

    col_a, col_b, col_c = st.columns(3)

    with col_a:
        st.subheader("1️⃣ Alpaca Paper Trading")
        st.markdown("""
        1. Go to [app.alpaca.markets/paper](https://app.alpaca.markets/paper)
        2. Sign up or log in.
        3. In the dashboard, click **"View API Keys"**.
        4. Generate a new key (or use existing).
        5. Copy the **API Key** and **Secret Key**.
        """)
        st.info("💡 Paper trading uses fake money – no risk!")

    with col_b:
        st.subheader("2️⃣ Telegram Bot Token")
        st.markdown("""
        1. Open Telegram and search for **@BotFather**.
        2. Start a chat and send `/newbot`.
        3. Follow the prompts to name your bot.
        4. Once created, you'll receive a **Bot Token** (e.g., `123456:ABC...`).
        """)
        st.warning("🔐 Keep this token secret!")

    with col_c:
        st.subheader("3️⃣ Telegram Chat ID")
        st.markdown("""
        1. Search for **@userinfobot** on Telegram.
        2. Start the bot – it will immediately reply with your numeric **Chat ID**.
        3. Copy that number into the sidebar.
        """)
        st.success("✅ That's it! Enter all three in the sidebar and click **Start Bot**.")

    st.markdown("---")
    st.subheader("💡 Tips for Running the Bot")
    st.markdown("""
    - **Keep the browser tab open** – the bot runs in the background as long as this Streamlit app is active.
    - The bot checks for EMA crossovers **every minute**.
    - Trades are only submitted when the market is open (🟢 Open). You'll see a warning otherwise.
    - All orders go to Alpaca **Paper Trading** – no real money is used.
    - If you close the tab, the bot stops. To run 24/7, deploy on a cloud server or use Streamlit Community Cloud with a paid plan.
    """)

# ------------------------------
# Auto‑refresh for Live Updates (fixes freezing issue)
# ------------------------------
if st.session_state.bot_running:
    # Rerun the script every 5 seconds to pull fresh data from session_state
    time.sleep(5)
    st.rerun()
