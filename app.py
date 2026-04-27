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
RAW_WATCHLIST = ["BTC", "ETH", "SOL", "WIF", "SPX", "PEOPLE", "SPACE", "DOGE", "LINEA", "ZEC", "TAO"]
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
    <title>SQZ Scanner</title>
    <style>
        body { font-family: monospace; background: #0b0e11; color: #d1d4dc; margin: 0; padding: 10px; font-size: 12px; }
        h1 { font-size: 16px; color: #f0b90b; text-align: center; margin-bottom: 5px; }
        .status { text-align: center; font-size: 11px; color: #666; margin-bottom: 15px; }
        
        /* Table Layout */
        table { width: 100%; border-collapse: collapse; background: #1e2329; }
        
        /* Header Grouping */
        th.group-header { background: #2b3139; color: #f0b90b; padding: 8px 0; text-align: center; border-bottom: 1px solid #0b0e11; font-size: 13px; }
        th.sub-header { background: #2b3139; color: #848e9c; padding: 5px 0; text-align: center; font-size: 10px; font-weight: normal; border-bottom: 2px solid #0b0e11; }
        
        /* Cells */
        td { padding: 6px 4px; text-align: center; border-bottom: 1px solid #2b3139; white-space: nowrap; }
        td.coin { text-align: left; font-weight: bold; color: #fff; padding-left: 10px; border-right: 1px solid #0b0e11; }
        td.align { text-align: center; font-weight: bold; padding-right: 10px; border-left: 1px solid #0b0e11; }
        
        /* Colors for Values */
        .none { color: #3a3f47; } /* Very dim */
        .vol { color: #848e9c; }
        .all { color: #0ecb81; } /* Green */
        .mega { color: #f0b90b; } /* Yellow */
        .special { color: #ff4d4d; } /* Red */
        
        .bull { color: #0ecb81; }
        .bear { color: #f6465d; }
        
        .highlight-row { background: #1a2622; } /* Light green bg for active breaks */
    </style>
</head>
<body>
    <h1>SQZ MATRIX SCANNER</h1>
    <div id="time" class="status">Connecting...</div>
    
    <table id="scanTable">
        <thead>
            <!-- Main Headers -->
            <tr>
                <th rowspan="2" class="group-header" style="width: 5%;">COIN</th>
                <th colspan="3" class="group-header">3m</th>
                <th colspan="3" class="group-header">5m</th>
                <th colspan="3" class="group-header">15m</th>
                <th rowspan="2" class="group-header" style="width: 10%;">ALIGN</th>
            </tr>
            <!-- Sub Headers -->
            <tr>
                <th class="sub-header">SQZ</th>
                <th class="sub-header">BREAK</th>
                <th class="sub-header">SWEEP</th>
                
                <th class="sub-header">SQZ</th>
                <th class="sub-header">BREAK</th>
                <th class="sub-header">SWEEP</th>
                
                <th class="sub-header">SQZ</th>
                <th class="sub-header">BREAK</th>
                <th class="sub-header">SWEEP</th>
            </tr>
        </thead>
        <tbody id="tableBody">
            <tr><td colspan="11" style="text-align:center; color:#666; padding:20px;">Scanning...</td></tr>
        </tbody>
    </table>

    <script>
        const labels = {
            'Volatility': 'Vol', 'All Together': 'All', 'Special One': 'Spec', 'MEGA SQZ': 'MEGA', 'None': '-',
            'Bull Elephant': 'BullEle', 'Bear Elephant': 'BearEle', 'Bull Tail': 'BullTl', 'Bear Tail': 'BearTl',
            'Bull Sweep': 'BullSw', 'Bear Sweep': 'BearSw'
        };

        function getClass(type, value) {
            if(value === 'None' || value === '-') return 'none';
            if(type === 'sqz') {
                if(value.includes('MEGA')) return 'mega';
                if(value.includes('Special')) return 'special';
                if(value.includes('All')) return 'all';
                if(value.includes('Vol')) return 'vol';
            }
            if(type === 'break' || type === 'sweep') {
                if(value.includes('Bull')) return 'bull';
                if(value.includes('Bear')) return 'bear';
            }
            if(type === 'align') {
                if(value !== 'None') return 'mega';
            }
            return '';
        }

        function fmt(text) { return labels[text] || text; }

        async function load() {
            try {
                const r = await fetch('/data');
                const d = await r.json();
                document.getElementById('time').innerText = "Last Update: " + d.last_update + " | Exchange: " + d.exchange;
                const tbody = document.getElementById('tableBody');
                tbody.innerHTML = '';

                if(d.results.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; color:#666;">No setups found.</td></tr>';
                    return;
                }

                d.results.forEach(item => {
                    let rowClass = (item.has_break) ? 'highlight-row' : '';
                    
                    let alignTxt = item.alignment;
                    let alignClass = getClass('align', alignTxt);
                    if(alignTxt !== 'None') alignTxt = "YES(" + item.alignment_type + ")";

                    tbody.innerHTML += `
                    <tr class="${rowClass}">
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
                        
                        <td class="align ${alignClass}">${alignTxt}</td>
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

# --- LOGIC ---
def get_squeeze(df):
    # Standard BB(20,2) and KC(20,1.5)
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
    
    # Boolean helper
    df['vol_sqz_check'] = (df['bb_lower'] > df['kc_lower']) & (df['bb_upper'] < df['kc_upper'])
    return df

def get_sqz_type(row):
    vol_sqz = (row['bb_lower'] > row['kc_lower']) and (row['bb_upper'] < row['kc_upper'])
    
    # Check Special One (Price + 20 + 100 + 200)
    vals_sp = [row['high'], row['low'], row['sma_20'], row['sma_100'], row['sma_200']]
    range_sp = max(vals_sp) - min(vals_sp)
    special_one = (range_sp <= row['close'] * 0.001)

    # Check All Together (Price + 20 + 100)
    vals_at = [row['high'], row['low'], row['sma_20'], row['sma_100']]
    range_at = max(vals_at) - min(vals_at)
    all_together = (range_at <= row['close'] * 0.001)

    # Logic Hierarchy
    if vol_sqz and (special_one or all_together):
        return "MEGA SQZ"
    if special_one:
        return "Special One"
    if all_together:
        return "All Together"
    if vol_sqz:
        return "Volatility"
    return "None"

def analyze_timeframe(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 1. SQZ TYPE
    current_sqz = get_sqz_type(last)
    prev_sqz = get_sqz_type(prev)
    
    # 2. BREAK CONFIRMATION
    # Rule: Only mark if leaving compression (Prev was SQZ) and Candle is large
    current_range = last['high'] - last['low']
    body = abs(last['close'] - last['open'])
    
    # Size Filter: Must be >= avg squeeze candles
    recent_candles = df.iloc[-11:-1]
    sqz_candles = recent_candles[recent_candles['vol_sqz_check'] == True]
    avg_sqz_range = (sqz_candles['high'] - sqz_candles['low']).mean() if len(sqz_candles) > 0 else (recent_candles['high'] - recent_candles['low']).mean()
    
    is_valid_size = (current_range >= avg_sqz_range)
    
    break_type = "None"
    
    # Only check break if previous was compressed (Leaving compression rule)
    if prev_sqz != "None" and is_valid_size:
        # Elephant Logic
        avg_body = df['close'].diff().abs().rolling(10).mean().iloc[-1]
        is_elephant = body > (avg_body * 1.5)
        
        # Tail Logic
        is_tail = body < (current_range * 0.4) and current_range > 0
        
        direction = "Bull" if last['close'] > last['open'] else "Bear"
        
        if is_elephant:
            break_type = f"{direction} Elephant"
        elif is_tail:
            # Tail direction depends on wick
            upper_wick = last['high'] - max(last['open'], last['close'])
            lower_wick = min(last['open'], last['close']) - last['low']
            
            # Bull Tail = Rejection of lows (Long lower wick)
            if lower_wick > upper_wick:
                break_type = "Bull Tail"
            elif upper_wick > lower_wick:
                break_type = "Bear Tail"

    # 3. SWEEP
    recent_high = df['high'].iloc[-21:-1].max()
    recent_low = df['low'].iloc[-21:-1].min()
    
    sweep = "None"
    if last['low'] < recent_low and last['close'] > recent_low: sweep = "Bull Sweep"
    if last['high'] > recent_high and last['close'] < recent_high: sweep = "Bear Sweep"

    return {
        "sqz": current_sqz,
        "break": break_type,
        "sweep": sweep
    }

def get_coin_data(symbol, exchange):
    try:
        tf_res = {}
        for tf in ['3m', '5m', '15m']:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=300)
            if len(ohlcv) < 200: return None
            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            df = get_squeeze(df)
            tf_res[tf] = analyze_timeframe(df)
            
        # ALIGNMENT
        # Only if SAME type on all 3
        s3 = tf_res['3m']['sqz']
        s5 = tf_res['5m']['sqz']
        s15 = tf_res['15m']['sqz']
        
        align = "None"
        align_type = "None"
        if s3 != "None" and s3 == s5 == s15:
            align = "YES"
            align_type = s3
            
        # Has break flag for row highlighting
        has_break = (tf_res['3m']['break'] != "None") or (tf_res['5m']['break'] != "None") or (tf_res['15m']['break'] != "None")

        return {
            "coin": symbol.replace("/USDT", ""),
            "tf_3m": tf_res['3m'],
            "tf_5m": tf_res['5m'],
            "tf_15m": tf_res['15m'],
            "alignment": align,
            "alignment_type": align_type,
            "has_break": has_break
        }
    except Exception as e:
        print(f"Error {symbol}: {e}")
    return None

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
                res = get_coin_data(sym, exchange)
                if res: results.append(res)
            
            # Sort by Break presence then Alignment
            results.sort(key=lambda x: (x['has_break'], x['alignment']!="None"), reverse=True)
            
            DATA_CACHE = {
                "last_update": time.strftime("%H:%M:%S"), 
                "results": results,
                "exchange": ex_name
            }
            time.sleep(45)
        except Exception as e:
            print(f"Error: {e}")
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
