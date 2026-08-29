import sys
sys.path.insert(0, 'src')
from screening.penny_screener import screen_penny_stocks, build_penny_portfolio, check_penny_exit

print("=" * 65)
print("  ATLAS PENNY STOCK SCREENER")
print("=" * 65)

# Full screen
result = screen_penny_stocks(total_portfolio_value=500000)

print(f"\n  Screened: {result['screened']} | Passed: {result['passed']} | Rejected: {result['rejected']}")
print(f"\n  STRONG BUYS (Score >= 78):")
for s in result["strong_buys"]:
    print(f"    {s['symbol']:<14} Score={s['penny_score']:>5} | {s['verdict'][:30]}")
    print(f"                   Rev 2Y CAGR: {s['revenue_cagr_2y']}% | Promoter: {s['promoter_trend']}")

print(f"\n  BUYS (Score 62-78):")
for s in result["buys"]:
    print(f"    {s['symbol']:<14} Score={s['penny_score']:>5} | Rev 2Y: {s['revenue_cagr_2y']}%")

print(f"\n  WATCHLIST (Score 48-62):")
for s in result["watchlist"]:
    print(f"    {s['symbol']:<14} Score={s['penny_score']:>5}")

print(f"\n  REJECTED:")
for r in result["rejected_stocks"]:
    print(f"    {r['symbol']:<14} Reason: {r['reason']}")

# Build a balanced penny portfolio with Rs 5L total
print("\n" + "=" * 65)
print("  BALANCED PENNY PORTFOLIO (Rs 5,00,000 total | 15% = Rs 75,000)")
print("=" * 65)
port = build_penny_portfolio(total_portfolio_value=500000, strategy="balanced")
print(f"\n  Budget: Rs {port['penny_budget']:,.0f} | Invested: Rs {port['total_allocated']:,.0f}")
for a in port["allocations"]:
    print(f"    {a['symbol']:<14} {a['shares']:>4} shares @ Rs{a['price']:>6} = Rs {a['amount_inr']:>8,.0f}  ({a['portfolio_pct']}% of portfolio)")
    print(f"                   Score: {a['penny_score']} | {a['category']} | Rev: {a['revenue_cagr_2y']}% CAGR")

# Exit signal check — simulate Cupid bought at Rs 100, now Rs 215 (2x+)
print("\n" + "=" * 65)
print("  EXIT SIGNAL CHECK")
print("=" * 65)
exit_check = check_penny_exit("CUPIDLTD", entry_price=100, current_price=215)
print(f"\n  CUPIDLTD: Entry Rs 100 → Current Rs 215")
print(f"  P&L: {exit_check['pnl_pct']:+}%")
print(f"  Exit urgency: {exit_check['exit_urgency']}")
for sig in exit_check["signals"]:
    print(f"  [{sig['trigger']}] {sig['message']}")
    print(f"    → Action: {sig['action']}")

# Simulate stop-loss scenario
exit_bad = check_penny_exit("TRIDENT", entry_price=50, current_price=32)
print(f"\n  TRIDENT: Entry Rs 50 → Current Rs 32")
print(f"  P&L: {exit_bad['pnl_pct']:+}%")
print(f"  Exit urgency: {exit_bad['exit_urgency']}")
for sig in exit_bad["signals"]:
    print(f"  [{sig['trigger']}] {sig['message']}")
    print(f"    → Action: {sig['action']}")
