import duckdb
import yfinance as yf
import pandas as pd
import os
from datetime import datetime, timedelta

def initialize_db(db_path: str):
    """Initializes the DuckDB database and creates necessary tables if they don't exist."""
    print(f"Connecting to DuckDB at {db_path}...")
    
    # Ensure the directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    con = duckdb.connect(db_path)
    
    # Create a table for Indian Equities (using NSE symbols via yfinance, e.g., RELIANCE.NS)
    # We store the standard OHLCV data.
    con.execute("""
        CREATE TABLE IF NOT EXISTS daily_candles (
            symbol VARCHAR,
            date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            PRIMARY KEY (symbol, date)
        )
    """)
    print("Database initialized successfully.")
    return con

def fetch_and_load_data(con, symbol: str, days_back: int = 365):
    """Fetches data from Yahoo Finance and loads it into DuckDB."""
    print(f"Fetching {days_back} days of data for {symbol}...")
    
    end_date = datetime.today()
    start_date = end_date - timedelta(days=days_back)
    
    # yfinance uses .NS for NSE stocks
    yf_symbol = f"{symbol}.NS"
    ticker = yf.Ticker(yf_symbol)
    df = ticker.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
    
    if df.empty:
        print(f"No data found for {symbol}. Check the symbol name.")
        return
        
    # Reset index to make Date a column
    df.reset_index(inplace=True)
    
    # Prepare dataframe for DuckDB (rename columns to match schema, add symbol)
    df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].copy()
    df.rename(columns={
        'Date': 'date',
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume'
    }, inplace=True)
    
    # yfinance returns timezone-aware datetimes, convert to naive date
    df['date'] = df['date'].dt.date
    df['symbol'] = symbol
    
    print(f"Inserting {len(df)} rows into DuckDB...")
    
    # Load into DuckDB. We use INSERT OR IGNORE conceptually, 
    # but DuckDB's primary key conflict handling needs ON CONFLICT
    con.execute("""
        INSERT INTO daily_candles 
        SELECT symbol, date, open, high, low, close, volume FROM df
        ON CONFLICT (symbol, date) DO UPDATE SET 
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume;
    """)
    
    print(f"Data for {symbol} successfully loaded.")

def query_sample(con, symbol: str):
    """Runs a quick sanity check query."""
    print(f"\n--- Sample data for {symbol} ---")
    result = con.execute(f"SELECT * FROM daily_candles WHERE symbol = '{symbol}' ORDER BY date DESC LIMIT 5").df()
    print(result)

if __name__ == "__main__":
    DB_PATH = "../data/market_data.duckdb"
    
    # Use a dummy database path if running from root vs src directory
    if not os.path.exists("../data") and not os.path.exists("./data"):
         # We are likely running from the research folder, so path is data/market_data.duckdb
         DB_PATH = "data/market_data.duckdb"
    elif os.path.exists("research/data"):
         # Running from project root
         DB_PATH = "research/data/market_data.duckdb"
         
    con = initialize_db(DB_PATH)
    
    # Load sample data for Reliance Industries and Tata Consultancy Services
    symbols_to_test = ["RELIANCE", "TCS"]
    
    for sym in symbols_to_test:
        fetch_and_load_data(con, sym, days_back=30)
        query_sample(con, sym)
        
    con.close()
    print("\nDuckDB test completed successfully.")
