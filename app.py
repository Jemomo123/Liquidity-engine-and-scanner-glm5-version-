import streamlit as st
import pandas as pd
import numpy as np
import ccxt
from datetime import datetime
import streamlit.components.v1 as components

# --- Configuration ---
EXCHANGE_QUEUE = [
    {'id': 'binanceusdm', 'name': 'Binance Futures', 'type': 'swap'},
    {'id': 'okx', 'name': 'OKX', 'type': 'swap'},
    {'id': 'bybit', 'name': 'Bybit', 'type': 'linear'},
    {'id': 'gateio', 'name': 'Gate.io', 'type': 'swap'},
    {'id': 'mexc', 'name': 'MEXC', 'type': 'swap'}
]

TIMEFRAMES = {"3m": "3m", "5m": "5m", "15m": "15m"}

# --- 1. Core Indicator Logic ---

def calculate_sma(data, length): 
    return data['Close'].rolling(window=length).mean()

def calculate_atr(data, length):
    high, low, close = data['High'], data['Low'], data['Close'].shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - close), abs(low - close)))
    return tr.rolling(window=length).mean()

def check_bb_sqz(data, length=20, mult=2.0, kc_mult=1.5):
    basis = calculate_sma(data, length)
    stdev = data['Close'].rolling(window=length).std()
    bb_upper, bb_lower = basis + (stdev * mult), basis - (stdev * mult)
    
    kc_basis = data['Close'].ewm(span=length, adjust=False).mean() 
    atr = calculate_atr(data, length)
    kc_upper, kc_lower = kc_basis + (atr * kc_mult), kc_basis - (atr * kc_mult)
    
    return (bb_upper < kc_upper) & (bb_lower > kc_lower)

def check_sma_sqz(data, len1=20, len2=100):
    """
    USER EDGE: Price, SMA 20, and SMA 100 are together.
    """
    sma1, sma2 = calculate_sma(data, len1), calculate_sma(data, len2)
    highest_ma, lowest_ma = np.maximum(sma1, sma2), np.minimum(sma1, sma2)
    return (data['High'] >= lowest_ma) & (data['Low'] <= highest_ma)

# --- 2. Liquidity Engine ---

def detect_liquidity_sweep(df, lookback=10):
    if len(df) < lookback + 1: return "No Data"
    recent_high = df['High'].iloc[-lookback:-1].max()
    recent_low = df['Low'].iloc[-lookback:-1].min()
    curr_high, curr_low, curr_close = df['High'].iloc[-1], df['Low'].iloc[-1], df['Close'].iloc[-1]
    
    if curr_low < recent_low and curr_close > recent_low: return "💧 Sweep Low"
    if curr_high > recent_high and curr_close < recent_high: return "💧 Sweep High"
    return "No Sweep"

# --- 3. Session Logic (WITH FAILOVER) ---

def get_session_performance():
    now = datetime.utcnow()
    if 0 <= now.hour < 8: session, start_hour = "ASIA", 0
    elif 8 <= now.hour < 13: session, start_hour = "LONDON", 8
    else: session, start_hour = "NEW YORK", 13
        
    # FIX: Use Robust Fetcher for BTC 1H Data to ensure we get a reading
    df, _ = get_crypto_data_robust('BTC/USDT:USDT', '1h')
    
    if df is not None and len(df) > 0:
        df_reset = df.reset_index()
        # Find the candle that opened at session start hour
        session_candles = df_reset[df_reset['Timestamp'].dt.hour == start_hour]
        
        if not session_candles.empty:
            session_open = session_candles['Open'].iloc[-1]
            current_price = df['Close'].iloc[-1]
            change_pct = ((current_price - session_open) / session_open) * 100
            return session, change_pct
    
    # Fallback if data fetch fails
    return session, 0.0

# --- Placeholder for BTC Regime ---
def get_btc_regime():
    # Logic not defined in prompt
    return "PENDING", "⚪"

# --- Data Fetching ---

