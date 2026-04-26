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
    <title>SQZ Scanner</title>
    <style>
        body { font-family: sans-serif; background: #0b0e11; color: #d1d4dc; margin: 0; padding: 15px; }
        h1 { font-size: 20px; color: #f0b90b; text-align: center; margin-bottom: 5px; }
        .status { text-align: center; font-size: 12px; color: #666; margin-bottom: 20px; }
        
        /* Layout */
        .card { background: #1e2329; border-radius: 6px; padding: 10px; margin-bottom: 10px; border-left: 4px solid #2b3139; }
        .card.mega { border-left: 4px solid #ff4d4d; }
        .card.entry { border-left: 4px solid #f0b90b; }
        .card.triple { border-left: 4px solid #ff0000; background: #2a1515; } /* DARK RED FOR TRIPLE */
        
        .row { display: flex; justify-content: space-between; align-items: center; width: 100%; }
        .col { display: flex; flex-direction: column; }
        
        .symbol { font-weight: bold; font-size: 18px; color: #fff; }
        .tf { font-size: 12px; color: #848e9c; margin-top: 2px; }
        
        .sweep-box { text-align: right; }
        .sweep-val { font-weight: bold; font-size: 14px; }
        .sweep-bull { color: #0ecb81; }
        .sweep-bear { color: #f6465d; }
        .sweep-none { color: #848e9c; font-size: 12px; }
        
        .details { margin-top: 8px; padding-top: 8px; border-top: 1px solid #2b3139; font-size: 13px; display: flex; justify-content: space-between; }
        .sqz-tag { background: #2b3139; padding: 2px 6px; border-radius: 4px; color: #d1d4dc; }
        .entry-tag { background: #f0b90b; color: #000; padding: 2px 6px; border-radius: 4px; font-weight: bold; }
        
        /* Triple Aligned Special Text */
        .triple-text { color: #ff4d4d; font-weight: bold; text-transform: uppercase; }
    </style>
</head>
<body>
    <h1>📊 Futures SQZ Scanner</h1>
    <div id="time" class="status">Connecting...</div>
    <div id="list"></div>
    <script>
        async function load() {
            try {
                const r = await fetch('/data');
                const d = await r.json();
                const ex_name = d.exchange || 'N/A';
                document.getElementById('time').innerText = "Last Update: " + d.last_update + " | Exchange: " + ex_name;
                const list = document.getElementById('list');
                list.innerHTML = '';
                
                if(d.results.length === 0) list.innerHTML = '<div style="text-align:center; padding:20px; color:#666;">Scanning markets...</div>';

                d.results.forEach(item => {
                    let c = 'card';
                    // Priority: Triple > Mega > Entry
                    if(item.is_triple) c += ' triple';
                    else if(item.is_mega) c += ' mega';
                    else if(item.has_entry) c += ' entry';

                    let sweep_html = `<span class="sweep-none">None</span>`;
                    if(item.sweep_type !== 'None') {
                        let color = item.sweep_type.includes('Bull') ? 'sweep-bull' : 'sweep-bear';
                        sweep_html = `<span class="sweep-val ${color}">${item.sweep_type}</span>`;
                    }

                    let entry_html = item.has_entry ? `<span class="entry-tag">${item.entry_type}</span>` : '';
                    
                    // Special display for Triple Aligned
                    let sqz_display = item.sqz_type;
                    if(item.is_triple) {
                        sqz_display = `<span class="triple-text">Triple Aligned 🔥</span>`;
                    }

                    list.innerHTML += `
                    <div class="${c}">
                        <div class="row">
                            <div class="col">
                                <span class="symbol">${item.symbol}</span>
                                <span class="tf">${item.timeframe} Timeframe</span>
                            </div>
                            <div class="col sweep-box">
                                <span class="sweep-none" style="font-size:10px;">SWEEP</span>
                                ${sweep_html}
                            </div>
                        </div>
                        <div class="details">
                            <div><span class="sqz-tag">${sqz_display}</span></div>
                            <div>${entry_html}</div>
                        </div>
                    </div>`;
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
    # Bollinger Bands (20, 2)
    df['sma_20'] = df['close'].rolling(20).mean()
    df['std_20'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['sma_20'] + (df['std_20'] * 2)
    df['bb_lower'] = df['sma_20'] - (df['std_20'] * 2)

    # Keltner Channels (20, 1.5 ATR)
    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift()), abs(df['low'] - df['close'].shift())))
    df['atr'] = df['tr'].rolling(20).mean()
    df['kc_upper'] = df['sma_20'] + (df['atr'] * 1.5)
    df['kc_lower'] = df['sma_20'] - (df['atr'] * 1.5)

    # SMAs
    df['sma_100'] = df['close'].rolling(100).mean()
    df['sma_200'] = df['close'].rolling(200).mean()
    
    # Helper to identify SQZ on each row (for lookback)
    # We calculate the boolean condition row-wise
    df['vol_sqz_check'] = (df['bb_lower'] > df['kc_lower']) & (df['bb_upper'] < df['kc_upper'])
    
    return df

def analyze_symbol(symbol, timeframe, exchange):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=300)
        if len(ohlcv) < 200: return None
        
        df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        df = get_squeeze(df)
        
        last = df.iloc[-1]
        
        # --- 1. SQUEEZE LOGIC ---
        vol_sqz = (last['bb_lower'] > last['kc_lower']) and (last['bb_upper'] < last['kc_upper'])

        vals_at = [last['high'], last['low'], last['sma_20'], last['sma_100']]
        range_at = max(vals_at) - min(vals_at)
        all_together = (range_at <= last['close'] * 0.001)

        vals_sp = [last['high'], last['low'], last['sma_20'], last['sma_100'], last['sma_200']]
        range_sp = max(vals_sp) - min(vals_sp)
        special_one = (range_sp <= last['close'] * 0.001)

        sqz_type = "None"
        is_mega = False
        is_sqz_active = False
        
        if special_one: sqz_type = "Special One"
        if all_together: sqz_type = "All Together"
        if vol_sqz: sqz_type = "Volatility"
        
        if vol_sqz or all_together or special_one:
            is_sqz_active = True

        if vol_sqz and (special_one or all_together):
            sqz_type = "MEGA SQZ 🔥"
            is_mega = True

        # --- 2. LIQUIDITY SWEEPS ---
        recent_high = df['high'].iloc[-21:-1].max()
        recent_low = df['low'].iloc[-21:-1].min()
        
        sweep_type = "None"
        if last['low'] < recent_low and last['close'] > recent_low: sweep_type = "Bull Sweep"
        if last['high'] > recent_high and last['close'] < recent_high: sweep_type = "Bear Sweep"

        # --- 3. ENTRY CONFIRMATION (UPDATED WITH SIZE FILTER) ---
        # Calculate current candle range
        current_range = last['high'] - last['low']
        body = abs(last['close'] - last['open'])
        
        # Calculate average SQZ size
        # Look at last 10 candles. Filter only those that were SQZ.
        # If no SQZ in last 10, fallback to average range of last 10.
        recent_candles = df.iloc[-11:-1] # Exclude current candle
        sqz_candles = recent_candles[recent_candles['vol_sqz_check'] == True]
        
        if len(sqz_candles) > 0:
            avg_sqz_range = (sqz_candles['high'] - sqz_candles['low']).mean()
        else:
            # Fallback if no sqz detected in lookback (using simple average range)
            avg_sqz_range = (recent_candles['high'] - recent_candles['low']).mean()

        # Elephant: Body > 1.5x avg_body AND Range >= Avg_SQZ_Range
        avg_body = df['close'].diff().abs().rolling(10).mean().iloc[-1]
        
        has_entry = False
        entry_type = ""
        
        # RULE: Entry must be >= 1x size of SQZ candles
        is_valid_size = (current_range >= avg_sqz_range)

        if is_valid_size:
            if body > (avg_body * 1.5):
                has_entry = True
                entry_type = "Elephant 🐘"
            elif body < (current_range * 0.4) and current_range > 0:
                has_entry = True
                entry_type = "Tail 🦊"

        # Return data structure
        # We only return valid setups to the main loop
        if is_sqz_active or sweep_type != "None" or has_entry:
            clean_name = symbol.replace("/USDT", "")
            return {
                "symbol": clean_name,
                "timeframe": timeframe,
                "sqz_type": sqz_type,
                "is_mega": is_mega,
                "is_sqz_active": is_sqz_active, # Needed for Triple Check
                "sweep_type": sweep_type,
                "has_entry": has_entry,
                "entry_type": entry_type,
                "is_triple": False # Default, updated in main loop
            }
    except Exception as e:
        return None
    return None

# --- SCANNER THREAD ---
def scanner_loop():
    global DATA_CACHE
    
    exchange, ex_name = get_exchange_connection()
    
    while True:
        if exchange is None:
            print("All exchanges down. Retrying in 30s...")
            time.sleep(30)
            exchange, ex_name = get_exchange_connection()
            continue

        try:
            results = []
            print(f"Scanning using {ex_name}...")
            
            # Dictionary to hold results per symbol to check for Triple Alignment
            # Structure: { "BTC": { "3m": result_obj, "5m": result_obj, ... } }
            symbol_groups = {}

            for tf in ['3m', '5m', '15m']:
                for sym in WATCHLIST:
                    res = analyze_symbol(sym, tf, exchange)
                    
                    if res:
                        # Initialize group if not exists
                        if sym not in symbol_groups:
                            symbol_groups[sym] = {}
                        
                        symbol_groups[sym][tf] = res

            # Process Groups for Triple Alignment
            for sym, tf_dict in symbol_groups.items():
                # Check if we have all 3 timeframes
                has_3m = '3m' in tf_dict and tf_dict['3m']['is_sqz_active']
                has_5m = '5m' in tf_dict and tf_dict['5m']['is_sqz_active']
                has_15m = '15m' in tf_dict and tf_dict['15m']['is_sqz_active']
                
                is_triple = has_3m and has_5m and has_15m
                
                # Add all individual results to the main list
                for tf, res in tf_dict.items():
                    if is_triple:
                        res['is_triple'] = True
                        # Overwrite SQZ type if triple, but keep Entry/Sweep info
                        if res['is_sqz_active']:
                             res['sqz_type'] = "Triple Aligned"
                    
                    results.append(res)

            # Sort: Triple First, then Mega, then Entry
            results.sort(key=lambda x: (
                x['is_triple'], 
                x['is_mega'], 
                x['has_entry']
            ), reverse=True)
            
            DATA_CACHE = {
                "last_update": time.strftime("%H:%M:%S"), 
                "results": results,
                "exchange": ex_name
            }
            print(f"Scan Complete: {len(results)} setups.")
            time.sleep(45) 

        except Exception as e:
            print(f"Error on {ex_name}: {e}. Switching exchange...")
            exchange, ex_name = get_exchange_connection()
            if exchange:
                DATA_CACHE["exchange"] = ex_name + " (Switched)"
            else:
                DATA_CACHE["exchange"] = "All Exchanges Down"

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
