"""
Project Atlas - Synthetic Market Data Generator
=================================================
Generates realistic synthetic OHLCV data for testing feature engineering
pipelines when real API keys are unavailable.

Uses a geometric Brownian motion (GBM) model with realistic parameters
for Indian equity markets.
"""

import pandas as pd
import numpy as np
import duckdb
import os
from datetime import datetime, timedelta


def generate_synthetic_ohlcv(
    symbol: str,
    start_date: str = '2024-01-01',
    end_date: str = '2025-07-31',
    initial_price: float = 2500.0,
    annual_drift: float = 0.12,       # 12% annual return (typical for NIFTY)
    annual_volatility: float = 0.18,  # 18% annual vol (typical for Indian large-cap)
    seed: int = None
) -> pd.DataFrame:
    """
    Generate synthetic daily OHLCV data using Geometric Brownian Motion.
    
    The GBM model:
        dS = μ*S*dt + σ*S*dW
    
    where:
        μ = drift (annual return)
        σ = volatility
        dW = Wiener process (random walk)
    
    We also simulate realistic intraday high/low ranges and volume.
    
    Args:
        symbol: Stock symbol name.
        start_date: Start date string (YYYY-MM-DD).
        end_date: End date string (YYYY-MM-DD).
        initial_price: Starting price of the synthetic stock.
        annual_drift: Expected annual return.
        annual_volatility: Expected annual volatility.
        seed: Random seed for reproducibility.
    
    Returns:
        DataFrame with columns: symbol, date, open, high, low, close, volume.
    """
    if seed is not None:
        np.random.seed(seed)
    
    # Generate business days (exclude weekends, approximate Indian market)
    dates = pd.bdate_range(start=start_date, end=end_date)
    n_days = len(dates)
    
    # Daily parameters
    dt = 1 / 252  # One trading day
    daily_drift = annual_drift * dt
    daily_vol = annual_volatility * np.sqrt(dt)
    
    # Generate close prices using GBM
    # ln(S_t/S_{t-1}) ~ N((μ - σ²/2)*dt, σ*√dt)
    log_returns = np.random.normal(
        loc=(annual_drift - 0.5 * annual_volatility**2) * dt,
        scale=daily_vol,
        size=n_days
    )
    
    close_prices = initial_price * np.exp(np.cumsum(log_returns))
    
    # Generate realistic OHLC from close prices
    # Open: close of previous day + small overnight gap
    overnight_gap = np.random.normal(0, daily_vol * 0.3, size=n_days)
    open_prices = np.roll(close_prices, 1) * (1 + overnight_gap)
    open_prices[0] = initial_price
    
    # Intraday range: typically 1-3% for Indian large caps
    intraday_range = np.random.uniform(0.008, 0.025, size=n_days)
    
    # High and Low relative to the day's range
    high_bias = np.random.uniform(0.3, 0.8, size=n_days)  # Where the high falls in the range
    
    day_midpoint = (open_prices + close_prices) / 2
    half_range = day_midpoint * intraday_range / 2
    
    high_prices = np.maximum(open_prices, close_prices) + half_range * high_bias
    low_prices = np.minimum(open_prices, close_prices) - half_range * (1 - high_bias)
    
    # Ensure OHLC integrity: High >= max(Open, Close), Low <= min(Open, Close)
    high_prices = np.maximum(high_prices, np.maximum(open_prices, close_prices))
    low_prices = np.minimum(low_prices, np.minimum(open_prices, close_prices))
    
    # Generate volume (log-normal distribution, mean ~5M shares for large caps)
    base_volume = 5_000_000
    volume = np.random.lognormal(
        mean=np.log(base_volume), 
        sigma=0.4, 
        size=n_days
    ).astype(int)
    
    # Volume tends to spike on high-volatility days
    vol_spike_factor = 1 + np.abs(log_returns) / daily_vol * 0.5
    volume = (volume * vol_spike_factor).astype(int)
    
    df = pd.DataFrame({
        'symbol': symbol,
        'date': dates[:n_days],
        'open': np.round(open_prices, 2),
        'high': np.round(high_prices, 2),
        'low': np.round(low_prices, 2),
        'close': np.round(close_prices, 2),
        'volume': volume
    })
    
    return df


def generate_and_load_to_duckdb(db_path: str, symbols_config: dict = None):
    """
    Generate synthetic data for multiple symbols and load into DuckDB.
    
    Args:
        db_path: Path to the DuckDB database file.
        symbols_config: Dict mapping symbol names to their config parameters.
            Example: {'RELIANCE': {'initial_price': 2500, 'seed': 42}}
    """
    if symbols_config is None:
        # Default: simulate major NIFTY50 stocks with realistic starting prices
        symbols_config = {
            'RELIANCE':  {'initial_price': 2500.0, 'annual_drift': 0.15, 'annual_volatility': 0.22, 'seed': 42},
            'TCS':       {'initial_price': 3800.0, 'annual_drift': 0.10, 'annual_volatility': 0.16, 'seed': 43},
            'INFY':      {'initial_price': 1500.0, 'annual_drift': 0.12, 'annual_volatility': 0.20, 'seed': 44},
            'HDFCBANK':  {'initial_price': 1600.0, 'annual_drift': 0.14, 'annual_volatility': 0.18, 'seed': 45},
            'ICICIBANK': {'initial_price': 1000.0, 'annual_drift': 0.18, 'annual_volatility': 0.24, 'seed': 46},
        }
    
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = duckdb.connect(db_path)
    
    # Create table
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
    
    total_rows = 0
    for symbol, config in symbols_config.items():
        print(f"  Generating synthetic data for {symbol}...")
        df = generate_synthetic_ohlcv(symbol, **config)
        
        con.execute("""
            INSERT OR REPLACE INTO daily_candles 
            SELECT symbol, date, open, high, low, close, volume FROM df
        """)
        total_rows += len(df)
        print(f"    -> {len(df)} rows loaded.")
    
    print(f"\nTotal: {total_rows} rows loaded into {db_path}")
    
    # Quick sanity check
    result = con.execute("SELECT symbol, COUNT(*) as rows, MIN(date) as start, MAX(date) as end FROM daily_candles GROUP BY symbol").df()
    print("\nDatabase Summary:")
    print(result.to_string(index=False))
    
    con.close()


if __name__ == "__main__":
    DB_PATH = "data/market_data.duckdb"
    
    # Check if running from project root or research dir
    if os.path.exists("research/data"):
        DB_PATH = "research/data/market_data.duckdb"
    
    print("=" * 60)
    print("Project Atlas: Synthetic Data Generator")
    print("=" * 60)
    generate_and_load_to_duckdb(DB_PATH)
    print("\nDone!")
