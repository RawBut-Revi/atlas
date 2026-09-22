"""
Telegram (HTML) formatting for the /invest scanner commands. Pure functions over ScannerService output.

Every picks message carries the backtest reference and its honesty line, so a ranked list is never
shown without the evidence about how far to trust it.
"""
from typing import Dict, Optional


def _pct(v: Optional[float], nd: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{nd}f}%"


def _signed(v: float) -> str:
    return f"{v:+.1f}%"


def backtest_line(bt: Optional[Dict]) -> str:
    if not bt:
        return "📊 Backtest: not run yet (python run_scanner.py --backtest)"
    return (f"📊 Backtest {bt['first_date'][:4]}-{bt['last_date'][:4]}: CAGR <b>{_pct(bt['cagr'])}</b>, "
            f"max drawdown {_pct(bt['max_drawdown'], 0)}, holding the whole universe {_pct(bt['benchmark_cagr'])}. "
            f"25% met in <b>{bt['years_meeting_target']} of {bt['years_total']}</b> years.")


def format_picks(out: Dict) -> str:
    lines = [
        f"📈 <b>INVEST SCANNER</b> (prices as of {out.get('asof')})",
        "━━━━━━━━━━━━━━━━━━━",
        backtest_line(out.get("backtest")),
        "⚠️ No proven edge over holding the whole universe; the backtest is survivorship-flattered. "
        "25% is a goal, not a forecast. Not investment advice.",
    ]
    sm = out.get("study")
    if sm:
        lines.append(f"🌍 Same model on {sm['markets']} markets: ahead of its own universe after costs in "
                     f"<b>{sm['beat_universe']}</b>, statistically significant in <b>{sm['significant']}</b> (/invest study).")
    age = out.get("price_age_hours")
    if out.get("refreshing"):
        lines.append("🔄 Refreshing prices in the background...")
    elif age is not None and age > 20:
        lines.append(f"🕒 Prices are {age:.0f}h old: refresh started, run /invest again in ~2 min.")
    if not out.get("risk_on"):
        lines.append(f"🛑 Risk-off: only {_pct(out.get('breadth'), 0)} of stocks above their 200-day average. Holding cash.")
    lines.append("")
    if not out["picks"]:
        lines.append("No stock passes the filters right now.")
    for k, p in enumerate(out["picks"], 1):
        tag = f" ({p['sector']})" if p.get("sector") else ""
        warn = " ❔" if p.get("unverified") else ""
        lines.append(
            f"{k}. <b>{p['symbol']}</b>{tag}{warn} — {p['weight'] * 100:.1f}% @ ₹{p['price']:,.2f}\n"
            f"   12-1m {_signed(p['mom_12_1'] * 100)} | 6m {_signed(p['mom_6'] * 100)} | "
            f"trend {_signed(p['trend_pct'])} | vol {p['vol'] * 100:.0f}%"
        )
    if out.get("rejected"):
        names = ", ".join(r["symbol"] for r in out["rejected"][:5])
        lines.append(f"\n🚫 Skipped on quality: {names}")
    lines.append(f"\n💼 Invested {out.get('invested_pct', 0)}% | {out['eligible']} eligible of {out['universe_scanned']} scanned")
    lines.append("❔ = fundamentals missing (unverified).  /invest status | /invest rebalance | /invest refresh")
    return "\n".join(lines)


def format_study(rep: Dict) -> str:
    """The same fixed model on every market: a fixed-width table plus a plain-language verdict."""
    lines = ["🌍 <b>DOES THE MODEL WORK ELSEWHERE?</b>", "━━━━━━━━━━━━━━━━━━━",
             "Same model everywhere, after costs. Scanner vs holding that market's whole universe.", "<pre>"]
    lines.append(f"{'market':<11}{'scan':>6}{'univ':>6}{'diff':>7}{'t':>6} verdict")
    for r in rep["rows"]:
        name = (r["market"].replace("_", " ")[:10]) + ("*" if r["reference"] else "")
        t = "n/a" if r["t_stat"] is None else f"{r['t_stat']:.1f}"
        lines.append(f"{name:<11}{r['scanner_cagr'] * 100:5.1f}%{r['universe_cagr'] * 100:5.1f}%"
                     f"{r['excess'] * 100:+6.1f}%{t:>6} {r['verdict'].replace('ahead, ', '')}")
    lines.append("</pre>")
    sm = rep["summary"]
    lines.append(f"Ahead of its own universe in <b>{sm['beat_universe']} of {sm['markets']}</b> markets after costs; "
                 f"statistically significant in <b>{sm['significant']}</b>; 25%/yr reached in <b>{sm['reached_25pct']}</b>.")
    lines.append("t under 2 means the gap could be luck. * = old 176-stock list, for comparison.")
    lines.append("⚠️ Every universe is today's survivors, so all figures are flattered (India-all the most).")
    return "\n".join(lines)


