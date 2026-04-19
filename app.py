import streamlit as st
import pandas as pd
import numpy as np
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ==========================================
# 1. CONFIGURATION & CONSTANTS
# ==========================================

# --- SMA Nearness Engine (User's Logic) ---
# Distance between SMA 20 and SMA 100 as % of Price
SMA_THRESHOLD_ULTRA = 0.05   # 0.05% distance (Very Tight)
SMA_THRESHOLD_TIGHT = 0.10   # 0.10% distance
SMA_THRESHOLD_NORMAL = 0.20  # 0.20% distance

# --- BB/KC Squeeze Engine (Original Logic) ---
# Bollinger Band Width % (Relative to Price)
BB_WIDTH_ULTRA = 0.08
BB_WIDTH_TIGHT = 0.10
BB_WIDTH_NORMAL = 0.15

# General Settings
CHOP_CLEAN = 40
VOLUME_NORMAL = 1.2
VOLUME_HIGH = 1.5
VOLUME_KILLER = 2.0

# Indicator Periods
SMA_FAST = 20
SMA_SLOW = 100
BB_PERIOD = 20
KC_PERIOD = 20
RSI_PERIOD = 14

BASE_URL = "https://www.okx.com"
ENDPOINT = "/api/v5/market/candles"

DEFAULT_PAIRS = "BTC-USDT,ETH-USDT,SOL-USDT,DOGE-USDT,PEPE-USDT,WIF-USDT,AVAX-USDT,LINK-USDT,UNI-USDT,AAVE-USDT,XRP-USDT,ADA-USDT,TRX-USDT,SUI-USDT,TON-USDT,NEAR-USDT,APT-USDT,ARB-USDT,OP-USDT,MATIC-USDT,INJ-USDT,LTC-USDT,FET-USDT,RNDR-USDT,TIA-USDT,STX-USDT,IMX-USDT,HBAR-USDT,VET-USDT,ATOM-USDT,ETC-USDT,FIL-USDT,LDO-USDT,AR-USDT,THETA-USDT,ALGO-USDT,QNT-USDT,FTM-USDT,ENS-USDT,AKT-USDT,CFX-USDT,STG-USDT,AGIX-USDT,GMX-USDT,BLUR-USDT,GRT-USDT,SAND-USDT,MANA-USDT,AXS-USDT,EGLD-USDT,ICP-USDT,RUNE-USDT,SNX-USDT"

# ==========================================
# 2. PURE PANDAS INDICATOR CALCULATIONS
# ==========================================
# (No external dependencies to ensure easy deployment)

def calculate_sma(series, window):
    return series.rolling(window=window).mean()

