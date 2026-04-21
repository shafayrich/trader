"""
TraderMoney – Advanced Trading Bot
Modular, multi‑threaded CustomTkinter desktop app.
"""

import customtkinter as ctk
import yfinance as yf
import pandas as pd
import requests
import threading
import queue
import time
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
import alpaca_trade_api as tradeapi
from tkinter import messagebox
import traceback

# For live chart embedding
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# ------------------------------
# App Configuration & Constants
# ------------------------------
CONFIG_FILE = os.path.expanduser("~/.tradermoney_config.json")
DEFAULT_EMAS = (9, 50)
DEFAULT_TICKER = "AAPL"
DEFAULT_QUANTITY = 1

# ------------------------------
# Secure Config Manager
# ------------------------------
class ConfigManager:
    """Handles loading/saving API credentials and user preferences."""
    @staticmethod
    def load() -> Dict[str, Any]:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    @staticmethod
    def save(config: Dict[str, Any]) -> None:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

# ------------------------------
# Data Fetcher (with connection pooling)
# ------------------------------
class DataFetcher:
    """Handles all external API calls with session pooling."""
    def __init__(self):
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20)
        self.session.mount('https://', adapter)
        self.session.headers.update({'User-Agent': 'TraderMoney/2.0'})

    def fetch_historical(self, ticker: str, days: int = 60) -> Optional[pd.DataFrame]:
        """Fetch historical data using yfinance with retries."""
        for attempt in range(3):
            try:
                ticker_obj = yf.Ticker(ticker, session=self.session)
                end = datetime.now()
                start = end - timedelta(days=days)
                df = ticker_obj.history(start=start, end=end, interval="1d", prepost=False)
                if not df.empty:
                    return df
            except Exception:
                time.sleep(2)
        return None

    def get_market_status(self, api: tradeapi.REST) -> bool:
        """Return True if market is open."""
        try:
            return api.get_clock().is_open
        except:
            return False