def format_status(st: Dict) -> str:
    variant = st.get("variant", "monthly")
    label = st.get("label", "Monthly (default)")
    if st["status"] == "NO_PORTFOLIO":
        cmd = "/invest rebalance confirm" if variant == "monthly" else f"/invest rebalance {variant} confirm"
        return f"💼 No paper portfolio yet for {label}. Send <code>{cmd}</code> to create one (₹1,50,000 paper)."
    ann = f"{st['annualized_pct']:.1f}%" if st["annualized_pct"] is not None else st["annualized_note"]
    verdict = "🟢 ahead of" if st["on_track"] else "🔴 behind"
    nifty = "n/a" if st["nifty_return_pct"] is None else f"{st['nifty_return_pct']:+.2f}% (you {st['vs_nifty_pct']:+.2f}%)"
    lines = [
        f"💼 <b>SCANNER PAPER PORTFOLIO</b> — {label} (day {st['days']})",
        "━━━━━━━━━━━━━━━━━━━",
        f"💰 ₹{st['value']:,.0f} from ₹{st['capital']:,.0f}: <b>{st['return_pct']:+.2f}%</b> | annualized: {ann}",
        f"🎯 {verdict} the 25%/yr line (needs ₹{st['hurdle_value']:,.0f}, gap {st['vs_hurdle']:+,.0f})",
        f"🇮🇳 Nifty since start: {nifty}",
        f"📉 Max drawdown {st['max_drawdown_pct']:.1f}% | cash ₹{st['cash']:,.0f} | next rebalance {st['next_rebalance']}",
        "",
    ]
    for h in st["holdings"]:
        lines.append(f"• <b>{h['symbol']}</b> {h['qty']} @ ₹{h['avg_cost']:,.2f} → ₹{h['price']:,.2f} ({h['pnl_pct']:+.1f}%)")
    return "\n".join(lines)


def format_compare(cmp: Dict) -> str:
    """Monthly vs quarterly forward test, side by side. The comparison rule is shown every time so the
    criterion can never be quietly changed once real numbers exist."""
    lines = ["⚖️ <b>MONTHLY vs QUARTERLY (forward test)</b>", "━━━━━━━━━━━━━━━━━━━"]
    for variant in ("monthly", "quarterly"):
        st = cmp["variants"][variant]
        if st["status"] != "OK":
            lines.append(f"• <b>{st.get('label', variant)}</b>: {st['detail']}")
            continue
        ann = f"{st['annualized_pct']:.1f}%" if st["annualized_pct"] is not None else st["annualized_note"]
        lines.append(f"• <b>{st['label']}</b> (day {st['days']}): {st['return_pct']:+.2f}% "
                     f"(annualized {ann}) | max drawdown {st['max_drawdown_pct']:.1f}%")
    lines.append(f"\n🏁 <b>{cmp['verdict']}</b>")
    lines.append(f"\nRule (fixed before either had a result): {cmp['rule']}")
    return "\n".join(lines)


def format_plan(plan: Dict) -> str:
    if plan["status"] == "NOT_DUE":
        return f"⏳ {plan['detail']}"
    head = "✅ <b>PAPER REBALANCE EXECUTED</b>" if plan["status"] == "EXECUTED" else "🧾 <b>REBALANCE PLAN</b> (nothing executed)"
    lines = [head, "━━━━━━━━━━━━━━━━━━━",
             f"Portfolio ₹{plan['portfolio_value']:,.0f} | est. costs ₹{plan['est_costs']:,.0f} | cash after ₹{plan['cash_after']:,.0f}"]
    if not plan["orders"]:
        lines.append("No trades needed.")
    for o in plan["orders"]:
        icon = "🟢 BUY " if o["side"] == "BUY" else "🔴 SELL"
        lines.append(f"{icon} {o['qty']} × <b>{o['symbol']}</b> @ ₹{o['price']:,.2f} (₹{o['value']:,.0f})")
    lines.append(plan["note"])
    if plan["status"] == "PLAN":
        lines.append("Send <code>/invest rebalance confirm</code> to apply to the paper portfolio.")
    return "\n".join(lines)
