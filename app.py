import streamlit as st
import pandas as pd
import numpy as np
import ccxt
from datetime import datetime

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

def calculate_atr(data, length=14):
    high, low, close = data['High'], data['Low'], data['Close'].shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - close), abs(low - close)))
    return tr.rolling(window=length).mean()

def get_kc(data, length=20, mult=1.5):
    basis = data['Close'].ewm(span=length, adjust=False).mean()
    atr = calculate_atr(data, length)
    upper = basis + (atr * mult)
    lower = basis - (atr * mult)
    return upper, lower

def get_bb(data, length=20, mult=2.0):
    basis = calculate_sma(data, length)
    stdev = data['Close'].rolling(window=length).std()
    upper = basis + (stdev * mult)
    lower = basis - (stdev * mult)
    return upper, lower

def analyze_candle(df):
    o = df['Open'].iloc[-1]
    h = df['High'].iloc[-1]
    l = df['Low'].iloc[-1]
    c = df['Close'].iloc[-1]
    
    body = abs(c - o)
    atr = calculate_atr(df).iloc[-1]
    if pd.isna(atr) or atr == 0: atr = (h - l) if (h-l) > 0 else 0.0001
    
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    
    is_elephant = body >= atr
    is_upright_tail = lower_wick >= atr
    is_inverted_tail = upper_wick >= atr
    
    return {
        "is_elephant": is_elephant,
        "is_upright_tail": is_upright_tail,
        "is_inverted_tail": is_inverted_tail
    }

def check_system_one(df):
    bb_u, bb_l = get_bb(df)
    kc_u, kc_l = get_kc(df)
    
    is_sqz = (bb_u < kc_u) & (bb_l > kc_l)
    was_sqz = is_sqz.iloc[-2]
    
    curr_close = df['Close'].iloc[-1]
    
    potential_bull = was_sqz and (curr_close > kc_u.iloc[-1])
    potential_bear = was_sqz and (curr_close < kc_l.iloc[-1])
    
    candle = analyze_candle(df)
    
    bull_confirmed = potential_bull and (candle['is_elephant'] or candle['is_upright_tail'])
    bear_confirmed = potential_bear and (candle['is_elephant'] or candle['is_inverted_tail'])
    
    return {
        "squeeze": is_sqz.iloc[-1],
        "bull_break": bull_confirmed,
        "bear_break": bear_confirmed
    }

def check_system_two(df):
    sma20 = calculate_sma(df, 20)
    sma100 = calculate_sma(df, 100)
    
    highest_ma = np.maximum(sma20, sma100)
    lowest_ma = np.minimum(sma20, sma100)
    
    # FIX: User correction - Threshold is 0.1% (0.001), not 1%.
    price = df['Close'].iloc[-1]
    ma_gap = highest_ma.iloc[-1] - lowest_ma.iloc[-1]
    threshold = price * 0.001 # 0.1% threshold
    
    are_mas_together = ma_gap < threshold
    
    # Check if Price intersects the MA zone
    price_intersects = (df['High'].iloc[-1] >= lowest_ma.iloc[-1]) and (df['Low'].iloc[-1] <= highest_ma.iloc[-1])
    
    # Final Logic: MAs must be tight AND Price must touch them
    is_together = are_mas_together and price_intersects
    
    # Previous state check 
    was_together = False
    if len(df) > 2:
        prev_highest = np.maximum(sma20.iloc[-2], sma100.iloc[-2])
        prev_lowest = np.minimum(sma20.iloc[-2], sma100.iloc[-2])
        prev_gap = prev_highest - prev_lowest
        prev_threshold = df['Close'].iloc[-2] * 0.001 # 0.1% for previous candle
        was_together = (prev_gap < prev_threshold) and (df['High'].iloc[-2] >= prev_lowest) and (df['Low'].iloc[-2] <= prev_highest)

    curr_close = df['Close'].iloc[-1]
    
    potential_bull = was_together and (curr_close > highest_ma.iloc[-1])
    potential_bear = was_together and (curr_close < lowest_ma.iloc[-1])
    
    candle = analyze_candle(df)
    
    bull_confirmed = potential_bull and (candle['is_elephant'] or candle['is_upright_tail'])
    bear_confirmed = potential_bear and (candle['is_elephant'] or candle['is_inverted_tail'])
    
    return {
        "together": is_together,
        "bull_break": bull_confirmed,
        "bear_break": bear_confirmed
    }

def analyze_status(sys1, sys2):
    if sys1['bull_break'] and sys2['bull_break']: return "🚀 MEGA BREAKOUT UP"
    if sys1['bear_break'] and sys2['bear_break']: return "💥 MEGA BREAKOUT DN"
    
    if sys1['bull_break']: return "⬆️ BB Breakout"
    if sys1['bear_break']: return "⬇️ BB Breakout"
    if sys2['bull_break']: return "⬆️ SMA Breakout"
    if sys2['bear_break']: return "⬇️ SMA Breakout"
    
    if sys1['squeeze'] and sys2['together']: return "🔒 DOUBLE SQZ"
    if sys1['squeeze']: return "BB SQZ"
    if sys2['together']: return "SMA TOGETHER"
    
    return ""

# --- 2. Liquidity Engine ---

def detect_liquidity_sweep(df, lookback=10):
    if len(df) < lookback + 1: return "No Data"
    recent_high = df['High'].iloc[-lookback:-1].max()
    recent_low = df['Low'].iloc[-lookback:-1].min()
    curr_high, curr_low, curr_close = df['High'].iloc[-1], df['Low'].iloc[-1], df['Close'].iloc[-1]
    
    if curr_low < recent_low and curr_close > recent_low: return "💧 Sweep Low"
    if curr_high > recent_high and curr_close < recent_high: return "💧 Sweep High"
    return "No Sweep"

