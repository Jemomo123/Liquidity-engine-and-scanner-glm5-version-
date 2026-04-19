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

# --- SMA Nearness Engine ---
SMA_THRESHOLD_ULTRA = 0.05
SMA_THRESHOLD_TIGHT = 0.10
SMA_THRESHOLD_NORMAL = 0.20

# --- BB/KC Squeeze Engine ---
BB_WIDTH_ULTRA = 0.08
BB_WIDTH_TIGHT = 0.10
BB_WIDTH_NORMAL = 0.15

# --- Liquidity Engine ---
SWEEP_LOOKBACK = 20
SWEEP_THRESHOLD = 0.002  # 0.2% wick

# General Settings
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

    df['sma_20'] = calculate_sma(df['close'], SMA_FAST)
    df['sma_100'] = calculate_sma(df['close'], SMA_SLOW)
    
    df['bb_mid'] = calculate_sma(df['close'], BB_PERIOD)
    df['bb_std'] = df['close'].rolling(window=BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']
    
    df['kc_atr'] = calculate_atr(df, KC_PERIOD)
    df['kc_mid'] = calculate_ema(df['close'], KC_PERIOD)
    df['kc_upper'] = df['kc_mid'] + 1.5 * df['kc_atr']
    df['kc_lower'] = df['kc_mid'] - 1.5 * df['kc_atr']
    
    df['rsi'] = calculate_rsi(df['close'], RSI_PERIOD)
    df['vol_sma'] = calculate_sma(df['volume'], 20)
    df['vol_ratio'] = df['volume'] / df['vol_sma']
    
    # Volatility for scoring
    df['volatility'] = df['kc_atr'] / df['close'] * 100
    
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
# 4. NEW SCORING COMPONENTS
# ==========================================

def get_mtf_alignment(symbol):
    """Checks alignment across 3m, 5m, 15m"""
    tfs = ['3m', '5m', '15m']
    trends = []
    
    # Simple synchronous fetch for now (parallelizing this requires changing executor logic)
    for tf in tfs:
        df = fetch_okx_data(symbol, tf, limit=200)
        if df is not None:
            df['sma_20'] = calculate_sma(df['close'], 20)
            df['sma_100'] = calculate_sma(df['close'], 100)
            last = df.iloc[-1]
            if last['sma_20'] > last['sma_100']: trends.append("BULL")
            else: trends.append("BEAR")
        else:
            return 0 # Error fetching one TF
            
    if trends.count("BULL") == 3 or trends.count("BEAR") == 3:
        return 15 # Perfect alignment
    elif trends.count("BULL") >= 2 or trends.count("BEAR") >= 2:
        return 8  # Majority alignment
    return 0

def detect_liquidity_sweep(df):
    """Detects BSL and SSL sweeps."""
    if len(df) < SWEEP_LOOKBACK + 1: return None, False, False
    
    window = df.iloc[-(SWEEP_LOOKBACK+1):-1]
    curr = df.iloc[-1]
    
    recent_high = window['high'].max()
    recent_low = window['low'].min()
    
    is_ssl = False
    is_bsl = False
    sweep_type = "None"
    
    # SSL (Bull Sweep) - Wick down, reversal up
    if curr['low'] < recent_low * (1 - SWEEP_THRESHOLD) and curr['close'] > curr['open']:
        sweep_type = "💧 SSL SWEEP (BULL)"
        is_ssl = True
        
    # BSL (Bear Sweep) - Wick up, reversal down
    if curr['high'] > recent_high * (1 + SWEEP_THRESHOLD) and curr['close'] < curr['open']:
        sweep_type = "💧 BSL SWEEP (BEAR)"
        is_bsl = True
        
    return sweep_type, is_ssl, is_bsl

def detect_candle_patterns(df):
    """Detects Elephant Bar and Tail Bar."""
    last = df.iloc[-1]
    body = abs(last['close'] - last['open'])
    avg_body = (df['open'] - df['close']).abs().mean()
    
    is_elephant = body > avg_body * 1.5
    
    # Tail Bar (Pinbar) - Long wick opposite to close
    upper_wick = last['high'] - max(last['open'], last['close'])
    lower_wick = min(last['open'], last['close']) - last['low']
    
    is_tail = False
    # Bullish Tail (Long lower wick)
    if lower_wick > body * 1.5 and last['close'] > last['open']:
        is_tail = True
    # Bearish Tail (Long upper wick)
    if upper_wick > body * 1.5 and last['close'] < last['open']:
        is_tail = True
        
    return is_elephant, is_tail

def calculate_sma_respect(df):
    """
    Checks if price is respecting the SMA 20 or 100.
    Simple logic: If price is above SMA 20, are recent lows bouncing off it?
    Returns a score 0-10.
    """
    last = df.iloc[-1]
    # Check last 5 candles
    recent = df.iloc[-5:]
    
    if last['close'] > last['sma_20']:
        # Bull Trend - Check if lows are near SMA 20
        dist = (recent['low'] - recent['sma_20']).min()
        # If the closest touch was within 0.5%, it's respected
        if abs(dist / last['close']) < 0.005:
            return 10
    else:
        # Bear Trend - Check if highs are near SMA 20
        dist = (recent['high'] - recent['sma_20']).max()
        if abs(dist / last['close']) < 0.005:
            return 10
            
    return 5 # Standard

# ==========================================
# 5. MAIN LOGIC
# ==========================================

def analyze_pair(symbol, timeframe, btc_regimes):
    # 1. Fetch Data
    df = fetch_okx_data(symbol, timeframe)
    if df is None: return None
    df = calculate_indicators(df)
    if df is None: return None
    
    last = df.iloc[-1]
    
    # 2. Engine 1: Squeeze
    sma_dist = abs(last['sma_20'] - last['sma_100']) / last['close'] * 100
    sma_sqz = sma_dist < SMA_THRESHOLD_NORMAL
    
    bb_width = (last['bb_upper'] - last['bb_lower']) / last['bb_mid'] * 100
    bb_sqz = (last['bb_lower'] > last['kc_lower']) and (last['bb_upper'] < last['kc_upper'])
    
    # 3. Engine 2: Liquidity
    sweep_type, is_ssl, is_bsl = detect_liquidity_sweep(df)
    
    # 4. Engine 3: Patterns
    is_elephant, is_tail = detect_candle_patterns(df)
    
    # 5. Scoring
    score = 0
    details = {}
    
    # A. Base Squeeze Score (0-30)
    base_score = 0
    if sma_sqz and bb_sqz: base_score = 30; details['sqz'] = "MEGA SQZ"
    elif sma_sqz: base_score = 20; details['sqz'] = "SMA SQZ"
    elif bb_sqz: base_score = 15; details['sqz'] = "BB SQZ"
    else: details['sqz'] = "None"
    score += base_score
    
    # B. MTF Alignment (0-15)
    mtf_score = get_mtf_alignment(symbol)
    score += mtf_score
    details['mtf_align'] = f"{mtf_score}/15"
    
    # C. Liquidity Sweep (0-15)
    if is_ssl or is_bsl:
        score += 15
        details['liq'] = "✅ SWEPT"
    else:
        details['liq'] = "No Sweep"
        
    # D. Candle Confirmation (0-10)
    candle_score = 0
    if is_elephant: candle_score += 5; details['candle'] = "ELEPHANT"
    if is_tail: candle_score += 5; details['candle'] = "TAIL BAR"
    if candle_score == 0: details['candle'] = "Normal"
    score += candle_score
    
    # E. SMA Respect (0-10)
    respect_score = calculate_sma_respect(df)
    score += respect_score
    details['respect'] = f"{respect_score}/10"
    
    # F. Volatility (ATR based) (0-10)
    # High volatility in squeeze is good energy
    vol = last['volatility']
    if vol > 0.5: score += 10
    elif vol > 0.3: score += 5
    
    # G. BTC Alignment (0-10) - Keeping BTC context
    local_trend = "BULL" if last['close'] > last['sma_20'] else "BEAR"
    btc_score = 0
    if '15m' in btc_regimes and btc_regimes['15m']['bias'] != 'ERROR':
        if btc_regimes['15m']['bias'].find(local_trend[:4].upper()) != -1: btc_score += 10
    score += btc_score
    details['btc_align'] = f"{btc_score}/10"
    
    # Determine State
    state = "NEUTRAL"
    if is_ssl or is_bsl: state = sweep_type
    elif sma_sqz and bb_sqz: state = "MEGA SQZ"
    elif sma_sqz: state = "SMA SQZ"
    elif bb_sqz: state = "BB SQZ"
    
    # Tier
    tier = "❌ RISKY"
    if score >= 90: tier = "🐘 KILLER ⭐"
    elif score >= 80: tier = "⭐ HIGH"
    elif score >= 70: tier = "👀 WATCH"
    elif score >= 60: tier = "⚠️ MEDIUM"
    
    return {
        'symbol': symbol, 'timeframe': timeframe, 'price': last['close'],
        'state': state, 'score': score, 'tier': tier, 'details': details, 
        'rsi': last['rsi'], 'vol_ratio': last['vol_ratio']
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
# 6. UI
# ==========================================

def run_scan(pairs, timeframes, btc_regimes, results_placeholder, session_placeholder, min_score):
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
    all_results.sort(key=lambda x: ("SWEEP" in x['state'], -x['score']))
    
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
        filtered = [r for r in all_results if r['score'] >= min_score]
        st.markdown(f"### 📡 Signals ({len(filtered)} found)")
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
                <h3 style="color:white;">{res['tier']} ({res['score']})</h3>
                <p><b>State:</b> {res['state']}</p>
                <p><b>Liquidity:</b> {res['details']['liq']}</p>
                <hr>
                <p><b>Squeeze:</b> {res['details']['sqz']}</p>
                <p><b>MTF Align:</b> {res['details']['mtf_align']} | <b>BTC:</b> {res['details']['btc_align']}</p>
                <p><b>Candle:</b> {res['details']['candle']} | <b>Respect:</b> {res['details']['respect']}</p>
            </div>
            """
            col.markdown(html, unsafe_allow_html=True)

def main():
    st.set_page_config(page_title="Advanced Liquidity Scanner", layout="wide")
    st.markdown("""
    <style>
    .card { border-radius: 10px; padding: 15px; margin-bottom: 10px; background-color: #1E1E1E; color: white; }
    .killer { border-left: 5px solid #00FF00; }
    .high { border-left: 5px solid #00BFFF; }
    .watch { border-left: 5px solid #FFD700; }
    .medium { border-left: 5px solid #FFA500; }
    .low { border-left: 5px solid #FF6347; }
    .risk { border-left: 5px solid #6c757d; background-color: #2C2C2C; }
    </style>
    """, unsafe_allow_html=True)

    st.title("🚀 Advanced Liquidity & Squeeze Scanner")
    st.info("🚨 **IMPORTANT:** Use with **BTC Market Dashboard**. Look for SWEETS + MTF Alignment.", icon="⚠️")

    with st.sidebar:
        st.header("Settings")
        pairs_input = st.text_area("Watchlist", DEFAULT_PAIRS, height=150)
        pairs = [p.strip() for p in pairs_input.split(',') if p.strip()]
        timeframes = st.multiselect("Timeframes", ['3m', '5m', '15m'], default=['5m', '15m'])
        min_score = st.slider("Minimum Score", 0, 100, 40) 
        scan_btn = st.button("🔍 Scan Market")
        auto_refresh = st.checkbox("Auto Refresh (60s)", False)
        st.header("BTC Regime")
        regime_placeholder = st.empty()

    session_placeholder = st.empty()
    results_placeholder = st.empty()
    
    with st.spinner("Analyzing BTC Regime..."):
        btc_regimes = get_btc_regime()
    
    with regime_placeholder.container():
        st.markdown("### Multi-TF Bias")
        for tf, data in btc_regimes.items():
            emoji = "📈" if "BULL" in data['bias'] else "📉" if "BEAR" in data['bias'] else "➡️"
            st.metric(f"BTC {tf}", f"{emoji} {data['bias']}")
            
    if auto_refresh:
        while True:
            start_time = time.time()
            run_scan(pairs, timeframes, btc_regimes, results_placeholder, session_placeholder, min_score)
            elapsed = time.time() - start_time
            sleep_time = max(0, 60 - elapsed)
            time.sleep(sleep_time)
            btc_regimes = get_btc_regime()
            with regime_placeholder.container():
                st.markdown("### Multi-TF Bias")
                for tf, data in btc_regimes.items():
                    emoji = "📈" if "BULL" in data['bias'] else "📉" if "BEAR" in data['bias'] else "➡️"
                    st.metric(f"BTC {tf}", f"{emoji} {data['bias']}")
    else:
        run_scan(pairs, timeframes, btc_regimes, results_placeholder, session_placeholder, min_score)

if __name__ == "__main__":
    main()