# ------------------------------
# Trading Engine (Background Thread)
# ------------------------------
class TradingEngine(threading.Thread):
    """Runs the strategy loop and communicates with UI via queues."""
    def __init__(self, ui_queue: queue.Queue, config: Dict[str, Any]):
        super().__init__(daemon=True)
        self.ui_queue = ui_queue
        self.config = config
        self.running = False
        self.fetcher = DataFetcher()
        self.alpaca_api: Optional[tradeapi.REST] = None
        self.position = 0  # current number of shares held
        self.prev_ema = (None, None)

    def connect_alpaca(self) -> bool:
        """Initialize Alpaca REST client."""
        creds = self.config.get("alpaca", {})
        key = creds.get("api_key")
        secret = creds.get("secret_key")
        if not key or not secret:
            self.ui_queue.put(("error", "Alpaca credentials missing"))
            return False
        try:
            self.alpaca_api = tradeapi.REST(key, secret,
                                           base_url="https://paper-api.alpaca.markets",
                                           api_version="v2")
            acc = self.alpaca_api.get_account()
            if acc.status != "ACTIVE":
                self.ui_queue.put(("error", "Alpaca account not active"))
                return False
            return True
        except Exception as e:
            self.ui_queue.put(("error", f"Alpaca connection failed: {e}"))
            return False

    def send_telegram(self, message: str) -> None:
        """Send Telegram alert if configured."""
        tg = self.config.get("telegram", {})
        token = tg.get("token")
        chat_id = tg.get("chat_id")
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=5)
        except:
            pass

    def place_bracket_order(self, symbol: str, qty: int, side: str, 
                            sl_percent: float, tp_percent: float) -> bool:
        """Place a bracket order with stop loss and take profit."""
        try:
            price = float(self.alpaca_api.get_last_trade(symbol).price)
            if side == "buy":
                stop_loss_price = round(price * (1 - sl_percent/100), 2)
                take_profit_price = round(price * (1 + tp_percent/100), 2)
            else:
                stop_loss_price = round(price * (1 + sl_percent/100), 2)
                take_profit_price = round(price * (1 - tp_percent/100), 2)

            self.alpaca_api.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type="market",
                time_in_force="gtc",
                order_class="bracket",
                stop_loss={"stop_price": stop_loss_price},
                take_profit={"limit_price": take_profit_price}
            )
            return True
        except Exception as e:
            self.ui_queue.put(("error", f"Bracket order failed: {e}"))
            return False

    def close_all_positions(self) -> None:
        """Emergency kill switch."""
        if not self.alpaca_api:
            return
        try:
            self.alpaca_api.close_all_positions()
            self.position = 0
            self.ui_queue.put(("log", "⚠️ KILL SWITCH: All positions closed"))
            self.send_telegram("⚠️ KILL SWITCH ACTIVATED – All positions closed")
        except Exception as e:
            self.ui_queue.put(("error", f"Kill switch failed: {e}"))

    def run(self):
        """Main strategy loop."""
        if not self.connect_alpaca():
            self.ui_queue.put(("status", "❌ Alpaca connection failed"))
            return

        ticker = self.config.get("ticker", DEFAULT_TICKER).upper()
        mode = self.config.get("mode", "signal")
        qty = self.config.get("quantity", DEFAULT_QUANTITY)
        ema_fast, ema_slow = self.config.get("emas", DEFAULT_EMAS)
        use_bracket = self.config.get("use_bracket", False)
        sl_pct = self.config.get("sl_percent", 2.0)
        tp_pct = self.config.get("tp_percent", 4.0)

        self.ui_queue.put(("status", f"✅ Running {ticker} | Mode: {mode} | Qty: {qty}"))
        self.send_telegram(f"🤖 Bot started for {ticker} ({mode} mode)")

        while self.running:
            try:
                # Market status
                is_open = self.fetcher.get_market_status(self.alpaca_api)
                self.ui_queue.put(("market", "🟢 Open" if is_open else "🔴 Closed"))

                # Fetch data
                df = self.fetcher.fetch_historical(ticker, 60)
                if df is None or df.empty:
                    self.ui_queue.put(("log", f"⚠️ Data fetch failed for {ticker}"))
                    time.sleep(60)
                    continue

                # Compute EMAs
                df['EMA_fast'] = df['Close'].ewm(span=ema_fast, adjust=False).mean()
                df['EMA_slow'] = df['Close'].ewm(span=ema_slow, adjust=False).mean()
                latest = df.iloc[-1]
                price = latest['Close']
                ema_f = latest['EMA_fast']
                ema_s = latest['EMA_slow']

                # Update UI
                self.ui_queue.put(("price", round(price, 2)))
                self.ui_queue.put(("ema", (round(ema_f, 2), round(ema_s, 2))))
                self.ui_queue.put(("chart_data", df[['Close', 'EMA_fast', 'EMA_slow']].tail(100)))

                # Check for crossover
                prev_f, prev_s = self.prev_ema
                if prev_f is not None and prev_s is not None:
                    signal = None
                    if prev_f <= prev_s and ema_f > ema_s:
                        signal = "BUY"
                        rationale = (f"BUY Signal: EMA{ema_fast} crossed above EMA{ema_slow} "
                                     f"indicating bullish momentum at ${price:.2f}")
                    elif prev_f >= prev_s and ema_f < ema_s:
                        signal = "SELL"
                        rationale = (f"SELL Signal: EMA{ema_fast} crossed below EMA{ema_slow} "
                                     f"indicating bearish momentum at ${price:.2f}")

                    if signal:
                        self.ui_queue.put(("log", f"🚀 {signal} signal detected"))
                        self.ui_queue.put(("rationale", rationale))
                        alert_msg = f"<b>{signal} Signal</b> – {ticker} @ ${price:.2f}\n{rationale}"
                        self.send_telegram(alert_msg)

                        if mode == "auto" and is_open:
                            if signal == "BUY" and self.position == 0:
                                try:
                                    if use_bracket:
                                        success = self.place_bracket_order(ticker, qty, "buy", sl_pct, tp_pct)
                                    else:
                                        self.alpaca_api.submit_order(symbol=ticker, qty=qty, side="buy",
                                                                     type="market", time_in_force="day")
                                        success = True
                                    if success:
                                        self.position = qty
                                        self.ui_queue.put(("log", f"✅ Bought {qty} shares"))
                                        self.send_telegram(f"✅ Bought {qty} {ticker} @ ${price:.2f}")
                                except Exception as e:
                                    self.ui_queue.put(("error", f"Buy failed: {e}"))
                            elif signal == "SELL" and self.position > 0:
                                try:
                                    if use_bracket:
                                        success = self.place_bracket_order(ticker, self.position, "sell", sl_pct, tp_pct)
                                    else:
                                        self.alpaca_api.submit_order(symbol=ticker, qty=self.position, side="sell",
                                                                     type="market", time_in_force="day")
                                        success = True
                                    if success:
                                        self.ui_queue.put(("log", f"✅ Sold {self.position} shares"))
                                        self.send_telegram(f"✅ Sold {self.position} {ticker} @ ${price:.2f}")
                                        self.position = 0
                                except Exception as e:
                                    self.ui_queue.put(("error", f"Sell failed: {e}"))
                        else:
                            reason = "market closed" if not is_open else "signal-only mode"
                            self.ui_queue.put(("log", f"ℹ️ No execution: {reason}"))

                self.prev_ema = (ema_f, ema_s)

                # Update account P&L
                if self.alpaca_api:
                    try:
                        acc = self.alpaca_api.get_account()
                        equity = float(acc.equity)
                        pl = float(acc.equity) - float(acc.last_equity)
                        self.ui_queue.put(("account", (round(equity, 2), round(pl, 2))))
                    except:
                        pass

                # Wait 60 seconds (check stop flag each second)
                for _ in range(60):
                    if not self.running:
                        break
                    time.sleep(1)

            except Exception as e:
                self.ui_queue.put(("error", f"Loop error: {traceback.format_exc()}"))
                time.sleep(60)

        self.ui_queue.put(("status", "⏹️ Bot stopped"))
        self.send_telegram("🛑 Bot stopped")

    def stop(self):
        self.running = False

