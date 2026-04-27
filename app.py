import ccxt
import pandas as pd
import numpy as np
from flask import Flask, jsonify, render_template_string
import threading
import time
import os
from collections import deque

# --- CONFIG ---
app = Flask(__name__)
DATA_CACHE = {"last_update": "Initializing...", "results": [], "call_log": [], "exchange": "None"}

# --- WATCHLIST & SETTINGS ---
RAW_WATCHLIST = ["BTC", "ETH", "SOL", "WIF", "SPX", "PEOPLE", "SPACE", "DOGE", "LINEA", "ZEC", "TAO"]
WATCHLIST = [s + "/USDT" for s in RAW_WATCHLIST]
TIMEFRAMES = ['3m', '5m', '15m']

# PERSISTENCE & LOG
SIGNAL_STATE = {} # Format: { "BTC_3m": { "type": "Bull Ele", "count": 0 } }
CALL_LOG = deque(maxlen=100) # Thread-safe circular buffer

# --- EXCHANGE SETUP (FAILOVER) ---
EXCHANGE_CLASSES = [
    {'name': 'Binance', 'class': ccxt.binanceusdm},
    {'name': 'Bybit', 'class': ccxt.bybit},
    {'name': 'OKX', 'class': ccxt.okx}
]

def get_exchange_connection():
    for ex_info in EXCHANGE_CLASSES:
        try:
            ex = ex_info['class']({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}
            })
            ex.load_markets()
            print(f"Successfully connected to {ex_info['name']}")
            return ex, ex_info['name']
        except Exception as e:
            print(f"Failed to connect to {ex_info['name']}: {e}")
            continue
    return None, "Disconnected"

