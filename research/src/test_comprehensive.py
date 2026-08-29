import sys
sys.path.insert(0, 'src')

from llm.tools import comprehensive_plan

print("=" * 70)
print("  YOUR 3-YEAR PLAN: Step-up SIP + DRIP + Penny Stocks")
print("  Year 1: Rs10k/mo | Year 2: Rs15k/mo | Year 3: Rs22.5k/mo")
print("  Penny: Rs17,000 total | Inflation hurdle: 15%/yr")
print("=" * 70)

result = comprehensive_plan(
    yearly_sip_amounts=[10000, 15000, 22500],
    penny_total=17000,
    strategy="hybrid",
    inflation_rate_pct=15.0,
)

ps = result["plan_summary"]
print(f"\n  CAPITAL BREAKDOWN:")
print(f"    Year 1 SIP (12 x Rs10,000):   Rs {12*10000:>8,}")
print(f"    Year 2 SIP (12 x Rs15,000):   Rs {12*15000:>8,}")
print(f"    Year 3 SIP (12 x Rs22,500):   Rs {12*22500:>8,}")
print(f"    ─────────────────────────────────────────")
print(f"    Total SIP:                     Rs {ps['total_sip_contributed']:>8,.0f}")
print(f"    Penny Stocks:                  Rs {ps['penny_stock_budget']:>8,.0f}")
print(f"    ═════════════════════════════════════════")
print(f"    TOTAL CAPITAL DEPLOYED:        Rs {ps['total_capital_deployed']:>8,.0f}")

print(f"\n  YEAR-BY-YEAR RESULTS:")
print(f"  {'Year':<6} {'Monthly':<12} {'Invested':<14} {'Port Value End':<18} {'Dividends/yr':<14} {'Gain%'}")
print(f"  {'-'*5} {'-'*11} {'-'*13} {'-'*17} {'-'*13} {'-'*6}")
for y in result["year_by_year"]:
    print(f"  Y{y['year']:<5} Rs{y['monthly_sip']:>8,}  Rs{y['total_invested_this_year']:>10,.0f}  Rs{y['portfolio_value_end']:>14,.0f}  Rs{y['annual_dividend_income']:>10,.0f}/yr  {y['year_gain_pct']:>+.1f}%")

fp = result["final_portfolio"]
print(f"\n  FINAL PORTFOLIO (End of Year 3):")
print(f"    Equity Value:           Rs {fp['equity_value']:>10,.0f}  ← WHAT YOUR STOCKS ARE WORTH")
print(f"    Unrealised Gain:        Rs {fp['unrealised_gain']:>10,.0f}  ← Profit above what you put in")
print(f"    Total Return:            {fp['total_return_pct']:>9.1f}%")
print(f"    Annual Dividend Income: Rs {fp['annual_dividend_income']:>10,.0f}/yr")
print(f"    Monthly Dividend:       Rs {fp['monthly_dividend_income']:>10,.0f}/mo  ← Passive income")

ds = result["drip_stats"]
print(f"\n  DRIP (Dividend Reinvestment):")
print(f"    Total extra invested via DRIP:  Rs {ds['total_reinvested']:>8,.0f}")
print(f"    DRIP transactions done:          {ds['transactions']:>8}")

ic = result["inflation_check"]
print(f"\n  INFLATION CHECK (15%/yr for 3 years):")
print(f"    Your total capital:              Rs {ic['total_capital_deployed']:>8,.0f}")
print(f"    What 15% inflation requires:     Rs {ic['inflation_adjusted_target']:>8,.0f}  ← Need to beat this")
print(f"    Your portfolio value:            Rs {ic['final_portfolio_value']:>8,.0f}")
if ic["beats_inflation"]:
    print(f"    Result:  BEATS INFLATION by Rs {ic['gap']:,.0f}")
else:
    print(f"    Result:  SHORT of inflation by Rs {abs(ic['gap']):,.0f}")
    print(f"    Penny stocks need to close this gap!")

qj = result["quit_job_analysis"]
print(f"\n  QUIT JOB ANALYSIS:")
print(f"    Monthly dividend income:  Rs {qj['monthly_passive_income']:,.0f}/mo")
print(f"    Annual dividend income:   Rs {qj['annual_passive_income']:,.0f}/yr")
print(f"\n    To quit your job, Rs {qj['monthly_passive_income']:,.0f}/mo needs to exceed your salary.")
print(f"    If your salary is Rs 30,000/mo: {'YES - Possible!' if qj['monthly_passive_income'] > 30000 else 'Not yet. Keep building.'}")
print(f"    If your salary is Rs 50,000/mo: {'YES - Possible!' if qj['monthly_passive_income'] > 50000 else 'Not yet. Keep building.'}")
print(f"    If your salary is Rs 75,000/mo: {'YES - Possible!' if qj['monthly_passive_income'] > 75000 else 'Not yet. Keep building.'}")

print(f"\n  PENNY STOCK ALLOCATION (Rs17,000):")
for a in result["penny_portfolio"]["allocations"]:
    print(f"    {a['symbol']:<14} {a['shares']:>4} shares @ Rs{a['price']:>6} = Rs{a['amount_inr']:>7,.0f}  ({a['revenue_cagr_2y']}% rev growth)")

print(f"\n  {result['penny_portfolio']['risk_warning']}")
print("\n" + "=" * 70)