# ------------------------------
# Main Application Window
# ------------------------------
class TraderMoneyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TraderMoney")
        self.geometry("1300x800")
        self.minsize(1100, 700)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")
        self.configure(fg_color="#0A0C0F")

        # Load config
        self.config = ConfigManager.load()
        if not self.config:
            self.config = {"alpaca": {}, "telegram": {}, "ticker": DEFAULT_TICKER,
                           "mode": "signal", "quantity": DEFAULT_QUANTITY,
                           "emas": DEFAULT_EMAS, "use_bracket": False,
                           "sl_percent": 2.0, "tp_percent": 4.0}

        # Communication queue from engine to UI
        self.ui_queue = queue.Queue()
        self.engine: Optional[TradingEngine] = None

        # Build UI
        self.create_widgets()
        self.after(100, self.process_queue)

    def create_widgets(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---------- Sidebar ----------
        self.sidebar = ctk.CTkFrame(self, width=300, corner_radius=0, fg_color="#11151A")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(10, weight=1)

        # Config sections
        ctk.CTkLabel(self.sidebar, text="⚙️ Configuration", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#E0E0E0").pack(pady=(20,10))

        # Alpaca
        self.alpaca_frame = self._create_section_frame("🔐 Alpaca Paper")
        self.api_key = self._add_entry(self.alpaca_frame, "API Key", show="*")
        self.secret_key = self._add_entry(self.alpaca_frame, "Secret Key", show="*")
        # Pre-fill from config
        self.api_key.insert(0, self.config["alpaca"].get("api_key", ""))
        self.secret_key.insert(0, self.config["alpaca"].get("secret_key", ""))

        # Telegram
        self.tg_frame = self._create_section_frame("📱 Telegram")
        self.tg_token = self._add_entry(self.tg_frame, "Bot Token", show="*")
        self.tg_chat = self._add_entry(self.tg_frame, "Chat ID")
        self.tg_token.insert(0, self.config["telegram"].get("token", ""))
        self.tg_chat.insert(0, self.config["telegram"].get("chat_id", ""))

        # Ticker
        self.ticker_frame = self._create_section_frame("📊 Ticker")
        self.ticker_entry = self._add_entry(self.ticker_frame, "Symbol")
        self.ticker_entry.insert(0, self.config.get("ticker", DEFAULT_TICKER))

        # EMA Settings
        self.ema_frame = self._create_section_frame("📈 EMAs")
        self.ema_fast = self._add_entry(self.ema_frame, "Fast EMA", width=80)
        self.ema_slow = self._add_entry(self.ema_frame, "Slow EMA", width=80)
        self.ema_fast.insert(0, str(self.config["emas"][0]))
        self.ema_slow.insert(0, str(self.config["emas"][1]))

        # Quantity
        self.qty_frame = self._create_section_frame("💰 Quantity")
        self.qty_entry = self._add_entry(self.qty_frame, "Shares")
        self.qty_entry.insert(0, str(self.config.get("quantity", DEFAULT_QUANTITY)))

        # Mode Toggle
        self.mode_frame = self._create_section_frame("🎛️ Mode")
        self.mode_var = ctk.StringVar(value=self.config.get("mode", "signal"))
        ctk.CTkRadioButton(self.mode_frame, text="Signal Only", variable=self.mode_var,
                           value="signal", text_color="#E0E0E0").pack(anchor="w", padx=10, pady=2)
        ctk.CTkRadioButton(self.mode_frame, text="Auto Trade", variable=self.mode_var,
                           value="auto", text_color="#E0E0E0").pack(anchor="w", padx=10, pady=2)

        # Bracket Orders Toggle
        self.bracket_frame = self._create_section_frame("🛡️ Bracket Orders")
        self.bracket_var = ctk.BooleanVar(value=self.config.get("use_bracket", False))
        ctk.CTkCheckBox(self.bracket_frame, text="Enable SL/TP", variable=self.bracket_var,
                        text_color="#E0E0E0").pack(anchor="w", padx=10)
        self.sl_entry = self._add_entry(self.bracket_frame, "SL %", width=60)
        self.tp_entry = self._add_entry(self.bracket_frame, "TP %", width=60)
        self.sl_entry.insert(0, str(self.config.get("sl_percent", 2.0)))
        self.tp_entry.insert(0, str(self.config.get("tp_percent", 4.0)))

        # Save Credentials Button
        ctk.CTkButton(self.sidebar, text="💾 Save Credentials", command=self.save_credentials,
                      fg_color="#2D3748", hover_color="#3A4A5A").pack(pady=10, padx=20, fill="x")

        # Kill Switch
        self.kill_btn = ctk.CTkButton(self.sidebar, text="⚠️ KILL SWITCH – CLOSE ALL",
                                      command=self.kill_switch, fg_color="#8B0000", hover_color="#A52A2A")
        self.kill_btn.pack(pady=10, padx=20, fill="x")
        self.kill_btn.configure(state="disabled")

        # Start/Stop
        self.btn_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.btn_frame.pack(pady=20, padx=20, fill="x")
        self.start_btn = ctk.CTkButton(self.btn_frame, text="▶️ Start Bot", command=self.start_bot,
                                       fg_color="#00A896", hover_color="#008B7A")
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0,5))
        self.stop_btn = ctk.CTkButton(self.btn_frame, text="⏹️ Stop Bot", command=self.stop_bot,
                                      fg_color="#555555", hover_color="#666666", state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(5,0))

        # ---------- Main Content ----------
        self.main = ctk.CTkFrame(self, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=0)  # header
        self.main.grid_rowconfigure(1, weight=0)  # metrics
        self.main.grid_rowconfigure(2, weight=1)  # chart
        self.main.grid_rowconfigure(3, weight=1)  # log & rationale

        # Header
        ctk.CTkLabel(self.main, text="💸 TraderMoney", font=ctk.CTkFont(size=32, weight="bold"),
                     text_color="#00C9B1").grid(row=0, column=0, sticky="w")

        # Metrics Row
        self.metrics_frame = ctk.CTkFrame(self.main, fg_color="transparent")
        self.metrics_frame.grid(row=1, column=0, sticky="ew", pady=(10,20))
        self.metrics_frame.grid_columnconfigure((0,1,2,3,4), weight=1)
        self.price_label = self._metric_card(self.metrics_frame, "💵 Price", "—", 0)
        self.ema_fast_label = self._metric_card(self.metrics_frame, "📈 Fast EMA", "—", 1)
        self.ema_slow_label = self._metric_card(self.metrics_frame, "📉 Slow EMA", "—", 2)
        self.equity_label = self._metric_card(self.metrics_frame, "💰 Equity", "—", 3)
        self.pl_label = self._metric_card(self.metrics_frame, "📊 Daily P&L", "—", 4)

        # Status Bar
        self.status_frame = ctk.CTkFrame(self.main, fg_color="transparent")
        self.status_frame.grid(row=2, column=0, sticky="ew", pady=(0,10))
        self.status_label = ctk.CTkLabel(self.status_frame, text="⚪ Bot is idle.", text_color="#E0E0E0")
        self.status_label.pack(side="left")
        self.market_label = ctk.CTkLabel(self.status_frame, text="Market: —", text_color="#E0E0E0")
        self.market_label.pack(side="right")

        # Chart Frame
        self.chart_frame = ctk.CTkFrame(self.main, fg_color="#11151A", border_color="#2A3440", border_width=1)
        self.chart_frame.grid(row=3, column=0, sticky="nsew", pady=(0,10))
        self.chart_frame.grid_rowconfigure(0, weight=1)
        self.chart_frame.grid_columnconfigure(0, weight=1)
        self.fig = Figure(figsize=(8, 4), dpi=100, facecolor="#11151A")
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#11151A")
        self.ax.tick_params(colors="#E0E0E0")
        self.ax.spines['bottom'].set_color('#2A3440')
        self.ax.spines['left'].set_color('#2A3440')
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # Rationale & Log Area
        self.bottom_frame = ctk.CTkFrame(self.main, fg_color="transparent")
        self.bottom_frame.grid(row=4, column=0, sticky="nsew")
        self.bottom_frame.grid_columnconfigure(0, weight=1)
        self.bottom_frame.grid_rowconfigure(0, weight=0)
        self.bottom_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self.bottom_frame, text="📝 Trade Rationale", text_color="#00C9B1").grid(row=0, column=0, sticky="w")
        self.rationale_text = ctk.CTkTextbox(self.bottom_frame, height=50, fg_color="#0A0C0F",
                                             text_color="#E0E0E0", border_width=1, border_color="#2A3440")
        self.rationale_text.grid(row=1, column=0, sticky="ew", pady=(5,10))
        self.rationale_text.insert("0.0", "Waiting for signal...")
        self.rationale_text.configure(state="disabled")

        ctk.CTkLabel(self.bottom_frame, text="📋 Live Log", text_color="#00C9B1").grid(row=2, column=0, sticky="w")
        self.log_text = ctk.CTkTextbox(self.bottom_frame, height=120, fg_color="#0A0C0F",
                                       text_color="#E0E0E0", border_width=1, border_color="#2A3440")
        self.log_text.grid(row=3, column=0, sticky="nsew", pady=(5,0))
        self.log_text.insert("0.0", "Ready.\n")
        self.log_text.configure(state="disabled")

    def _create_section_frame(self, title):
        frame = ctk.CTkFrame(self.sidebar, fg_color="#1A1F26")
        frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold"),
                     text_color="#00C9B1").pack(anchor="w", padx=10, pady=(10,5))
        return frame

    def _add_entry(self, parent, placeholder, show="", width=None):
        entry = ctk.CTkEntry(parent, placeholder_text=placeholder, show=show,
                             fg_color="#0F1419", border_color="#2A3440", text_color="#FFFFFF",
                             width=width if width else None)
        entry.pack(fill="x", padx=10, pady=5)
        return entry

    def _metric_card(self, parent, title, value, col):
        card = ctk.CTkFrame(parent, fg_color="#11151A", border_color="#2A3440", border_width=1, corner_radius=8)
        card.grid(row=0, column=col, padx=5, sticky="nsew")
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=11), text_color="#8B949E").pack(pady=(8,0))
        label = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=18, weight="bold"), text_color="#00C9B1")
        label.pack(pady=(0,8))
        return label

    def save_credentials(self):
        self.config["alpaca"] = {"api_key": self.api_key.get(), "secret_key": self.secret_key.get()}
        self.config["telegram"] = {"token": self.tg_token.get(), "chat_id": self.tg_chat.get()}
        self.config["ticker"] = self.ticker_entry.get().upper()
        self.config["emas"] = (int(self.ema_fast.get()), int(self.ema_slow.get()))
        self.config["quantity"] = int(self.qty_entry.get())
        self.config["mode"] = self.mode_var.get()
        self.config["use_bracket"] = self.bracket_var.get()
        self.config["sl_percent"] = float(self.sl_entry.get())
        self.config["tp_percent"] = float(self.tp_entry.get())
        ConfigManager.save(self.config)
        messagebox.showinfo("Saved", "Credentials saved securely.")

    def start_bot(self):
        if self.engine and self.engine.running:
            return
        # Update config from UI
        self.config["alpaca"] = {"api_key": self.api_key.get(), "secret_key": self.secret_key.get()}
        self.config["telegram"] = {"token": self.tg_token.get(), "chat_id": self.tg_chat.get()}
        self.config["ticker"] = self.ticker_entry.get().upper()
        self.config["emas"] = (int(self.ema_fast.get()), int(self.ema_slow.get()))
        self.config["quantity"] = int(self.qty_entry.get())
        self.config["mode"] = self.mode_var.get()
        self.config["use_bracket"] = self.bracket_var.get()
        self.config["sl_percent"] = float(self.sl_entry.get())
        self.config["tp_percent"] = float(self.tp_entry.get())

        self.engine = TradingEngine(self.ui_queue, self.config)
        self.engine.running = True
        self.engine.start()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.kill_btn.configure(state="normal")
        self.status_label.configure(text="⏳ Bot starting...")
        self._log("Bot started")

    def stop_bot(self):
        if self.engine:
            self.engine.stop()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.kill_btn.configure(state="disabled")
        self.status_label.configure(text="⏹️ Bot stopped")

    def kill_switch(self):
        if self.engine and self.engine.alpaca_api:
            # Run in thread to avoid UI freeze
            threading.Thread(target=self.engine.close_all_positions, daemon=True).start()
            self._log("Kill switch triggered")

    def process_queue(self):
        """Process messages from the trading engine."""
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                if msg[0] == "price":
                    self.price_label.configure(text=f"${msg[1]:.2f}")
                elif msg[0] == "ema":
                    self.ema_fast_label.configure(text=f"{msg[1][0]:.2f}")
                    self.ema_slow_label.configure(text=f"{msg[1][1]:.2f}")
                elif msg[0] == "account":
                    self.equity_label.configure(text=f"${msg[1][0]:.2f}")
                    color = "#00C9B1" if msg[1][1] >= 0 else "#F85149"
                    self.pl_label.configure(text=f"${msg[1][1]:.2f}", text_color=color)
                elif msg[0] == "market":
                    self.market_label.configure(text=f"Market: {msg[1]}")
                elif msg[0] == "status":
                    self.status_label.configure(text=msg[1])
                elif msg[0] == "log":
                    self._log(msg[1])
                elif msg[0] == "error":
                    self._log(f"❌ {msg[1]}")
                elif msg[0] == "rationale":
                    self.rationale_text.configure(state="normal")
                    self.rationale_text.delete("0.0", "end")
                    self.rationale_text.insert("0.0", msg[1])
                    self.rationale_text.configure(state="disabled")
                elif msg[0] == "chart_data":
                    self._update_chart(msg[1])
        except queue.Empty:
            pass
        self.after(100, self.process_queue)

    def _log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{datetime.now().strftime('%H:%M:%S')}  {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _update_chart(self, df):
        self.ax.clear()
        self.ax.plot(df.index, df['Close'], label='Close', color='#E0E0E0', linewidth=1.5)
        self.ax.plot(df.index, df['EMA_fast'], label=f'EMA{self.config["emas"][0]}', color='#00C9B1', linewidth=1)
        self.ax.plot(df.index, df['EMA_slow'], label=f'EMA{self.config["emas"][1]}', color='#F85149', linewidth=1)
        self.ax.legend(loc='upper left', facecolor='#11151A', edgecolor='#2A3440', labelcolor='#E0E0E0')
        self.ax.set_facecolor("#11151A")
        self.ax.tick_params(colors="#E0E0E0")
        self.fig.tight_layout()
        self.canvas.draw()

# ------------------------------
# Entry Point
# ------------------------------
if __name__ == "__main__":
    app = TraderMoneyApp()
    app.mainloop()