# --- HTML TEMPLATE ---
HTML_CODE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SQZ Pro Scanner</title>
    <style>
        body { font-family: monospace; background: #0b0e11; color: #d1d4dc; margin: 0; padding: 10px; font-size: 12px; }
        h1 { font-size: 16px; color: #f0b90b; text-align: center; margin-bottom: 5px; }
        .status { text-align: center; font-size: 11px; color: #666; margin-bottom: 15px; }
        
        /* Table Layout */
        table { width: 100%; border-collapse: collapse; background: #1e2329; margin-bottom: 30px; }
        th.group-header { background: #2b3139; color: #f0b90b; padding: 8px 0; text-align: center; border-bottom: 1px solid #0b0e11; }
        th.sub-header { background: #2b3139; color: #848e9c; padding: 5px 0; text-align: center; font-size: 10px; font-weight: normal; border-bottom: 2px solid #0b0e11; }
        
        td { padding: 6px 4px; text-align: center; border-bottom: 1px solid #2b3139; white-space: nowrap; }
        td.coin { text-align: left; font-weight: bold; color: #fff; padding-left: 10px; border-right: 1px solid #0b0e11; }
        td.align { text-align: center; font-weight: bold; padding-right: 10px; border-left: 1px solid #0b0e11; }
        
        /* Colors */
        .none { color: #3a3f47; }
        .vol { color: #848e9c; }
        .all { color: #0ecb81; }
        .mega { color: #f0b90b; }
        .spec { color: #ff4d4d; }
        .bull { color: #0ecb81; }
        .bear { color: #f6465d; }
        
        /* Call Log */
        .log-section { margin-top: 20px; }
        .log-title { color: #f0b90b; border-bottom: 1px solid #2b3139; padding-bottom: 5px; margin-bottom: 10px; }
        .log-card { background: #1e2329; padding: 10px; margin-bottom: 8px; border-left: 3px solid #2b3139; display: flex; justify-content: space-between; align-items: center; }
        .log-time { font-size: 10px; color: #666; display: block; margin-bottom: 4px; }
        .log-main { font-weight: bold; }
        .log-details { font-size: 11px; color: #848e9c; }
    </style>
</head>
<body>
    <h1>SQZ PRO SCANNER</h1>
    <div id="time" class="status">Connecting...</div>
    
    <!-- MAIN TABLE -->
    <table id="scanTable">
        <thead>
            <tr>
                <th rowspan="2" class="group-header" style="width: 5%;">COIN</th>
                <th colspan="3" class="group-header">3m</th>
                <th colspan="3" class="group-header">5m</th>
                <th colspan="3" class="group-header">15m</th>
                <th rowspan="2" class="group-header" style="width: 10%;">ALIGN</th>
            </tr>
            <tr>
                <th class="sub-header">SQZ</th><th class="sub-header">BREAK</th><th class="sub-header">SWEEP</th>
                <th class="sub-header">SQZ</th><th class="sub-header">BREAK</th><th class="sub-header">SWEEP</th>
                <th class="sub-header">SQZ</th><th class="sub-header">BREAK</th><th class="sub-header">SWEEP</th>
            </tr>
        </thead>
        <tbody id="tableBody">
            <tr><td colspan="11" style="text-align:center; color:#666; padding:20px;">Scanning...</td></tr>
        </tbody>
    </table>

    <!-- CALL LOG -->
    <div class="log-section">
        <div class="log-title">CALL LOG (LAST 100)</div>
        <div id="logContainer"></div>
    </div>

    <script>
        const labels = {
            'Volatility': 'Vol', 'All Together': 'All', 'Special One': 'Spec', 'MEGA SQZ': 'Mega', 'None': '-',
            'Bull Elephant': 'BullEle', 'Bear Elephant': 'BearEle', 'Bull Tail': 'BullTl', 'Bear Tail': 'BearTl',
            'Bull Sweep': 'BullSw', 'Bear Sweep': 'BearSw',
            'Vol Align': 'Vol Al', 'All Align': 'All Al', 'Spec Align': 'Spec Al', 'Mega Align': 'Mega Al'
        };

        function getClass(type, value) {
            if(value === 'None' || value === '-') return 'none';
            if(type === 'sqz') {
                if(value.includes('Mega')) return 'mega';
                if(value.includes('Spec')) return 'spec';
                if(value.includes('All')) return 'all';
                if(value.includes('Vol')) return 'vol';
            }
            if(type === 'break' || type === 'sweep' || type === 'align') {
                if(value.includes('Bull')) return 'bull';
                if(value.includes('Bear')) return 'bear';
                if(value.includes('Mega') || value.includes('Align')) return 'mega';
                if(value.includes('All') || value.includes('Vol')) return 'all';
            }
            return '';
        }
        
        function fmt(text) { return labels[text] || text; }

        async function load() {
            try {
                const r = await fetch('/data');
                const d = await r.json();
                document.getElementById('time').innerText = "Last Update: " + d.last_update + " | Exchange: " + d.exchange;
                
                // Table
                const tbody = document.getElementById('tableBody');
                tbody.innerHTML = '';
                if(d.results.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; color:#666;">No setups.</td></tr>';
                } else {
                    d.results.forEach(item => {
                        let rowClass = (item.has_break) ? 'highlight-row' : '';
                        tbody.innerHTML += `
                        <tr>
                            <td class="coin">${item.coin}</td>
                            <td class="${getClass('sqz', item.tf_3m.sqz)}">${fmt(item.tf_3m.sqz)}</td>
                            <td class="${getClass('break', item.tf_3m.break)}">${fmt(item.tf_3m.break)}</td>
                            <td class="${getClass('sweep', item.tf_3m.sweep)}">${fmt(item.tf_3m.sweep)}</td>
                            
                            <td class="${getClass('sqz', item.tf_5m.sqz)}">${fmt(item.tf_5m.sqz)}</td>
                            <td class="${getClass('break', item.tf_5m.break)}">${fmt(item.tf_5m.break)}</td>
                            <td class="${getClass('sweep', item.tf_5m.sweep)}">${fmt(item.tf_5m.sweep)}</td>
                            
                            <td class="${getClass('sqz', item.tf_15m.sqz)}">${fmt(item.tf_15m.sqz)}</td>
                            <td class="${getClass('break', item.tf_15m.break)}">${fmt(item.tf_15m.break)}</td>
                            <td class="${getClass('sweep', item.tf_15m.sweep)}">${fmt(item.tf_15m.sweep)}</td>
                            
                            <td class="align ${getClass('align', item.alignment)}">${fmt(item.alignment)}</td>
                        </tr>`;
                    });
                }

                // Call Log
                const logContainer = document.getElementById('logContainer');
                logContainer.innerHTML = '';
                if(d.call_log.length === 0) {
                    logContainer.innerHTML = '<div style="color:#666; padding:10px;">No signals recorded yet.</div>';
                } else {
                    d.call_log.forEach(log => {
                        logContainer.innerHTML += `
                        <div class="log-card" style="border-color: ${log.break.includes('Bull') ? '#0ecb81' : '#f6465d'};">
                            <div>
                                <span class="log-time">${log.time}</span>
                                <span class="log-main">${log.symbol} | ${log.timeframe}</span>
                            </div>
                            <div class="log-details">
                                SQZ: ${log.sqz} | Break: ${log.break} | Sweep: ${log.sweep}
                            </div>
                        </div>`;
                    });
                }
            } catch(e) { console.error("Error", e); }
        }
        setInterval(load, 10000);
        load();
    </script>
</body>
</html>
"""

# --- LOGIC FUNCTIONS ---

def get_squeeze_type(row):
    # Standard calculations
    vol_sqz = (row['bb_lower'] > row['kc_lower']) and (row['bb_upper'] < row['kc_upper'])
    
    # Range check for MAs
    # All Together: High, Low, SMA20, SMA100
    vals_all = [row['high'], row['low'], row['sma_20'], row['sma_100']]
    range_all = max(vals_all) - min(vals_all)
    is_all = (range_all <= row['close'] * 0.003) # 0.3% threshold from prompt

    # Special One: High, Low, SMA20, SMA100, SMA200
    vals_spec = [row['high'], row['low'], row['sma_20'], row['sma_100'], row['sma_200']]
    range_spec = max(vals_spec) - min(vals_spec)
    is_spec = (range_spec <= row['close'] * 0.003)

    # Classification
    if vol_sqz and (is_all or is_spec): return "MEGA SQZ"
    if is_spec: return "Special One"
    if is_all: return "All Together"
    if vol_sqz: return "Volatility"
    return "None"

def check_compression(df, idx):
    """
    Checks if valid compression exists based on SQZ Range (0.3% rule)
    Allows fast check (1-2 candles) and standard check (5 candles)
    """
    price = df['close'].iloc[idx]
    
    # Check last 5 candles range (Standard)
    if idx >= 5:
        max_h = df['high'].iloc[idx-4:idx+1].max()
        min_l = df['low'].iloc[idx-4:idx+1].min()
        if (max_h - min_l) <= (price * 0.003): return True

    # Check last 2 candles range (Fast)
    if idx >= 2:
        max_h = df['high'].iloc[idx-1:idx+1].max()
        min_l = df['low'].iloc[idx-1:idx+1].min()
        if (max_h - min_l) <= (price * 0.003): return True
        
    return False

def analyze_timeframe(df, symbol, tf):
    # Calc Indicators
    df['sma_20'] = df['close'].rolling(20).mean()
    df['sma_100'] = df['close'].rolling(100).mean()
    df['sma_200'] = df['close'].rolling(200).mean()
    
    # BB & KC
    df['std_20'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['sma_20'] + (df['std_20'] * 2)
    df['bb_lower'] = df['sma_20'] - (df['std_20'] * 2)
    
    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift()), abs(df['low'] - df['close'].shift())))
    df['atr'] = df['tr'].rolling(20).mean()
    df['kc_upper'] = df['sma_20'] + (df['atr'] * 1.5)
    df['kc_lower'] = df['sma_20'] - (df['atr'] * 1.5)

    # Forward fill for safety
    df.fillna(method='ffill', inplace=True)
    df.fillna(method='bfill', inplace=True)

    # Current and Previous Data
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 1. SQZ DETECTION ---
    # Check if compression exists on current OR previous candles (setup)
    # Primary SQZ logic is based on the Type definition
    current_sqz_type = get_sqz_type(last)
    prev_sqz_type = get_sqz_type(prev)
    
    # Also check raw compression flag for "fast" markets where indicators lag
    is_compressed_now = check_compression(df, len(df)-1)
    was_compressed = check_compression(df, len(df)-2)

    # Determine final SQZ state for display
    # If currently compressed but not typed (rare), label generic?
    # No, stick to types. If current is None but compression true, mark Volatility?
    # Let's assume Type logic covers it.
    
    # --- 2. SWEEP DETECTION ---
    recent_high = df['high'].iloc[-21:-1].max()
    recent_low = df['low'].iloc[-21:-1].min()
    
    sweep = "None"
    if last['low'] < recent_low and last['close'] > recent_low: sweep = "Bull Sweep"
    elif last['high'] > recent_high and last['close'] < recent_high: sweep = "Bear Sweep"

    # --- 3. BREAK DETECTION (FIRST BREAK) ---
    # Logic: Breakout on Current. Previous was SQZ (or compression). Prev Prev NOT Break.
    
    break_type = "None"
    key = f"{symbol}_{tf}"
    
    # Conditions for a valid Setup
    setup_exists = (prev_sqz_type != "None") or was_compressed
    current_is_expanding = not is_compressed_now # Price left compression
    
    # Calculate SQZ Candle Averages (last 5 candles)
    avg_body = df['open'].iloc[-6:-1].sub(df['close'].iloc[-6:-1]).abs().mean()
    avg_range = (df['high'].iloc[-6:-1] - df['low'].iloc[-6:-1]).mean()
    
    # Current Candle Props
    body = abs(last['close'] - last['open'])
    range_c = last['high'] - last['low']
    is_bull = last['close'] > last['open']
    
    # Elephant Check: Body >= 1x Avg Body of SQZ candles
    # Tail Check: Body small, Range large, Wick dominance
    is_elephant = body >= avg_body
    is_tail = (body < range_c * 0.5) and (range_c > avg_range)
    
    # --- Persistence Logic ---
    # Check if we already have an active signal
    if key in SIGNAL_STATE:
        SIGNAL_STATE[key]['count'] += 1
        if SIGNAL_STATE[key]['count'] <= 3:
            # Keep signal active
            break_type = SIGNAL_STATE[key]['type'] + " (Active)"
            # Reset if still expanding? No, just keep for 3 candles.
        else:
            del SIGNAL_STATE[key] # Expired
            
    # Only detect NEW break if no active signal
    if break_type == "None" and setup_exists and current_is_expanding:
        
        if is_elephant:
            # Filter: Elephant must not conflict with Sweep
            if (is_bull and sweep != "Bear Sweep") or (not is_bull and sweep != "Bull Sweep"):
                break_type = "Bull Elephant" if is_bull else "Bear Elephant"
        
        elif is_tail:
            # Filter: Tail REQUIRES Sweep
            if is_bull and sweep == "Bull Sweep":
                break_type = "Bull Tail"
            elif not is_bull and sweep == "Bear Sweep":
                break_type = "Bear Tail"
    
    # Register New Signal
    if "Active" not in break_type and break_type != "None":
        SIGNAL_STATE[key] = {'type': break_type, 'count': 0}
        # Log it
        CALL_LOG.appendleft({
            "time": time.strftime("%H:%M:%S"),
            "symbol": symbol.replace("/USDT", ""),
            "timeframe": tf,
            "sqz": prev_sqz_type, # The setup that broke
            "break": break_type,
            "sweep": sweep
        })

    return {
        "sqz": current_sqz_type if current_sqz_type != "None" else ("Volatility" if is_compressed_now else "None"),
        "break": break_type,
        "sweep": sweep
    }

# --- SCANNER LOOP ---
def scanner_loop():
    global DATA_CACHE
    exchange, ex_name = get_exchange_connection()
    
    while True:
        if exchange is None:
            time.sleep(30)
            exchange, ex_name = get_exchange_connection()
            continue
            
        try:
            results = []
            for sym in WATCHLIST:
                tf_data = {}
                has_break = False
                
                for tf in TIMEFRAMES:
                    ohlcv = exchange.fetch_ohlcv(sym, timeframe=tf, limit=300)
                    if len(ohlcv) < 200: continue
                    df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                    
                    res = analyze_timeframe(df, sym, tf)
                    tf_data[tf] = res
                    if "Active" in res['break'] or res['break'] != "None": has_break = True

                # Alignment
                sqz_3 = tf_data.get('3m', {}).get('sqz', 'None')
                sqz_5 = tf_data.get('5m', {}).get('sqz', 'None')
                sqz_15 = tf_data.get('15m', {}).get('sqz', 'None')
                
                alignment = "None"
                if sqz_3 != "None" and sqz_3 == sqz_5 == sqz_15:
                    alignment = f"{sqz_3} Align" # e.g., "Vol Align", "Mega Align"
                
                results.append({
                    "coin": sym.replace("/USDT", ""),
                    "tf_3m": tf_data.get('3m', {}),
                    "tf_5m": tf_data.get('5m', {}),
                    "tf_15m": tf_data.get('15m', {}),
                    "alignment": alignment,
                    "has_break": has_break
                })
            
            # Sort: Breaks first
            results.sort(key=lambda x: x['has_break'], reverse=True)
            
            DATA_CACHE = {
                "last_update": time.strftime("%H:%M:%S"),
                "results": results,
                "call_log": list(CALL_LOG),
                "exchange": ex_name
            }
            time.sleep(45)
            
        except Exception as e:
            print(f"Error: {e}. Switching exchange...")
            exchange, ex_name = get_exchange_connection()

# --- ROUTES ---
@app.route('/')
def home():
    return render_template_string(HTML_CODE)

@app.route('/data')
def data():
    return jsonify(DATA_CACHE)

if __name__ == '__main__':
    scanner_thread = threading.Thread(target=scanner_loop)
    scanner_thread.daemon = True
    scanner_thread.start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
