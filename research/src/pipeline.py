"""
Project Atlas - Feature Engineering Pipeline
===============================================
Central pipeline that:
    1. Connects to DuckDB
    2. Loads raw OHLCV data
    3. Applies all feature engineering (technical, volatility, statistical)
    4. Saves the enriched dataset back to DuckDB
    
This is the main entry point for transforming raw market data into
analysis-ready features.
"""

import sys
import os
import duckdb
import pandas as pd

# Add the src directory to path so we can import our features package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from features.technical import calculate_sma, calculate_ema, calculate_rsi, calculate_macd
from features.volatility import (
    calculate_atr, calculate_bollinger_bands, calculate_historical_volatility,
    calculate_garman_klass_volatility, calculate_yang_zhang_volatility
)
from features.statistical import (
    calculate_log_returns, calculate_pct_returns, calculate_rolling_volatility,
    calculate_z_score, calculate_volume_features
)


def apply_all_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply the full suite of feature engineering to a single stock's DataFrame.
    
    This function orchestrates all indicator calculations in the correct order.
    
    Args:
        df: DataFrame with columns: date, open, high, low, close, volume.
            Must be sorted by date ascending.
    
    Returns:
        DataFrame with all feature columns appended.
    """
    # Ensure sorted by date
    df = df.sort_values('date').reset_index(drop=True)
    
    # ===== TREND & MOMENTUM =====
    df = calculate_sma(df, periods=[20, 50, 200])
    df = calculate_ema(df, periods=[12, 26, 50])
    df = calculate_rsi(df, period=14)
    df = calculate_macd(df)
    
    # ===== VOLATILITY =====
    df = calculate_atr(df, period=14)
    df = calculate_bollinger_bands(df, period=20)
    df = calculate_historical_volatility(df, period=20)
    df = calculate_garman_klass_volatility(df, period=20)
    df = calculate_yang_zhang_volatility(df, period=20)
    
    # ===== STATISTICAL =====
    df = calculate_log_returns(df)
    df = calculate_pct_returns(df, periods=[1, 5, 10, 20])
    df = calculate_rolling_volatility(df, periods=[10, 20, 60])
    df = calculate_z_score(df, period=20)
    df = calculate_volume_features(df, periods=[10, 20])
    
    return df


def run_pipeline(db_path: str):
    """
    Run the full feature engineering pipeline.
    
    Reads raw data from the 'daily_candles' table, applies all features
    per symbol, and writes the enriched data to an 'enriched_candles' table.
    
    Args:
        db_path: Path to the DuckDB database file.
    """
    print("=" * 60)
    print("Project Atlas: Feature Engineering Pipeline")
    print("=" * 60)
    
    con = duckdb.connect(db_path)
    
    # Get list of symbols
    symbols = con.execute("SELECT DISTINCT symbol FROM daily_candles ORDER BY symbol").fetchall()
    symbols = [s[0] for s in symbols]
    
    print(f"\nFound {len(symbols)} symbols: {', '.join(symbols)}")
    
    all_enriched = []
    
    for symbol in symbols:
        print(f"\n  Processing {symbol}...")
        
        # Load raw data for this symbol
        df = con.execute(
            "SELECT * FROM daily_candles WHERE symbol = ? ORDER BY date", 
            [symbol]
        ).df()
        
        print(f"    Raw data: {len(df)} rows ({df['date'].min()} to {df['date'].max()})")
        
        # Apply all features
        df = apply_all_features(df)
        
        # Count NaN rows (from lookback windows) and report
        # The longest lookback is SMA 200, so first ~200 rows will have some NaNs
        nan_count = df['sma_200'].isna().sum()
        valid_count = len(df) - nan_count
        print(f"    Features applied: {len(df.columns)} columns")
        print(f"    Valid rows (after longest lookback): {valid_count}")
        
        all_enriched.append(df)
    
    # Combine all symbols
    enriched_df = pd.concat(all_enriched, ignore_index=True)
    
    # Drop the old enriched table if it exists, and create a new one
    con.execute("DROP TABLE IF EXISTS enriched_candles")
    con.execute("CREATE TABLE enriched_candles AS SELECT * FROM enriched_df")
    
    # Summary
    result = con.execute("""
        SELECT symbol, 
               COUNT(*) as total_rows,
               COUNT(sma_200) as valid_rows_sma200,
               COUNT(yz_vol_20) as valid_rows_yz_vol,
               MIN(date) as start_date, 
               MAX(date) as end_date
        FROM enriched_candles 
        GROUP BY symbol
    """).df()
    
    print("\n" + "=" * 60)
    print("Pipeline Summary (enriched_candles table)")
    print("=" * 60)
    print(result.to_string(index=False))
    
    # Show column names
    cols = con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'enriched_candles'").fetchall()
    col_names = [c[0] for c in cols]
    print(f"\nTotal feature columns: {len(col_names)}")
    print(f"Columns: {', '.join(col_names)}")
    
    con.close()
    print("\nPipeline completed successfully!")


if __name__ == "__main__":
    DB_PATH = "data/market_data.duckdb"
    
    # Check if running from project root
    if os.path.exists("research/data"):
        DB_PATH = "research/data/market_data.duckdb"
    
    run_pipeline(DB_PATH)
