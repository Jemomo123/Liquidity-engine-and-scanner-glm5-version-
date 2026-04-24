import ccxt
import pandas as pd
import numpy as np
import flask
from flask import Flask, jsonify, render_template_string
import threading
import time
import os
import random

# --- CONFIG ---
app = Flask(__name__)
DATA_CACHE = {"last_update": "Initializing...", "results": [], "exchange": "None"}

# --- YOUR WATCHLIST ---
# We use "BASE/USDT" format which is standard for all exchanges
RAW_WATCHLIST = ["BTC", "ETH", "SOL", "WIF", "SPX", "PEOPLE", "SPACE", "DOGE", "LINEA"]
WATCHLIST = [s + "/USDT" for s in RAW_WATCHLIST]

# --- EXCHANGE SETUP (FAILOVER) ---
# List of exchanges to try in order
EXCHANGE_CLASSES = [
    {'name': 'Binance', 'class': ccxt.binanceusdm},
    {'name': 'Bybit', 'class': ccxt.bybit},
    {'name': 'OKX', 'class': ccxt.okx}
]

def get_exchange_connection():
    """
    Tries to connect to exchanges in order.
    Returns a connected exchange instance or None.
    """
    for ex_info in EXCHANGE_CLASSES:
        try:
            # Create instance
            ex = ex_info['class']({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'} # Ensure futures mode
            })
            # Load markets to verify connection
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
        .ex-badge { background: #2b3139; color: #0ecb81; padding: 2px 6px; border-radius: 4px; font-size: 10px; margin-left: 5px; }
        
        /* Layout */
        .card { background: #1e2329; border-radius: 6px; padding: 10px; margin-bottom: 10px; border-left: 4px solid #2b3139; }
        .card.mega { border-left: 4px solid #ff4d4d; }
        .card.entry { border-left: 4px solid #f0b90b; }
        
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
                    if(item.is_mega) c += ' mega';
                    else if(item.has_entry) c += ' entry';

                    let sweep_html = `<span class="sweep-none">None</span>`;
                    if(item.sweep_type !== 'None') {
                        let color = item.sweep_type.includes('Bull') ? 'sweep-bull' : 'sweep-bear';
                        sweep_html = `<span class="sweep-val ${color}">${item.sweep_type}</span>`;
                    }

                    let entry_html = item.has_entry ? `<span class="entry-tag">${item.entry_type}</span>` : '';

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
                            <div><span class="sqz-tag">${item.sqz_type}</span></div>
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
    
    return df

def analyze_symbol(symbol, timeframe, exchange):
    try:
        # Fetch 300 candles
        # CCXT standard symbol format: BTC/USDT
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=300)
        if len(ohlcv) < 200: return None
        
        df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        df = get_squeeze(df)
        
        last = df.iloc[-1]
        
        # --- SQUEEZE LOGIC ---
        vol_sqz = (last['bb_lower'] > last['kc_lower']) and (last['bb_upper'] < last['kc_upper'])

        vals_at = [last['high'], last['low'], last['sma_20'], last['sma_100']]
        range_at = max(vals_at) - min(vals_at)
        all_together = (range_at <= last['close'] * 0.001)

        vals_sp = [last['high'], last['low'], last['sma_20'], last['sma_100'], last['sma_200']]
        range_sp = max(vals_sp) - min(vals_sp)
        special_one = (range_sp <= last['close'] * 0.001)

        sqz_type = "None"
        is_mega = False
        
        if special_one: sqz_type = "Special One"
        if all_together: sqz_type = "All Together"
        if vol_sqz: sqz_type = "Volatility"
        
        if vol_sqz and (special_one or all_together):
            sqz_type = "MEGA SQZ 🔥"
            is_mega = True

        # --- LIQUIDITY SWEEPS ---
        recent_high = df['high'].iloc[-21:-1].max()
        recent_low = df['low'].iloc[-21:-1].min()
        
        sweep_type = "None"
        if last['low'] < recent_low and last['close'] > recent_low: sweep_type = "Bull Sweep"
        if last['high'] > recent_high and last['close'] < recent_high: sweep_type = "Bear Sweep"

        # --- ENTRY CONFIRMATION ---
        body = abs(last['close'] - last['open'])
        range_c = last['high'] - last['low']
        avg_body = df['close'].diff().abs().rolling(10).mean().iloc[-1]
        
        has_entry = False
        entry_type = ""
        
        if body > (avg_body * 1.5):
            has_entry = True
            entry_type = "Elephant 🐘"
        elif body < (range_c * 0.4) and range_c > 0:
            has_entry = True
            entry_type = "Tail 🦊"

        if sqz_type != "None" or sweep_type != "None" or has_entry:
            # Clean symbol name for display (remove /USDT)
            clean_name = symbol.replace("/USDT", "")
            return {
                "symbol": clean_name,
                "timeframe": timeframe,
                "sqz_type": sqz_type,
                "is_mega": is_mega,
                "sweep_type": sweep_type,
                "has_entry": has_entry,
                "entry_type": entry_type
            }
    except Exception as e:
        # If this specific symbol fails, return None
        return None
    return None

# --- SCANNER THREAD ---
def scanner_loop():
    global DATA_CACHE
    
    # Initial connection
    exchange, ex_name = get_exchange_connection()
    
    while True:
        # 1. Check if we have a valid exchange connection
        if exchange is None:
            print("All exchanges down. Retrying in 30s...")
            time.sleep(30)
            exchange, ex_name = get_exchange_connection()
            continue

        try:
            results = []
            print(f"Scanning using {ex_name}...")
            
            for tf in ['3m', '5m', '15m']:
                for sym in WATCHLIST:
                    res = analyze_symbol(sym, tf, exchange)
                    if res: results.append(res)
            
            results.sort(key=lambda x: (x['is_mega'], x['has_entry']), reverse=True)
            
            DATA_CACHE = {
                "last_update": time.strftime("%H:%M:%S"), 
                "results": results,
                "exchange": ex_name
            }
            print(f"Scan Complete: {len(results)} setups.")
            time.sleep(45) # Scan every 45 seconds

        except Exception as e:
            # 2. FAILOVER TRIGGER
            print(f"Error on {ex_name}: {e}. Switching exchange...")
            # Force reconnect to NEXT exchange in list
            # We shift the list in get_exchange_connection naturally by luck/random, 
            # or we just try to get a new one (logic inside get_exchange_connection tries all)
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