def calculate_ema(series, window):
    return series.ewm(span=window, adjust=False).mean()

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close'].shift(1)
    tr = pd.concat([high - low, (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def calculate_indicators(df):
    if df is None or len(df) < SMA_SLOW:
        return None

    # 1. SMA Nearness (User Logic)
    df['sma_20'] = calculate_sma(df['close'], SMA_FAST)
    df['sma_100'] = calculate_sma(df['close'], SMA_SLOW)
    
    # 2. BB/KC Squeeze (Original Logic)
    # Bollinger Bands
    df['bb_mid'] = calculate_sma(df['close'], BB_PERIOD)
    df['bb_std'] = df['close'].rolling(window=BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']
    
    # Keltner Channels
    df['kc_atr'] = calculate_atr(df, KC_PERIOD)
    df['kc_mid'] = calculate_ema(df['close'], KC_PERIOD)
    df['kc_upper'] = df['kc_mid'] + 1.5 * df['kc_atr']
    df['kc_lower'] = df['kc_mid'] - 1.5 * df['kc_atr']
    
    # 3. Other Indicators
    df['rsi'] = calculate_rsi(df['close'], RSI_PERIOD)
    df['vol_sma'] = calculate_sma(df['volume'], 20)
    df['vol_ratio'] = df['volume'] / df['vol_sma']
    
    # Chop (simplified calculation for pure pandas)
    # Chop = 100 * LOG10(SUM(ATR(1), n) / (MaxHi - MinLo)) / LOG10(n)
    # Using a simplified ATR-based volatility measure for chop logic
    df['chop'] = df['kc_atr'] / df['close'] * 100 * 10 # Scaled for UI
    
    return df

# ==========================================
# 3. DATA FETCHING
# ==========================================

def fetch_okx_data(symbol, timeframe, limit=300):
    tf_map = {'3m': '3m', '5m': '5m', '15m': '15m', '1H': '1H', '4H': '4H'}
    bar = tf_map.get(timeframe, '5m')
    params = {'instId': symbol, 'bar': bar, 'limit': str(limit)}
    url = f"{BASE_URL}{ENDPOINT}"
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()['data']
        if not data: return None
        df = pd.DataFrame(data, columns=['ts', 'o', 'h', 'l', 'c', 'vol', 'volCcy', 'volCcyQuote', 'confirm'])
        df = df[['ts', 'o', 'h', 'l', 'c', 'vol']].astype(float)
        df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception:
        return None

def get_btc_regime():
    timeframes = ['15m', '1H', '4H']
    regimes = {}
    for tf in timeframes:
        df = fetch_okx_data('BTC-USDT', tf, limit=300)
        if df is not None:
            df['sma_20'] = calculate_sma(df['close'], 20)
            df['sma_100'] = calculate_sma(df['close'], 100)
            last = df.iloc[-1]
            bias = "NEUTRAL"
            if last['sma_20'] > last['sma_100']: bias = "BULLISH"
            else: bias = "BEARISH"
            regimes[tf] = {'bias': bias}
        else:
            regimes[tf] = {'bias': 'ERROR'}
    return regimes

# ==========================================
# 4. DUAL ENGINE LOGIC
# ==========================================

def detect_engines(df):
    last = df.iloc[-1]
    
    # --- Engine 1: SMA Nearness ---
    # Distance between SMA 20 and SMA 100
    sma_dist_pct = abs(last['sma_20'] - last['sma_100']) / last['close'] * 100
    sma_sqz_on = sma_dist_pct < SMA_THRESHOLD_NORMAL
    
    # --- Engine 2: BB/KC Squeeze ---
    # Standard Bollinger inside Keltner
    bb_sqz_on = (last['bb_lower'] > last['kc_lower']) and (last['bb_upper'] < last['kc_upper'])
    
    # Calculate BB Width for scoring
    bb_width = (last['bb_upper'] - last['bb_lower']) / last['bb_mid'] * 100
    
    # --- Combined State ---
    # MEGA SQZ = Both Engines True
    # SMA SQZ = Only SMA True
    # BB SQZ = Only BB True
    
    state = "NEUTRAL"
    if sma_sqz_on and bb_sqz_on:
        state = "MEGA SQZ"
    elif sma_sqz_on:
        state = "SMA SQZ"
    elif bb_sqz_on:
        state = "BB SQZ"
    elif last['close'] > last['bb_upper']:
        state = "EXPANSION"
        
    # Duration (Based on SMA Nearness as it's your primary)
    duration = 0
    for i in range(len(df)-1, -1, -1):
        row = df.iloc[i]
        if pd.notna(row['sma_20']) and pd.notna(row['sma_100']):
            dist = abs(row['sma_20'] - row['sma_100']) / row['close'] * 100
            if dist < SMA_THRESHOLD_NORMAL:
                duration += 1
            else:
                break

    return state, sma_dist_pct, bb_width, duration

def calculate_score(df, btc_regimes, state, sma_dist, bb_width):
    last = df.iloc[-1]
    score = 0
    details = {}
    
    # 1. Base Quality Score (Hybrid)
    # If MEGA SQZ, maximum points. Otherwise, average the scores.
    
    # SMA Score (Max 15)
    sma_pts = 0
    if sma_dist <= SMA_THRESHOLD_ULTRA: sma_pts = 15
    elif sma_dist <= SMA_THRESHOLD_TIGHT: sma_pts = 12
    elif sma_dist <= SMA_THRESHOLD_NORMAL: sma_pts = 8
    
    # BB Score (Max 15)
    bb_pts = 0
    if bb_width <= BB_WIDTH_ULTRA: bb_pts = 15
    elif bb_width <= BB_WIDTH_TIGHT: bb_pts = 12
    elif bb_width <= BB_WIDTH_NORMAL: bb_pts = 8
    
    # Bonus for Confluence
    if state == "MEGA SQZ":
        score += 35 # Max points + 5 bonus
        details['quality'] = "MEGA CONFLUENCE"
    else:
        score += (sma_pts + bb_pts)
        details['quality'] = f"SMA:{sma_pts} | BB:{bb_pts}"

    # 2. Chop Score (Max 20)
    # Lower is better
    chop = last['chop']
    if chop < 2.0: score += 20      # Clean
    elif chop < 3.0: score += 15    # Moderate
    elif chop < 4.0: score += 5     # Choppy

    # 3. BTC Alignment (Max 20)
    local_trend = "BULL" if last['close'] > last['sma_20'] else "BEAR"
    btc_score = 0
    if '15m' in btc_regimes and btc_regimes['15m']['bias'] != 'ERROR':
        if btc_regimes['15m']['bias'].find(local_trend[:4].upper()) != -1: btc_score += 10
    if '1H' in btc_regimes and btc_regimes['1H']['bias'] != 'ERROR':
        if btc_regimes['1H']['bias'].find(local_trend[:4].upper()) != -1: btc_score += 5
    if '4H' in btc_regimes and btc_regimes['4H']['bias'] != 'ERROR':
        if btc_regimes['4H']['bias'].find(local_trend[:4].upper()) != -1: btc_score += 5
    score += btc_score
    details['btc_alignment'] = f"{btc_score}/20"

    # 4. Volume (Max 15)
    vol_ratio = last['vol_ratio']
    if vol_ratio >= VOLUME_KILLER: score += 15
    elif vol_ratio >= VOLUME_HIGH: score += 10
    elif vol_ratio >= VOLUME_NORMAL: score += 5

    # 5. Breakout (Max 10)
    avg_body = (df['open'] - df['close']).abs().mean()
    curr_body = abs(last['close'] - last['open'])
    if curr_body > avg_body * 1.5:
        score += 10
        details['breakout'] = "ELEPHANT"
    else:
        details['breakout'] = "STD"

    # Tier Assignment
    tier = "❌ RISKY"
    if score >= 90: tier = "🐘 KILLER ⭐"
    elif score >= 80: tier = "⭐ HIGH"
    elif score >= 70: tier = "👀 WATCH"
    elif score >= 60: tier = "⚠️ MEDIUM"
    elif score >= 50: tier = "🚫 LOW"
    
    return score, tier, details

def analyze_pair(symbol, timeframe, btc_regimes):
    df = fetch_okx_data(symbol, timeframe)
    if df is None: return None
    df = calculate_indicators(df)
    if df is None: return None
    
    state, sma_dist, bb_width, duration = detect_engines(df)
    score, tier, details = calculate_score(df, btc_regimes, state, sma_dist, bb_width)
    
    last = df.iloc[-1]
    setup_type = None
    rsi = last['rsi']
    
    # Setup Logic
    if state == "MEGA SQZ" and duration > 5: setup_type = "💥 MEGA SETUP"
    elif state == "SMA SQZ" and duration > 5: setup_type = "📉 SMA CONFLUENCE"
    elif state == "BB SQZ": setup_type = "⚡ VOLATILITY BUILD"

    return {
        'symbol': symbol, 'timeframe': timeframe, 'price': last['close'],
        'state': state, 'sma_dist': sma_dist, 'bb_width': bb_width,
        'score': score, 'tier': tier, 'details': details, 
        'rsi': rsi, 'chop': last['chop'], 'vol_ratio': last['vol_ratio'],
        'duration': duration, 'setup': setup_type
    }

@st.cache_data(ttl=600)
def calculate_session_performance(symbol):
    df = fetch_okx_data(symbol, '1H', limit=720) 
    if df is None: return None
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['hour'] = df['datetime'].dt.hour
    df['return'] = df['close'].pct_change() * 100
    sessions = {'Asian': (0, 8), 'London': (8, 13), 'NY': (13, 20), 'AfterHours': (20, 24)}
    stats = {}
    for name, (start, end) in sessions.items():
        s_df = df[(df['hour'] >= start) & (df['hour'] < end)]
        stats[name] = {'total_return': round(s_df['return'].sum(), 2)}
    return stats

# ==========================================
# 5. UI
# ==========================================

def main():
    st.set_page_config(page_title="Dual Engine Scanner", layout="wide")
    st.markdown("""
    <style>
    .card { border-radius: 10px; padding: 15px; margin-bottom: 10px; background-color: #1E1E1E; color: white; }
    .killer { border-left: 5px solid #00FF00; }
    .high { border-left: 5px solid #00BFFF; }
    .watch { border-left: 5px solid #FFD700; }
    .medium { border-left: 5px solid #FFA500; }
    .low { border-left: 5px solid #FF6347; }
    .risk { border-left: 5px solid #FF0000; }
    </style>
    """, unsafe_allow_html=True)

    st.title("🚀 Dual Engine Scanner (SMA + BB/KC)")
    
    with st.sidebar:
        st.header("Settings")
        pairs_input = st.text_area("Watchlist", DEFAULT_PAIRS, height=150)
        pairs = [p.strip() for p in pairs_input.split(',') if p.strip()]
        timeframes = st.multiselect("Timeframes", ['3m', '5m', '15m'], default=['5m', '15m'])
        scan_btn = st.button("🔍 Scan Market")
        auto_refresh = st.checkbox("Auto Refresh (60s)", False)
        st.header("BTC Regime")
        regime_placeholder = st.empty()

    session_placeholder = st.empty()
    results_placeholder = st.empty()
    
    while True:
        if not scan_btn and not auto_refresh:
            time.sleep(1)
            continue
            
        start_time = time.time()
        with st.spinner("Scanning..."):
            btc_regimes = get_btc_regime()
        
        with regime_placeholder.container():
            st.markdown("### Multi-TF Bias")
            for tf, data in btc_regimes.items():
                emoji = "📈" if "BULL" in data['bias'] else "📉" if "BEAR" in data['bias'] else "➡️"
                st.metric(f"BTC {tf}", f"{emoji} {data['bias']}")
        
        all_results = []
        progress_bar = st.progress(0)
        tasks = []
        for symbol in pairs:
            for tf in timeframes:
                tasks.append((symbol, tf, btc_regimes))
                
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for task in tasks:
                futures.append(executor.submit(analyze_pair, *task))
                time.sleep(0.1) 
            total = len(futures)
            for i, future in enumerate(as_completed(futures)):
                res = future.result()
                if res: all_results.append(res)
                progress_bar.progress((i + 1) / total)
        
        progress_bar.empty()
        all_results.sort(key=lambda x: (x['state'] == 'MEGA SQZ', -x['score']))
        
        with session_placeholder.container():
            st.markdown("### 📊 Session Performance")
            cols = st.columns(4)
            stats = calculate_session_performance('BTC-USDT')
            if stats:
                cols[0].metric("Asian", f"{stats['Asian']['total_return']}%")
                cols[1].metric("London", f"{stats['London']['total_return']}%")
                cols[2].metric("NY", f"{stats['NY']['total_return']}%")
                cols[3].metric("After Hours", f"{stats['AfterHours']['total_return']}%")

        with results_placeholder.container():
            st.markdown(f"### 📡 Signals ({len(all_results)} found)")
            # Filter for interesting states
            filtered = [r for r in all_results if "SQZ" in r['state'] or r['state'] == "EXPANSION"]
            grid = st.columns(3) 
            
            for idx, res in enumerate(filtered):
                col = grid[idx % 3]
                css_class = "risk"
                if res['score'] >= 90: css_class = "killer"
                elif res['score'] >= 80: css_class = "high"
                elif res['score'] >= 70: css_class = "watch"
                elif res['score'] >= 60: css_class = "medium"

                html = f"""
                <div class="card {css_class}">
                    <h4>{res['symbol']} <small>({res['timeframe']})</small></h4>
                    <h3 style="color:white;">{res['tier']}</h3>
                    <p><b>State:</b> {res['state']}</p>
                    <p><b>SMA Dist:</b> {res['sma_dist']:.4f}% | <b>BB Width:</b> {res['bb_width']:.4f}%</p>
                    <p><b>Score:</b> {res['score']}/100</p>
                    <p><b>Setup:</b> {res['setup'] if res['setup'] else 'Standard'}</p>
                    <hr>
                    <small>Align: {res['details']['btc_alignment']}</small>
                </div>
                """
                col.markdown(html, unsafe_allow_html=True)
        
        if not auto_refresh: break
        else:
            elapsed = time.time() - start_time
            time.sleep(max(0, 60 - elapsed))

if __name__ == "__main__":
    main()