# --- 3. BTC Regime & Session ---

def get_btc_regime():
    df, _ = get_crypto_data_robust('BTC/USDT:USDT', '1d')
    
    if df is not None and len(df) > 100:
        sma20 = calculate_sma(df, 20).iloc[-1]
        sma100 = calculate_sma(df, 100).iloc[-1]
        price = df['Close'].iloc[-1]
        
        if price > sma20 > sma100: return "BULL TREND", "🟢"
        if price < sma20 < sma100: return "BEAR TREND", "🔴"
        return "RANGE", "🟡"
    return "OFFLINE", "⚪"

def get_session_performance():
    now = datetime.utcnow()
    if 0 <= now.hour < 8: session, start_hour = "ASIA", 0
    elif 8 <= now.hour < 13: session, start_hour = "LONDON", 8
    else: session, start_hour = "NEW YORK", 13
        
    df, _ = get_crypto_data_robust('BTC/USDT:USDT', '1h')
    
    if df is not None and len(df) > 0:
        df['Timestamp'] = df.index
        try:
            session_open = df[df['Timestamp'].dt.hour == start_hour]['Open'].iloc[-1]
            current_price = df['Close'].iloc[-1]
            change_pct = ((current_price - session_open) / session_open) * 100
            return session, change_pct
        except:
            return session, 0.0
    return session, 0.0

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
    
    with st.sidebar:
        st.title("⚙️ Settings")
        default_tickers = "BTC/USDT:USDT, ETH/USDT:USDT, SOL/USDT:USDT, XRP/USDT:USDT, DOGE/USDT:USDT, PEPE/USDT:USDT"
        tickers_input = st.text_area("Enter Tickers", value=default_tickers, height=150)
        
        st.markdown("---")
        st.subheader("🔗 Dashboard Redirect")
        dashboard_url = st.text_input("Dashboard URL", value="https://www.tradingview.com/chart/")
        if st.button("🚀 Open Dashboard"):
            st.markdown(f'<meta http-equiv="refresh" content="0; url={dashboard_url}">', unsafe_allow_html=True)

    # --- Top Row ---
    col_regime, col_session, col_time = st.columns([2, 2, 1])
    
    regime_name, regime_icon = get_btc_regime()
    btc_link = "https://www.tradingview.com/chart/?symbol=BTCUSDT.P"
    col_regime.markdown(f"### [{regime_icon} {regime_name}]({btc_link})")
    col_regime.caption("BTC Regime (Daily)")
    
    session_name, session_perf = get_session_performance()
    col_session.metric(f"Session: {session_name}", f"{session_perf:+.2f}%")
    
    col_time.metric("Time (UTC)", datetime.utcnow().strftime("%H:%M:%S"))

    st.markdown("---")

    if st.button("🔄 Refresh Scanner"):
        st.rerun()

    tickers = [t.strip() for t in tickers_input.split(",") if t.strip()]
    results = []
    progress_bar = st.progress(0)

    for i, ticker in enumerate(tickers):
        progress_bar.progress((i + 1) / len(tickers), text=f"Scanning {ticker}...")
        
        row_data = {"Ticker": ticker}
        
        df_5m, source = get_crypto_data_robust(ticker, '5m') 
        
        if df_5m is not None and len(df_5m) > 100:
            row_data["Source"] = source
            
            sys1_5m = check_system_one(df_5m)
            sys2_5m = check_system_two(df_5m)
            row_data["Status (5m)"] = analyze_status(sys1_5m, sys2_5m)
            
            for tf_name, tf_val in TIMEFRAMES.items():
                if tf_val == '5m': continue 
                
                df_tf, _ = get_crypto_data_robust(ticker, tf_val)
                if df_tf is not None:
                    sys1_tf = check_system_one(df_tf)
                    sys2_tf = check_system_two(df_tf)
                    row_data[f"Status ({tf_name})"] = analyze_status(sys1_tf, sys2_tf)
                else:
                    row_data[f"Status ({tf_name})"] = "Error"
            
            row_data["Liquidity"] = detect_liquidity_sweep(df_5m)
            row_data["Risk Score"] = calculate_risk_score(df_5m)
            
        else:
            row_data["Source"] = "FAILED"
            row_data["Status (5m)"] = "Error"
            
        results.append(row_data)

    progress_bar.empty()

    # --- Display Results ---
    if results:
        df_results = pd.DataFrame(results)
        
        cols = ["Ticker", "Risk Score", "Status (5m)", "Status (3m)", "Status (15m)", "Source", "Liquidity"]
        df_results = df_results.reindex(columns=cols)
        
        def color_cells(val):
            val_str = str(val)
            if "MEGA BREAKOUT" in val_str: return 'background-color: #ff69b4; color: white; font-weight: bold' 
            if "DOUBLE SQZ" in val_str: return 'background-color: #800080; color: white; font-weight: bold' 
            if "BREAKOUT" in val_str: return 'background-color: #fff3cd; color: black; font-weight: bold' 
            if "SQZ" in val_str or "TOGETHER" in val_str: return 'background-color: #d4edda; color: black' 
            if "FAILED" in val_str or "Error" in val_str: return 'background-color: #f8d7da; color: black'
            return ''
            
        st.dataframe(df_results.style.map(color_cells), use_container_width=True)
        st.caption("Logic: SMA SQZ requires MAs to be within 0.1% of Price.")

if __name__ == "__main__":
    main()
