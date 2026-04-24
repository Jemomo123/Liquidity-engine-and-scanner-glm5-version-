import ccxt
import pandas as pd
import numpy as np
import flask
from flask import Flask, jsonify, render_template_string
import threading
import time
import os
from ta.volatility import BollingerBands, KeltnerChannel

# --- CONFIGURATION ---
app = Flask(__name__)
# Global storage for scan results
DATA_CACHE = {"last_update": "Waiting...", "results": []}

# --- HTML TEMPLATE (Embedded for simplicity) ---
# This creates the mobile-friendly view without a separate file
HTML_CODE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SQZ Scanner</title>
    <style>
        body { font-family: sans-serif; background: #0b0e11; color: #d1d4dc; margin: 0; padding: 15px; }
        h1 { font-size: 20px; color: #f0b90b; text-align: center; }
        .status { text-align: center; font-size: 12px; color: #666; margin-bottom: 20px; }
        .card { background: #1e2329; border-radius: 6px; padding: 12px; margin-bottom: 12px; border-left: 4px solid #f0b90b; }
        .header { display: flex; justify-content: space-between; font-weight: bold; font-size: 16px; margin-bottom: 5px; }
        .tf { background: #2b3139; padding: 2px 6px; border-radius: 4px; font-size: 11px; }
        .mega { border-left: 4px solid #ff4d4d; }
        .triple { border-left: 4px solid #2962ff; }
        .section { margin-top: 8px; font-size: 13px; }
        .label { color: #787b86; font-size: 11px; text-transform: uppercase; }
        .sweep { background: #2b3139; padding: 5px; border-radius: 4px; margin-top: 5px; }
        .bull { color: #0ecb81; } .bear { color: #f6465d; }
        .entry { background: #f0b90b; color: #000; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; margin-top: 5px; display: inline-block; }
    </style>
</head>
<body>
    <h1>📊 SQZ & Liquidity Scanner</h1>
    <div id="time" class="status">Loading...</div>
    <div id="list"></div>
    <script>
        async function load() {
            const r = await fetch('/data');
            const d = await r.json();
            document.getElementById('time').innerText = "Updated: " + d.last_update;
            const list = document.getElementById('list');
            list.innerHTML = '';
            
            if(d.results.length === 0) list.innerHTML = '<div style="text-align:center">No setups found.</div>';

            d.results.forEach(item => {
                let c = 'card';
                if(item.is_mega) c += ' mega';
                if(item.is_triple) c += ' triple';

                let sweep_html = '';
                if(item.sweep_type !== 'None') {
                    let color = item.sweep_type.includes('Bull') ? 'bull' : 'bear';
                    sweep_html = `<div class="sweep"><span class="${color}">${item.sweep_type}</span> detected</div>`;
                }

                let entry_html = item.has_entry ? `<div class="entry">${item.entry_type}</div>` : '';

                list.innerHTML += `
                    <div class="${c}">
                        <div class="header">
                            <span>${item.symbol}</span>
                            <span class="tf">${item.timeframe}</span>
                        </div>
                        <div class="section">
                            <span class="label">Setup:</span> ${item.sqz_type}
                            ${sweep_html}
                            ${entry_html}
                        </div>
                    </div>`;
            });
        }
        setInterval(load, 10000);
        load();
    </script>
</body>
</html>
"""

# --- SCANNER LOGIC ---

def calculate_indicators(df):
    # Bollinger Bands & Keltner Channels for Volatility Squeeze
    bb = BollingerBands(close=df['close'], window=20, window_dev=2)
    kc = KeltnerChannel(high=df['high'], low=df['low'], close=df['close'], window=20)
    df['bb_lower'] = bb.bollinger_lband()
    df['bb_upper'] = bb.bollinger_hband()
    df['kc_lower'] = kc.keltner_channel_lband()
    df['kc_upper'] = kc.keltner_channel_hband()
    
    # SMAs
    df['sma_20'] = df['close'].rolling(20).mean()
    df['sma_100'] = df['close'].rolling(100).mean()
    df['sma_200'] = df['close'].rolling(200).mean()
    
    # ATR for Candle Sizing
    df['atr'] = df['high'].rolling(14).max() - df['low'].rolling(14).min() # Simplified ATR proxy for speed
    return df

def analyze_symbol(symbol, timeframe, exchange):
    try:
        # Fetch 300 candles to ensure we have enough for SMA 200
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=300)
        df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        df = calculate_indicators(df)
        
        # We look at the last candle
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 1. VOLATILITY SQZ (BB inside KC)
        vol_sqz = (last['bb_lower'] > last['kc_lower']) and (last['bb_upper'] < last['kc_upper'])

        # 2. "ALL TOGETHER" SQZ (Price + 20 + 100 within 0.1%)
        # We calculate the range of these 3 values relative to price
        vals_at = [last['high'], last['low'], last['sma_20'], last['sma_100']]
        range_at = max(vals_at) - min(vals_at)
        all_together = (range_at <= last['close'] * 0.001)

        # 3. "SPECIAL ONE" SQZ (Price + 20 + 100 + 200 within 0.1%)
        vals_sp = [last['high'], last['low'], last['sma_20'], last['sma_100'], last['sma_200']]
        range_sp = max(vals_sp) - min(vals_sp)
        special_one = (range_sp <= last['close'] * 0.001)

        # DETERMINE SQZ TYPE
        sqz_type = "None"
        is_mega = False
        
        if special_one: sqz_type = "Special One"
        if all_together: sqz_type = "All Together"
        if vol_sqz: sqz_type = "Volatility"
        
        # Check for MEGA (Volatility + SMA converge)
        if vol_sqz and (special_one or all_together):
            sqz_type = "MEGA SQZ 🔥"
            is_mega = True

        # 4. LIQUIDITY SWEEPS
        # Look back 20 candles for high/low
        recent_high = df['high'].iloc[-21:-1].max()
        recent_low = df['low'].iloc[-21:-1].min()
        
        sweep_type = "None"
        # Bull Sweep: Price dips below recent low but closes back above
        if last['low'] < recent_low and last['close'] > recent_low:
            sweep_type = "Bull Sweep"
        # Bear Sweep: Price spikes above recent high but closes back below
        if last['high'] > recent_high and last['close'] < recent_high:
            sweep_type = "Bear Sweep"

        # 5. ENTRY CONFIRMATION (Elephant/Tail Bar)
        body = abs(last['close'] - last['open'])
        range_c = last['high'] - last['low']
        avg_body = df['close'].diff().abs().rolling(10).mean().iloc[-1] # Avg movement
        
        has_entry = False
        entry_type = ""
        
        # Elephant: Big body candle (breakout)
        if body > (avg_body * 1.5):
            has_entry = True
            entry_type = "Elephant Bar 🐘"
        # Tail: Small body, big wick (rejection)
        elif body < (range_c * 0.4) and range_c > 0:
            has_entry = True
            entry_type = "Tail Bar 🦊"

        # Only return if we have something interesting
        if sqz_type != "None" or sweep_type != "None" or has_entry:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "sqz_type": sqz_type,
                "is_mega": is_mega,
                "sweep_type": sweep_type,
                "has_entry": has_entry,
                "entry_type": entry_type,
                "price": last['close']
            }
    except Exception as e:
        print(f"Error {symbol}: {e}")
    return None

def scanner_job():
    global DATA_CACHE
    exchange = ccxt.binanceusdm({'enableRateLimit': True})
    # Top 30 pairs for speed on free hosting, change to 100 if on paid
    markets = [m for m in exchange.markets if 'USDT' in m and exchange.markets[m]['active']][:30]
    
    while True:
        print("Scanning...")
        all_results = []
        
        # Scan Timeframes
        for tf in ['3m', '5m', '15m']:
            for sym in markets:
                res = analyze_symbol(sym, tf, exchange)
                if res:
                    all_results.append(res)
        
        # Sort results: Mega first, then Entry
        all_results.sort(key=lambda x: (x['is_mega'], x['has_entry']), reverse=True)
        
        DATA_CACHE = {
            "last_update": time.strftime("%H:%M:%S"),
            "results": all_results
        }
        print(f"Scan done. Found {len(all_results)} setups.")
        time.sleep(60) # Scan every 60 seconds

# --- FLASK ROUTES ---

@app.route('/')
def home():
    return render_template_string(HTML_CODE)

@app.route('/data')
def data():
    return jsonify(DATA_CACHE)

if __name__ == '__main__':
    # Start background scanner thread
    t = threading.Thread(target=scanner_job)
    t.daemon = True
    t.start()
    
    # Start Web Server
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
