"""
TraderMoney – EMA Crossover Trading Bot
Forced dark theme – all users see the same professional dashboard.
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
# Page Configuration
# ------------------------------
st.set_page_config(
    page_title="TraderMoney",
    page_icon="💸",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': None,
        'Report a bug': None,
        'About': None
    }
)

# ------------------------------
# Session State
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
# Custom CSS – Forced Dark Theme (No Streamlit Bleed)
# ------------------------------
def inject_forced_dark_css():
    st.markdown("""
    <style>
        /* =============================================
           HIDE STREAMLIT DEFAULT UI ELEMENTS
           ============================================= */
        #MainMenu {visibility: hidden !important;}
        footer {visibility: hidden !important;}
        header {visibility: hidden !important;}
        .stDeployButton {display: none !important;}
        div[data-testid="stToolbar"] {display: none !important;}
        div[data-testid="stDecoration"] {display: none !important;}
        div[data-testid="stStatusWidget"] {display: none !important;}

        /* =============================================
           FORCE DARK THEME – OVERRIDE STREAMLIT VARIABLES
           ============================================= */
        :root {
            --background-color: #0F1117 !important;
            --secondary-background-color: #161B22 !important;
            --text-color: #E6EDF3 !important;
            --font: 'Inter', sans-serif !important;
        }

        @media (prefers-color-scheme: dark) {
            :root {
                --background-color: #0F1117 !important;
                --secondary-background-color: #161B22 !important;
                --text-color: #E6EDF3 !important;
            }
        }
        @media (prefers-color-scheme: light) {
            :root {
                --background-color: #0F1117 !important;
                --secondary-background-color: #161B22 !important;
                --text-color: #E6EDF3 !important;
            }
        }

        /* Global background */
        .stApp {
            background-color: #0F1117 !important;
            color: #E6EDF3 !important;
        }

        .main .block-container {
            background-color: #0F1117 !important;
        }

        /* Sidebar */
        section[data-testid="stSidebar"] {
            background-color: #161B22 !important;
            border-right: 1px solid #30363D !important;
        }
        section[data-testid="stSidebar"] * {
            color: #E6EDF3 !important;
        }

        /* Metric cards */
        div[data-testid="stMetric"] {
            background-color: #161B22 !important;
            border: 1px solid #30363D !important;
            border-radius: 16px !important;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3) !important;
        }
        div[data-testid="stMetric"] label {
            color: #8B949E !important;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            color: #E6EDF3 !important;
        }

        /* Buttons */
        .stButton button {
            background-color: #238636 !important;
            color: white !important;
            border: 1px solid #2EA043 !important;
            border-radius: 10px !important;
        }
        .stButton button:hover {
            background-color: #2EA043 !important;
        }
        .stButton button:disabled {
            background-color: #21262D !important;
            color: #8B949E !important;
            border-color: #30363D !important;
        }

        /* Input fields */
        .stTextInput input, .stTextInput textarea {
            background-color: #0D1117 !important;
            color: #E6EDF3 !important;
            border: 1px solid #30363D !important;
            border-radius: 10px !important;
        }
        .stTextInput input:focus {
            border-color: #58A6FF !important;
            box-shadow: 0 0 0 2px rgba(88,166,255,0.2) !important;
        }

        /* Expanders */
        .streamlit-expanderHeader {
            background-color: #21262D !important;
            color: #E6EDF3 !important;
            border: 1px solid #30363D !important;
            border-radius: 10px !important;
        }

        /* Tabs */
        .stTabs [data-baseweb="tab"] {
            background-color: #21262D !important;
            color: #8B949E !important;
            border: 1px solid #30363D !important;
            border-bottom: none !important;
        }
        .stTabs [aria-selected="true"] {
            background-color: #161B22 !important;
            color: #E6EDF3 !important;
            border-bottom: 3px solid #238636 !important;
        }

        /* Alerts */
        div[data-testid="stAlert"] {
            background-color: #161B22 !important;
            border-left-width: 4px !important;
            border-left-style: solid !important;
            border-radius: 12px !important;
        }

        /* Chart container */
        div[data-testid="stArrowVegaLiteChart"] {
            background-color: #161B22 !important;
            border: 1px solid #30363D !important;
            border-radius: 16px !important;
            padding: 1rem !important;
        }

        /* Code blocks */
        .stCodeBlock {
            background-color: #0D1117 !important;
            border: 1px solid #30363D !important;
            border-radius: 12px !important;
        }
        .stCodeBlock code {
            color: #E6EDF3 !important;
        }

        /* Dividers */
        hr {
            border-color: #30363D !important;
        }

        /* Custom TraderMoney title */
        .tradermoney-title {
            font-size: 3.2rem !important;
            font-weight: 700 !important;
            background: linear-gradient(135deg, #58A6FF, #3FB950) !important;
            -webkit-background-clip: text !important;
            -webkit-text-fill-color: transparent !important;
            background-clip: text !important;
            margin-bottom: 0.2rem !important;
        }
    </style>
    """, unsafe_allow_html=True)

inject_forced_dark_css()

# ------------------------------
# Sidebar – Configuration
# ------------------------------
with st.sidebar:
    st.markdown("<h2 style='text-align: center; color: #E6EDF3;'>⚙️ Configuration</h2>", unsafe_allow_html=True)
    st.divider()

    with st.expander("🔐 Alpaca Paper Trading", expanded=True):
        alpaca_api_key = st.text_input("API Key", type="password", placeholder="PK...", help="From Alpaca Paper Dashboard")
        alpaca_secret_key = st.text_input("Secret Key", type="password", placeholder="...", help="Keep this secret")

    st.divider()

    with st.expander("📱 Telegram Alerts", expanded=True):
        telegram_token = st.text_input("Bot Token", type="password", placeholder="123456:ABC...", help="From @BotFather")
        telegram_chat_id = st.text_input("Chat ID", placeholder="123456789", help="Your numeric Chat ID")

    st.divider()

    ticker = st.text_input("📊 Stock Ticker", value="AAPL", help="Yahoo Finance symbol (e.g., AAPL, TSLA)").upper().strip()

    st.divider()

    col1, col2 = st.columns(2)
    start_btn = col1.button("▶️ Start Bot", use_container_width=True, disabled=st.session_state.bot_running)
    stop_btn = col2.button("⏹️ Stop Bot", use_container_width=True, disabled=not st.session_state.bot_running)

    st.divider()
    st.caption("💡 Keep this tab open for continuous operation.")

# ------------------------------
# Helper Functions
# ------------------------------
def fetch_market_status(api_key, secret_key):
    if not api_key or not secret_key:
        return False, None, None
    try:
        api = tradeapi.REST(api_key, secret_key, base_url="https://paper-api.alpaca.markets", api_version="v2")
        clock = api.get_clock()
        return clock.is_open, clock.next_open, clock.next_close
    except:
        return False, None, None

if not st.session_state.bot_running:
    is_open, _, _ = fetch_market_status(alpaca_api_key, alpaca_secret_key)
    st.session_state.market_status = "🟢 Open" if is_open else "🔴 Closed" if alpaca_api_key else "Unknown"

def send_telegram_alert(message: str):
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

# ------------------------------
# Trading Loop (Background Thread)
# ------------------------------
def trading_loop():
    if not alpaca_api_key or not alpaca_secret_key:
        st.session_state.status_message = "❌ Alpaca API keys missing"
        st.session_state.bot_running = False
        return
    try:
        api = tradeapi.REST(alpaca_api_key, alpaca_secret_key, base_url="https://paper-api.alpaca.markets", api_version="v2")
        acc = api.get_account()
        if acc.status != "ACTIVE":
            st.session_state.status_message = "❌ Alpaca account not active"
            st.session_state.bot_running = False
            return
    except Exception as e:
        st.session_state.status_message = f"❌ Alpaca error: {e}"
        st.session_state.bot_running = False
        return

    is_open, _, _ = fetch_market_status(alpaca_api_key, alpaca_secret_key)
    market_text = "🟢 Open" if is_open else "🔴 Closed"
    st.session_state.market_status = market_text
    st.session_state.status_message = f"✅ Running – {ticker} | Market {market_text}"

    position = None
    prev_ema9 = prev_ema50 = None

    while st.session_state.bot_running:
        try:
            is_open, _, _ = fetch_market_status(alpaca_api_key, alpaca_secret_key)
            market_text = "🟢 Open" if is_open else "🔴 Closed"
            st.session_state.market_status = market_text

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

            st.session_state.latest_price = round(price, 2)
            st.session_state.ema_9 = round(ema9, 2)
            st.session_state.ema_50 = round(ema50, 2)
            st.session_state.chart_data = data[['Close', 'EMA_9', 'EMA_50']].tail(200)

            if prev_ema9 and prev_ema50:
                if prev_ema9 <= prev_ema50 and ema9 > ema50:
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
                elif prev_ema9 >= prev_ema50 and ema9 < ema50:
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

            for _ in range(60):
                if not st.session_state.bot_running:
                    break
                time.sleep(1)

        except Exception as e:
            st.session_state.loop_log = f"⚠️ Loop error: {e}"
            time.sleep(60)

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
# Main UI – TraderMoney Branding + Tabs
# ------------------------------
st.markdown("<div class='tradermoney-title'>💸 TraderMoney</div>", unsafe_allow_html=True)
st.caption("Automated EMA Crossover Trading – Alpaca Paper + Telegram Alerts")

tab1, tab2 = st.tabs(["📊 Dashboard", "⚙️ Setup Guide"])

with tab1:
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

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("💵 Latest Price", f"${st.session_state.latest_price}" if st.session_state.latest_price else "—")
    with col2:
        st.metric("📈 EMA 9", st.session_state.ema_9 if st.session_state.ema_9 else "—")
    with col3:
        st.metric("📉 EMA 50", st.session_state.ema_50 if st.session_state.ema_50 else "—")

    st.subheader(f"📊 {ticker} – Price & EMAs")
    if not st.session_state.chart_data.empty:
        st.line_chart(st.session_state.chart_data)
    else:
        st.info("Waiting for market data...")

    st.caption("📋 Live Log")
    st.code(st.session_state.loop_log, language="text")

    st.divider()
    st.caption("⚙️ Bot checks every minute. Trades only when market is open.")

with tab2:
    st.header("📘 How to Set Up TraderMoney")
    st.markdown("Follow these three steps to get your API credentials. You only need to do this once.")

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

    st.divider()
    st.subheader("💡 Important – Keep the Bot Running")
    st.warning("""
    ⚠️ **Do not close this browser tab!** The bot runs inside this Streamlit app.
    - If you close the tab, the bot stops.
    - To run 24/7, deploy on a cloud server (e.g., Streamlit Community Cloud with a paid plan) or use a VPS.
    - The bot checks for EMA crossovers **every minute**.
    - Trades are only submitted when the market is open (🟢 Open).
    - All orders go to Alpaca **Paper Trading** – no real money is used.
    """)

# ------------------------------
# Auto‑refresh for Live Updates
# ------------------------------
if st.session_state.bot_running:
    time.sleep(5)
    st.rerun()
