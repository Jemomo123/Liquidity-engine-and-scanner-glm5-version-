import streamlit as st
import pandas as pd
import numpy as np
import ccxt
import time

# --- Configuration ---
# The order matters: Binance first, then fallbacks
EXCHANGE_QUEUE = [
    {'id': 'binanceusdm', 'name': 'Binance Futures', 'type': 'swap'},
    {'id': 'okx', 'name': 'OKX', 'type': 'swap'},
    {'id': 'bybit', 'name': 'Bybit', 'type': 'linear'},
    {'id': 'gateio', 'name': 'Gate.io', 'type': 'swap'},
    {'id': 'mexc', 'name': 'MEXC', 'type': 'swap'}
]

TIMEFRAMES = {
    "3m": "3m",
    "5m": "5m", 
    "15m": "15m"
}

# --- Indicator Logic ---

def calculate_sma(data, length):
    return data['Close'].rolling(window=length).mean()

def calculate_ema(data, length):
    return data['Close'].ewm(span=length, adjust=False).mean()

def calculate_atr(data, length):
    high = data['High']
    low = data['Low']
    close = data['Close'].shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - close), abs(low - close)))
    return tr.rolling(window=length).mean()

def calculate_bb(data, length=20, mult=2.0):
    basis = calculate_sma(data, length)
    stdev = data['Close'].rolling(window=length).std()
    upper = basis + (stdev * mult)
    lower = basis - (stdev * mult)
    return upper, lower

def calculate_kc(data, length=20, mult=1.5):
    basis = calculate_ema(data, length)
    atr = calculate_atr(data, length)
    upper = basis + (atr * mult)
    lower = basis - (atr * mult)
    return upper, lower

def check_bb_sqz(data):
    bb_upper, bb_lower = calculate_bb(data)
    kc_upper, kc_lower = calculate_kc(data)
    return (bb_upper < kc_upper) & (bb_lower > kc_lower)

def check_sma_sqz(data, len1=20, len2=100):
    sma1 = calculate_sma(data, len1)
    sma2 = calculate_sma(data, len2)
    highest_ma = np.maximum(sma1, sma2)
    lowest_ma = np.minimum(sma1, sma2)
    return (data['High'] >= lowest_ma) & (data['Low'] <= highest_ma)

# --- Robust Data Fetching with Failover ---

def get_crypto_data_robust(symbol, timeframe):
    """
    Tries exchanges in order until one works.
    Returns: (DataFrame, ExchangeName) or (None, None)
    """
    last_error = None
    
    for ex_config in EXCHANGE_QUEUE:
        try:
            # Dynamically initialize the exchange
            exchange_class = getattr(ccxt, ex_config['id'])
            
            # Specific config for different exchanges
            options = {
                'enableRateLimit': True,
                'options': {'defaultType': ex_config['type']}
            }
            
            # Special case for Binance to set type correctly
            if ex_config['id'] == 'binanceusdm':
                 options['options'] = {'defaultType': 'future'}

            exchange = exchange_class(options)
            
            # Attempt to fetch
            # CCXT unified symbol format usually works (e.g., BTC/USDT:USDT)
            bars = exchange.fetch_ohlcv(symbol, timeframe, limit=500)
            
            if bars and len(bars) > 0:
                df = pd.DataFrame(bars, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
                df.set_index('Timestamp', inplace=True)
                return df, ex_config['name']

        except Exception as e:
            # Log error internally and try next
            last_error = e
            continue
            
    # If all fail
    return None, None

# --- Main Application ---

def main():
    st.set_page_config(page_title="Crypto Futures SQZ Scanner", layout="wide")
    st.title("📊 Crypto Futures SQZ Scanner (Multi-Exchange Failover)")
    
    st.markdown("""
    Scanning for **BB SQZ** and **SMA SQZ**. 
    **System:** Automatically switches between Binance, OKX, Bybit, Gate.io, and MEXC if an exchange is blocked.
    """)
    
    # Sidebar
    st.sidebar.header("Settings")
    
    default_tickers = "BTC/USDT:USDT, ETH/USDT:USDT, SOL/USDT:USDT, XRP/USDT:USDT, DOGE/USDT:USDT, 1000PEPE/USDT:USDT"
    tickers_input = st.sidebar.text_area("Enter Tickers (CCXT Format)", value=default_tickers, height=150)
    
    if st.button("🚀 Run Scan"):
        tickers = [t.strip() for t in tickers_input.split(",") if t.strip()]
        
        results = []
        status_messages = []
        progress_bar = st.progress(0)
        
        for i, ticker in enumerate(tickers):
            progress_bar.progress((i + 1) / len(tickers), text=f"Scanning {ticker}...")
            
            row_data = {"Ticker": ticker}
            data_source = None
            
            # Check each timeframe
            for tf_name, tf_val in TIMEFRAMES.items():
                
                # Use the Robust Fetcher
                df, source = get_crypto_data_robust(ticker, tf_val)
                
                if df is not None and len(df) > 100:
                    if data_source is None:
                        data_source = source
                    
                    # Calculate Signals
                    bb_sqz = check_bb_sqz(df)
                    sma_sqz = check_sma_sqz(df)
                    
                    # Get last candle
                    is_bb = bb_sqz.iloc[-1]
                    is_sma = sma_sqz.iloc[-1]
                    
                    row_data[f"BB ({tf_name})"] = "✅" if is_bb else ""
                    row_data[f"SMA ({tf_name})"] = "✅" if is_sma else ""
                    
                    # Count
                    if is_bb: row_data["BB Count"] = row_data.get("BB Count", 0) + 1
                    if is_sma: row_data["SMA Count"] = row_data.get("SMA Count", 0) + 1
                else:
                    row_data[f"BB ({tf_name})"] = "⚠️"
                    row_data[f"SMA ({tf_name})"] = "⚠️"
            
            # Store which exchange worked for this ticker
            row_data["Source"] = data_source if data_source else "FAILED"
            results.append(row_data)

        progress_bar.empty()
        
        if results:
            df_results = pd.DataFrame(results)
            
            # Reorder columns
            cols = ["Ticker", "Source", "BB Count", "SMA Count"]
            for tf in TIMEFRAMES.keys():
                cols.append(f"BB ({tf})")
                cols.append(f"SMA ({tf})")
            
            df_results = df_results.reindex(columns=cols)
            
            # Highlighting
            def color_cells(val):
                if "✅" in str(val):
                    return 'background-color: #d4edda; color: black'
                return ''
            
            st.dataframe(df_results.style.applymap(color_cells), use_container_width=True)
            
            st.caption("Source column shows which exchange provided the data (Failover active).")
        else:
            st.error("No data found for any ticker across all exchanges. Check symbol format.")

if __name__ == "__main__":
    main()
