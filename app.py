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

# --- YOUR WATCHLIST ---
RAW_WATCHLIST = ["BTC", "ETH", "SOL", "WIF", "SPX", "PEOPLE", "SPACE", "DOGE", "LINEA"]
WATCHLIST = [s + "/USDT" for s in RAW_WATCHLIST]

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
    <title>SQZ Matrix Scanner</title>
    <style>
        body { font-family: sans-serif; background: #0b0e11; color: #d1d4dc; margin: 0; padding: 15px; }
        h1 { font-size: 20px; color: #f0b90b; text-align: center; margin-bottom: 5px; }
        .status { text-align: center; font-size: 12px; color: #666; margin-bottom: 20px; }
        
        /* Table Styles */
        .matrix-table { width: 100%; border-collapse: collapse; background: #1e2329; border-radius: 6px; overflow: hidden; }
        .matrix-table th { background: #2b3139; color: #f0b90b; padding: 12px 5px; text-align: center; font-size: 13px; border-bottom: 2px solid #0b0e11; }
        .matrix-table td { padding: 10px 5px; text-align: center; border-bottom: 1px solid #2b3139; font-size: 13px; }
        
        /* Column widths */
        .col-symbol { width: 10%; font-weight: bold; color: #fff; text-align: left !important; padding-left: 15px !important; }
        .col-sqz { width: 12%; }
        .col-bo { width: 10%; color: #848e9c; }
        .col-triple { width: 12%; }

        /* Cell Styles */
        .sqz-vol { color: #848e9c; } /* Gray */
        .sqz-all { color: #0ecb81; } /* Green */
        .sqz-mega { color: #f0b90b; font-weight: bold; } /* Yellow */
        .sqz-special { color: #ff4d4d; } /* Red */
        
        .bo-elephant { color: #f0b90b; font-weight: bold; }
        .bo-tail { color: #0ecb81; font-weight: bold; }
        .bo-none { color: #3a3f47; font-size: 11px; }

        .triple-yes { 
            background: #ff4d4d; color: #fff; font-weight: bold; border-radius: 4px; padding: 2px 0; 
            display: block; margin: 0 5px;
        }
        
        /* Row Highlighting */
        tr.has-breakout { background: #1a2622; } /* Dark Green Tint */
        tr.has-triple { background: #2a1515; } /* Dark Red Tint */
    </style>
</head>
<body>
    <h1>📊 SQZ Matrix Scanner</h1>
    <div id="time" class="status">Connecting...</div>
    
    <table class="matrix-table">
        <thead>
            <tr>
                <th class="col-symbol">COIN</th>
                <th class="col-sqz">3M SQZ</th>
                <th class="col-bo">BO</th>
                <th class="col-sqz">5M SQZ</th>
                <th class="col-bo">BO</th>
                <th class="col-sqz">15M SQZ</th>
                <th class="col-bo">BO</th>
                <th class="col-triple">TRIPLE</th>
            </tr>
        </thead>
        <tbody id="table-body">
            <tr><td colspan="8" style="text-align:center; color:#666;">Scanning...</td></tr>
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
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#666;">No setups found.</td></tr>';
                    return;
                }

                d.results.forEach(item => {
                    // Helper to format cells
                    const fmtSqz = (t) => {
                        if(!t || t === 'None') return '<span class="bo-none">-</span>';
                        if(t.includes('MEGA')) return `<span class="sqz-mega">${t}</span>`;
                        if(t.includes('All')) return `<span class="sqz-all">All Tog</span>`;
                        if(t.includes('Special')) return `<span class="sqz-special">Special</span>`;
                        return `<span class="sqz-vol">Vol</span>`;
                    };
                    
                    const fmtBo = (t) => {
                        if(!t || t === 'None') return '<span class="bo-none">-</span>';
                        if(t.includes('Elephant')) return '<span class="bo-elephant">🐘</span>';
                        if(t.includes('Tail')) return '<span class="bo-tail">🦊</span>';
                        return t;
                    };

                    // Row Class
                    let rowClass = "";
                    if(item.triple_aligned) rowClass = "has-triple";
                    else if(item.has_any_breakout) rowClass = "has-breakout";

                    const triple_html = item.triple_aligned ? '<span class="triple-yes">ALIGNED</span>' : '-';

                    tbody.innerHTML += `
                    <tr class="${rowClass}">
                        <td class="col-symbol">${item.symbol}</td>
                        <td class="col-sqz">${fmtSqz(item.tf_3m.sqz_type)}</td>
                        <td class="col-bo">${fmtBo(item.tf_3m.breakout)}</td>
                        <td class="col-sqz">${fmtSqz(item.tf_5m.sqz_type)}</td>
                        <td class="col-bo">${fmtBo(item.tf_5m.breakout)}</td>
                        <td class="col-sqz">${fmtSqz(item.tf_15m.sqz_type)}</td>
                        <td class="col-bo">${fmtBo(item.tf_15m.breakout)}</td>
                        <td class="col-triple">${triple_html}</td>
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

def check_conditions(row):
    vol_sqz = (row['bb_lower'] > row['kc_lower']) and (row['bb_upper'] < row['kc_upper'])

    vals_at = [row['high'], row['low'], row['sma_20'], row['sma_100']]
    range_at = max(vals_at) - min(vals_at)
    all_together = (range_at <= row['close'] * 0.001)

    vals_sp = [row['high'], row['low'], row['sma_20'], row['sma_100'], row['sma_200']]
    range_sp = max(vals_sp) - min(vals_sp)
    special_one = (range_sp <= row['close'] * 0.001)

    sqz_type = "None"
    is_active = False

    if special_one: sqz_type = "Special One"
    if all_together: sqz_type = "All Together"
    if vol_sqz: sqz_type = "Volatility"
    
    if vol_sqz or all_together or special_one: is_active = True
    if vol_sqz and (special_one or all_together): sqz_type = "MEGA SQZ"

    return sqz_type, is_active

def analyze_timeframe(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 1. SQZ Logic (Look back if breaking)
    curr_sqz, curr_active = check_conditions(last)
    prev_sqz, prev_active = check_conditions(prev)
    
    final_sqz = curr_sqz
    is_breaking = False
    
    # 2. Breakout Logic
    current_range = last['high'] - last['low']
    body = abs(last['close'] - last['open'])
    
    # Size Filter
    recent_candles = df.iloc[-11:-1]
    sqz_candles = recent_candles[recent_candles['vol_sqz_check'] == True]
    avg_sqz_range = (sqz_candles['high'] - sqz_candles['low']).mean() if len(sqz_candles) > 0 else (recent_candles['high'] - recent_candles['low']).mean()
    
    is_valid_size = (current_range >= avg_sqz_range)
    avg_body = df['close'].diff().abs().rolling(10).mean().iloc[-1]
    
    breakout = "None"
    
    if is_valid_size:
        if body > (avg_body * 1.5): breakout = "Elephant"
        elif body < (current_range * 0.4) and current_range > 0: breakout = "Tail"
    
    # If we have a breakout NOW, check if it broke a previous SQZ
    if breakout != "None" and prev_active:
        final_sqz = prev_sqz # Report the SQZ that just ended
        is_breaking = True
    elif not curr_active:
        final_sqz = "None" # No sqz, no breakout

    return final_sqz, curr_active, breakout, is_breaking

def get_coin_data(symbol, exchange):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, limit=300)
        if len(ohlcv) < 200: return None
        df_base = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        
        # We need to process each TF separately
        # But fetching once is faster. CCXT fetch_ohlcv usually returns the timeframe requested.
        # To scan multiple TFs efficiently, we should ideally fetch 3 times.
        # Let's fetch 3 times inside here for accuracy.
        
        data = {}
        has_any_breakout = False
        
        for tf in ['3m', '5m', '15m']:
            try:
                tf_ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=300)
                if len(tf_ohlcv) < 200: continue
                df = pd.DataFrame(tf_ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                df = get_squeeze(df)
                
                sqz_type, is_active, breakout, is_breaking = analyze_timeframe(df)
                
                data[f'tf_{tf}'] = {
                    'sqz_type': sqz_type,
                    'is_active': is_active,
                    'breakout': breakout,
                    'is_breaking': is_breaking
                }
                if breakout != "None": has_any_breakout = True
            except:
                data[f'tf_{tf}'] = {'sqz_type': 'Error', 'is_active': False, 'breakout': 'None', 'is_breaking': False}

        # Check Triple Alignment
        active_3m = data.get('tf_3m', {}).get('is_active', False)
        active_5m = data.get('tf_5m', {}).get('is_active', False)
        active_15m = data.get('tf_15m', {}).get('is_active', False)
        triple_aligned = active_3m and active_5m and active_15m
        
        # Only return if something is happening
        if triple_aligned or has_any_breakout or active_3m or active_5m or active_15m:
            clean_name = symbol.replace("/USDT", "")
            return {
                "symbol": clean_name,
                "tf_3m": data.get('tf_3m', {}),
                "tf_5m": data.get('tf_5m', {}),
                "tf_15m": data.get('tf_15m', {}),
                "triple_aligned": triple_aligned,
                "has_any_breakout": has_any_breakout
            }
    except Exception as e:
        print(f"Error analyzing {symbol}: {e}")
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
            print(f"Scanning using {ex_name}...")
            
            for sym in WATCHLIST:
                res = get_coin_data(sym, exchange)
                if res: results.append(res)
            
            # Sort: Triple first, then Breakouts
            results.sort(key=lambda x: (x['triple_aligned'], x['has_any_breakout']), reverse=True)
            
            DATA_CACHE = {
                "last_update": time.strftime("%H:%M:%S"), 
                "results": results,
                "exchange": ex_name
            }
            print(f"Scan Complete: {len(results)} setups.")
            time.sleep(45)

        except Exception as e:
            print(f"Error: {e}. Switching exchange...")
            exchange, ex_name = get_exchange_connection()

# --- START SCANNER ---
scanner_thread = threading.Thread(target=scanner_loop)
scanner_thread.daemon = True
scanner_thread.start()

# --- FLASK ROUTES ---
@app.route('/')
def home():
    return render_template_string(HTML_CODE)

@app.route('/data')
def data():
    return jsonify(DATA_CACHE)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