def get_crypto_data_robust(symbol, timeframe):
    for ex_config in EXCHANGE_QUEUE:
        try:
            exchange_class = getattr(ccxt, ex_config['id'])
            options = {'enableRateLimit': True, 'options': {'defaultType': ex_config['type']}}
            if ex_config['id'] == 'binanceusdm': options['options'] = {'defaultType': 'future'}
            
            exchange = exchange_class(options)
            exchange.load_markets()
            
            bars = exchange.fetch_ohlcv(symbol, timeframe, limit=500)
            if bars and len(bars) > 0:
                df = pd.DataFrame(bars, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
                df.set_index('Timestamp', inplace=True)
                return df, ex_config['name']
        except Exception: continue
    return None, None

# --- Risk Score Logic ---
def calculate_risk_score(df):
    if df is None or len(df) < 20: return 0
    atr = calculate_atr(df, 20)
    atr_avg = atr.rolling(100).mean().iloc[-1]
    current_atr = atr.iloc[-1]
    if pd.isna(atr_avg) or atr_avg == 0: return 50
    ratio = (current_atr / atr_avg) * 100
    return int(min(max(ratio, 0), 100))

# --- Main Application ---

def main():
    st.set_page_config(page_title="Liquidity Engine & Scanner", layout="wide")
    
    # --- Sidebar ---
    with st.sidebar:
        st.title("⚙️ Settings")
        
        # Expanded Watchlist
        default_tickers = (
            "BTC/USDT:USDT, ETH/USDT:USDT, SOL/USDT:USDT, XRP/USDT:USDT, DOGE/USDT:USDT, PEPE/USDT:USDT, "
            "SPX/USDT:USDT, PUMP/USDT:USDT, FOGO/USDT:USDT, LINEA/USDT:USDT, "
            "SPACE/USDT:USDT, PEOPLE/USDT:USDT, WIF/USDT:USDT"
        )
        
        tickers_input = st.text_area("Enter Tickers", value=default_tickers, height=200)
        
        st.markdown("---")
        
        # --- HTML Dashboard Integration ---
        st.subheader("📊 BTC Dashboard")
        uploaded_file = st.file_uploader("Upload BTC Market Dashboard HTML", type="html")
        
        if st.button("🔄 Refresh Scanner"):
            st.rerun()

    # --- Main Layout ---
    # If user uploaded the dashboard, show it at the top
    if uploaded_file is not None:
        st.subheader("📊 BTC Market Dashboard")
        html_data = uploaded_file.read().decode("utf-8")
        components.html(html_data, height=400, scrolling=True)
        st.markdown("---")

    # --- Top Row: BTC Regime & Session ---
    col_regime, col_session, col_time = st.columns([2, 2, 1])
    
    regime_name, regime_icon = get_btc_regime()
    col_regime.metric("BTC Regime", f"{regime_icon} {regime_name}")
    
    # Session now uses Failover to prevent 0.00% error
    session_name, session_perf = get_session_performance()
    col_session.metric(f"Session: {session_name}", f"{session_perf:+.2f}%", "BTC Movement")
    
    col_time.metric("Time (UTC)", datetime.utcnow().strftime("%H:%M:%S"))

    st.markdown("---")

    tickers = [t.strip() for t in tickers_input.split(",") if t.strip()]
    results = []
    progress_bar = st.progress(0)

    for i, ticker in enumerate(tickers):
        progress_bar.progress((i + 1) / len(tickers), text=f"Analyzing {ticker}...")
        
        row_data = {"Ticker": ticker}
        
        # Analyze on 5m for Liquidity and Risk Score
        df_5m, source = get_crypto_data_robust(ticker, '5m') 
        
        if df_5m is not None and len(df_5m) > 100:
            row_data["Source"] = source
            
            # 1. SQZ Logic (3m, 5m, 15m)
            for tf_name, tf_val in TIMEFRAMES.items():
                if tf_val == '5m':
                    df_tf = df_5m
                else:
                    df_tf, _ = get_crypto_data_robust(ticker, tf_val)
                
                if df_tf is not None:
                    is_bb = check_bb_sqz(df_tf).iloc[-1]
                    is_sma = check_sma_sqz(df_tf).iloc[-1]
                    row_data[f"BB ({tf_name})"] = "✅" if is_bb else ""
                    row_data[f"SMA ({tf_name})"] = "✅" if is_sma else ""
            
            # 2. Liquidity Engine (5m)
            row_data["Liquidity"] = detect_liquidity_sweep(df_5m)
            
            # 3. Risk Score (0-100)
            row_data["Risk Score"] = calculate_risk_score(df_5m)
            
        else:
            row_data["Source"] = "FAILED"
            row_data["Liquidity"] = "Error"
            row_data["Risk Score"] = 0
            
        results.append(row_data)

    progress_bar.empty()

    # --- Display Results ---
    if results:
        df_results = pd.DataFrame(results)
        
        # Reorder Columns
        cols = ["Ticker", "Risk Score", "Source", "Liquidity", "BB (3m)", "SMA (3m)", "BB (5m)", "SMA (5m)", "BB (15m)", "SMA (15m)"]
        df_results = df_results.reindex(columns=cols)
        
        def color_cells(val):
            if "✅" in str(val) or "💧" in str(val): return 'background-color: #d4edda; color: black'
            if "FAILED" in str(val): return 'background-color: #f8d7da; color: black'
            return ''
            
        st.dataframe(df_results.style.map(color_cells), use_container_width=True)
        st.caption("Risk Score: 0-100 based on Volatility. Liquidity Codes: 💧 Sweep (Stop Hunt).")

if __name__ == "__main__":
    main()
