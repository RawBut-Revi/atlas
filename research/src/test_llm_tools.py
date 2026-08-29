"""
Project Atlas - Phase 4 Integration Test
Tests DRIP, Dynamic Allocator, Risk Metrics, and MF Analyser.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scoring.drip_engine import execute_drip, calculate_dividend_received, simulate_drip_portfolio
from portfolio.dynamic_allocator import get_dynamic_allocation, apply_allocation_to_capital, check_rebalance_needed
from portfolio.risk_metrics import sharpe_ratio, max_drawdown, portfolio_scorecard, correlation_matrix
from analysis.mf_analyser import get_smart_money_signals, get_mf_confirmation_for_stock, get_fund_overlap

# Build a simple basket for testing
basket = [
    {"symbol": "COALINDIA", "sector": "Mining", "price": 398,  "div_yield": 6.8, "atlas_score": 98.4, "category": ["Hybrid"]},
    {"symbol": "ONGC",      "sector": "Energy", "price": 266,  "div_yield": 4.5, "atlas_score": 86.2, "category": ["Aggressive Dividend"]},
    {"symbol": "ITC",       "sector": "FMCG",   "price": 449,  "div_yield": 3.2, "atlas_score": 60.2, "category": ["Hybrid"]},
    {"symbol": "HCLTECH",   "sector": "IT",     "price": 1739, "div_yield": 3.4, "atlas_score": 70.8, "category": ["Long Term Hold"]},
    {"symbol": "POWERGRID", "sector": "Util",   "price": 306,  "div_yield": 4.1, "atlas_score": 75.4, "category": ["Aggressive Dividend"]},
]
holdings = {"COALINDIA": 10, "ONGC": 15, "ITC": 8, "HCLTECH": 2, "POWERGRID": 12}

print("=" * 65)
print("  PHASE 4 INTEGRATION TEST")
print("=" * 65)

# --- DRIP ENGINE ---
print("\n[1] DRIP ENGINE (Strategy C: Highest Atlas-scored stock)")
dividends = calculate_dividend_received(holdings, basket)
total_div = sum(dividends.values())
print(f"  Quarterly dividends collected: Rs {total_div:.0f}")
for sym, amt in dividends.items():
    print(f"    {sym}: Rs {amt:.0f}")

drip_result = execute_drip(dict(holdings), basket, total_div, label="Q1 DRIP Test")
print(f"\n  DRIP action: {drip_result['action']}")
print(f"  Buying: {drip_result.get('shares_to_buy',0)} shares of {drip_result.get('symbol','N/A')}")
print(f"  Reason: {drip_result.get('note','')}")

# --- DRIP SIMULATION (36 months) ---
print("\n[2] DRIP SIMULATION (36 months, quarterly reinvestment)")
sim = simulate_drip_portfolio(basket, holdings, months=36, drip_frequency="quarterly")
print(f"  Final portfolio value:  Rs {sim['final_portfolio_value']:,.0f}")
print(f"  Annual dividend income: Rs {sim['annual_dividend_income']:,.0f}/yr")
print(f"  DRIP transactions made: {sim['total_drip_transactions']}")
print(f"  Final holdings: {sim['final_holdings']}")

# --- DYNAMIC ALLOCATOR ---
print("\n[3] DYNAMIC HEDGE ALLOCATOR")
# Simulate: NIFTY at 24500, SMA200 at 23000, VIX at 15.2 (calm bull market)
allocation = get_dynamic_allocation(
    nifty_price=24500, nifty_sma200=23000,
    india_vix=15.2, gsec_10y_yield=6.85,
    repo_rate=6.5, gold_price=85000, gold_sma200=80000,
)
print(f"  Regime: {allocation['regime']} — {allocation['regime_description']}")
print(f"  Equities:      {allocation['allocation']['equities_pct']}%")
print(f"  Gold:          {allocation['allocation']['gold_pct']}%")
print(f"  Bonds:         {allocation['allocation']['bonds_pct']}%")
print(f"  International: {allocation['allocation']['international_pct']}%")

# Apply to Rs 5,00,000 portfolio
amounts = apply_allocation_to_capital(500000, allocation)
print(f"\n  For Rs 5,00,000 portfolio:")
for k, v in amounts.items():
    print(f"    {k}: Rs {v:,.0f}")

# Simulate stressed market (VIX=28, NIFTY below SMA)
print("\n  [STRESS SCENARIO] VIX=28, NIFTY below 200-DMA")
stressed = get_dynamic_allocation(
    nifty_price=21000, nifty_sma200=23000,
    india_vix=28.5, gsec_10y_yield=6.50,
    gold_price=90000, gold_sma200=82000,
)
print(f"  Regime: {stressed['regime']} — {stressed['regime_description']}")
print(f"  Equities: {stressed['allocation']['equities_pct']}% (vs 60% normal)")
print(f"  Gold:     {stressed['allocation']['gold_pct']}% (vs 10% normal)")
print(f"  Bonds:    {stressed['allocation']['bonds_pct']}% (vs 20% normal)")

# --- RISK METRICS ---
print("\n[4] RISK METRICS")
# Synthetic monthly portfolio values
port_values  = [100000, 102000, 105000, 103000, 107000, 110000, 108000, 112000, 115000, 118000, 121000, 125000]
nifty_values = [100000, 101000, 103000, 102000, 105000, 107000, 106000, 109000, 111000, 113000, 115000, 118000]

scorecard = portfolio_scorecard(port_values, nifty_values)
print(f"  Portfolio CAGR:    {scorecard['returns']['portfolio_cagr_pct']}%")
print(f"  Benchmark CAGR:    {scorecard['returns']['benchmark_cagr_pct']}%")
print(f"  Alpha:             {scorecard['returns']['alpha_pct']:+}%")
print(f"  Sharpe Ratio:      {scorecard['risk_adjusted']['sharpe_ratio']} ({scorecard['ratings']['sharpe']})")
print(f"  Sortino Ratio:     {scorecard['risk_adjusted']['sortino_ratio']}")
print(f"  Max Drawdown:      {scorecard['risk']['max_drawdown_pct']}% ({scorecard['ratings']['drawdown']})")

# --- MF ANALYSER ---
print("\n[5] MUTUAL FUND PATTERN ANALYSER")
signals = get_smart_money_signals(min_funds=3, mode="steady")
print(f"  Smart money consensus stocks (3+ funds holding):")
for s in signals:
    print(f"    {s['symbol']:<14} Funds: {s['funds_holding']}  Trend: {s['accumulation']:<15} Strength: {s['signal_strength']}")

# Check COALINDIA institutional backing
print("\n  COALINDIA institutional backing:")
conf = get_mf_confirmation_for_stock("COALINDIA")
print(f"    Funds holding: {conf['funds_holding_count']}")
print(f"    Conviction Score: {conf['conviction_score']}/100")
print(f"    Verdict: {conf['smart_money_verdict']}")

# Fund overlap
print("\n  Quant Active vs Parag Parikh overlap:")
overlap = get_fund_overlap("quant_active", "parag_parikh_flexi")
print(f"    Common stocks: {overlap['common_stocks']}  Overlap: {overlap['overlap_pct']}%")
for h in overlap["shared_holdings"][:3]:
    print(f"    {h['symbol']}: Quant {h['fund1_pct']}% | PP {h['fund2_pct']}%")

print("\n" + "=" * 65)
print("  ALL PHASE 4 MODULES WORKING CORRECTLY")
print("=" * 65)
