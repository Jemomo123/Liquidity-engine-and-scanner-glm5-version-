import ccxt
import pandas as pd
import numpy as np
from flask import Flask, jsonify, render_template_string
import threading
import time
import os

# --- CONFIG ---
app = Flask(__name__)
DATA_CACHE = {"last_update": "Initializing...", "results": [], "exchange": "None"}

# --- WATCHLIST ---
RAW_WATCHLIST = ["BTC", "ETH", "SOL", "WIF", "SPX", "PEOPLE", "SPACE", "DOGE", "LINEA"]
WATCHLIST = [s + "/USDT" for s in RAW_WATCHLIST]

# --- EXCHANGE SETUP ---
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
    <title>SQZ Narrative Scanner</title>
    <style>
        body { font-family: sans-serif; background: #0b0e11; color: #d1d4dc; margin: 0; padding: 15px; }
        h1 { font-size: 20px; color: #f0b90b; text-align: center; margin-bottom: 5px; }
        .status { text-align: center; font-size: 12px; color: #666; margin-bottom: 20px; }
        
        /* Table Styles */
        .matrix-table { width: 100%; border-collapse: collapse; background: #1e2329; border-radius: 6px; overflow: hidden; }
        .matrix-table th { background: #2b3139; color: #f0b90b; padding: 12px 10px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #0b0e11; }
        .matrix-table td { padding: 12px 10px; border-bottom: 1px solid #2b3139; font-size: 13px; vertical-align: top; line-height: 1.4; }
        
        /* Column widths */
        .col-coin { width: 8%; font-weight: bold; color: #fff; }
        .col-state { width: 22%; }
        .col-liq { width: 20%; }
        .col-sig { width: 20%; }
        .col-align { width: 20%; }

        /* Text Styles */
        .text-normal { color: #848e9c; }
        .text-highlight { color: #d1d4dc; font-weight: 500; }
        .text-breaking { color: #f0b90b; font-weight: bold; }
        .text-aligned { color: #ff4d4d; font-weight: bold; }
        
        /* Row Highlighting */
        tr.has-signal { background: #1a2622; } /* Dark Green Tint */
        tr.has-alignment { background: #2a1515; } /* Dark Red Tint */
    </style>
</head>
<body>
    <h1>SQZ Narrative Scanner</h1>
    <div id="time" class="status">Connecting...</div>
    
    <table class="matrix-table">
        <thead>
            <tr>
                <th class="col-coin">COIN</th>
                <th class="col-state">MARKET STATE</th>
                <th class="col-liq">LIQUIDITY</th>
                <th class="col-sig">SIGNAL</th>
                <th class="col-align">ALIGNMENT</th>
            </tr>
        </thead>
        <tbody id="table-body">
            <tr><td colspan="5" style="text-align:center; color:#666;">Scanning...</td></tr>
        </tbody>
    </table>

    <script>
        async function load() {
            try {
                const r = await fetch('/data');
                const d = await r.json();
                document.getElementById('time').innerText = "Last Update: " + d.last_update + " | Exchange: " + d.exchange;
                const tbody = document.getElementById('table-body');
                tbody.innerHTML = '';

                if(d.results.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#666;">No events detected.</td></tr>';
                    return;
                }

                d.results.forEach(item => {
                    // Determine row highlight class
                    let rowClass = "";
                    if(item.alignment_text.includes("special alignment")) rowClass = "has-alignment";
                    else if(item.signal_text.includes("breakout") || item.signal_text.includes("rejection")) rowClass = "has-signal";

                    // Apply classes for text
                    let state_class = item.market_state.includes("compressed") ? 'text-highlight' : 'text-normal';
                    if(item.market_state.includes("breaking")) state_class = 'text-breaking';
                    
                    let align_class = item.alignment_text.includes("special alignment") ? 'text-aligned' : 'text-normal';
                    
                    tbody.innerHTML += `
                    <tr class="${rowClass}">
                        <td class="col-coin">${item.symbol}</td>
                        <td class="col-state ${state_class}">${item.market_state}</td>
                        <td class="col-liq text-normal">${item.liquidity_text}</td>
                        <td class="col-sig text-highlight">${item.signal_text}</td>
                        <td class="col-align ${align_class}">${item.alignment_text}</td>
                    </tr>`;
                });
            } catch(e) { console.error("Error", e); }
        }
        setInterval(load, 10000);
        load();
    </script>
</body>
</html>
"""

# --- INDICATOR LOGIC ---
def get_squeeze(df):
    df['sma_20'] = df['close'].rolling(20).mean()
    df['std_20'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['sma_20'] + (df['std_20'] * 2)
    df['bb_lower'] = df['sma_20'] - (df['std_20'] * 2)

    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift()), abs(df['low'] - df['close'].shift())))
    df['atr'] = df['tr'].rolling(20).mean()
    df['kc_upper'] = df['sma_20'] + (df['atr'] * 1.5)
    df['kc_lower'] = df['sma_20'] - (df['atr'] * 1.5)

    df['sma_100'] = df['close'].rolling(100).mean()
    df['sma_200'] = df['close'].rolling(200).mean()
    
    df['vol_sqz_check'] = (df['bb_lower'] > df['kc_lower']) & (df['bb_upper'] < df['kc_upper'])
    return df

def get_sqz_type(row):
    vol_sqz = (row['bb_lower'] > row['kc_lower']) and (row['bb_upper'] < row['kc_upper'])
    
    vals_sp = [row['high'], row['low'], row['sma_20'], row['sma_100'], row['sma_200']]
    range_sp = max(vals_sp) - min(vals_sp)
    special_one = (range_sp <= row['close'] * 0.001)

    vals_at = [row['high'], row['low'], row['sma_20'], row['sma_100']]
    range_at = max(vals_at) - min(vals_at)
    all_together = (range_at <= row['close'] * 0.001)

    if vol_sqz and (special_one or all_together): return "Mega"
    if special_one: return "Special One"
    if all_together: return "All Together"
    if vol_sqz: return "Volatility"
    return "None"

def analyze_timeframe(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 1. State
    current_sqz = get_sqz_type(last)
    prev_sqz = get_sqz_type(prev)
    is_active = current_sqz != "None"
    
    # 2. Liquidity
    recent_high = df['high'].iloc[-21:-1].max()
    recent_low = df['low'].iloc[-21:-1].min()
    liquidity = "neutral"
    if last['low'] < recent_low and last['close'] > recent_low: liquidity = "swept lows"
    elif last['high'] > recent_high and last['close'] < recent_high: liquidity = "swept highs"

    # 3. Signal (Breakout / Rejection)
    current_range = last['high'] - last['low']
    body = abs(last['close'] - last['open'])
    
    # Size Filter
    recent_candles = df.iloc[-11:-1]
    sqz_candles = recent_candles[recent_candles['vol_sqz_check'] == True]
    avg_sqz_range = (sqz_candles['high'] - sqz_candles['low']).mean() if len(sqz_candles) > 0 else (recent_candles['high'] - recent_candles['low']).mean()
    is_valid_size = (current_range >= avg_sqz_range)
    
    signal = "none"
    direction = "neutral"
    
    avg_body = df['close'].diff().abs().rolling(10).mean().iloc[-1]
    is_elephant = body > (avg_body * 1.5)
    is_tail = body < (current_range * 0.4) and current_range > 0
    
    if is_valid_size:
        if is_elephant:
            signal = "breakout"
            direction = "up" if last['close'] > last['open'] else "down"
        elif is_tail:
            signal = "rejection"
            upper_wick = last['high'] - max(last['open'], last['close'])
            lower_wick = min(last['open'], last['close']) - last['low']
            direction = "up" if lower_wick > upper_wick else "down"

    return {
        "sqz_type": current_sqz,
        "prev_sqz": prev_sqz,
        "is_active": is_active,
        "liquidity": liquidity,
        "signal": signal,
        "direction": direction
    }

def get_coin_data(symbol, exchange):
    try:
        tf_data = {}
        for tf in ['3m', '5m', '15m']:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=300)
            if len(ohlcv) < 200: return None
            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            df = get_squeeze(df)
            tf_data[tf] = analyze_timeframe(df)
            
        # --- GENERATE NARRATIVE ---

        # 1. Market State
        # Priority: Breakout > Active Squeeze (High TF > Low TF)
        state_parts = []
        
        # Check for breakouts first
        for tf in ['15m', '5m', '3m']:
            d = tf_data[tf]
            if d['signal'] != 'none' and d['prev_sqz'] != 'None':
                state_parts.append(f"Breaking {tf} compression")
        
        # Then check active compressions
        if not state_parts:
            for tf in ['15m', '5m', '3m']:
                d = tf_data[tf]
                if d['is_active']:
                    state_parts.append(f"Compressed on {tf} ({d['sqz_type']})")
        
        market_state = "The market is moving freely." if not state_parts else ". ".join(state_parts) + "."

        # 2. Liquidity
        liq_parts = []
        for tf, d in tf_data.items():
            if d['liquidity'] == "swept lows": liq_parts.append(f"Price swept {tf} lows")
            if d['liquidity'] == "swept highs": liq_parts.append(f"Price swept {tf} highs")
        
        liquidity_text = "Price did not sweep any levels." if not liq_parts else " and ".join(liq_parts) + "."

        # 3. Signal
        sig_parts = []
        for tf, d in tf_data.items():
            if d['signal'] == 'breakout':
                dir_word = "upward" if d['direction'] == 'up' else "downward"
                sig_parts.append(f"Strong breakout {dir_word} on {tf}")
            elif d['signal'] == 'rejection':
                dir_word = "lower prices" if d['direction'] == 'up' else "higher prices"
                sig_parts.append(f"Rejection of {dir_word} on {tf}")
        
        signal_text = "No signal." if not sig_parts else " and ".join(sig_parts) + "."

        # 4. Alignment
        sqz_3 = tf_data['3m']['sqz_type']
        sqz_5 = tf_data['5m']['sqz_type']
        sqz_15 = tf_data['15m']['sqz_type']
        
        alignment_text = "No alignment."
        if (sqz_3 != "None" and sqz_3 == sqz_5 == sqz_15):
            alignment_text = f"A special alignment event is present ({sqz_3})."

        # Filter: Only return if something is happening
        is_event = (sqz_3 != "None" or sqz_5 != "None" or sqz_15 != "None" or sig_parts)
        
        if is_event:
            return {
                "symbol": symbol.replace("/USDT", ""),
                "market_state": market_state,
                "liquidity_text": liquidity_text,
                "signal_text": signal_text,
                "alignment_text": alignment_text
            }
            
    except Exception as e:
        print(f"Error {symbol}: {e}")
    return None

# --- SCANNER THREAD ---
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
                res = get_coin_data(sym, exchange)
                if res: results.append(res)
            
            DATA_CACHE = {
                "last_update": time.strftime("%H:%M:%S"), 
                "results": results,
                "exchange": ex_name
            }
            time.sleep(45)

        except Exception as e:
            print(f"Error: {e}. Switching exchange...")
            exchange, ex_name = get_exchange_connection()

# --- START ---
scanner_thread = threading.Thread(target=scanner_loop)
scanner_thread.daemon = True
scanner_thread.start()

@app.route('/')
def home():
    return render_template_string(HTML_CODE)

@app.route('/data')
def data():
    return jsonify(DATA_CACHE)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
