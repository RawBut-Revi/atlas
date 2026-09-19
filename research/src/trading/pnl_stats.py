"""
Pure P&L statistics over the closed-trade history, shared by every Telegram command and the EOD report
so /status, /pnl, /report and the daily summary always agree.
"""
from collections import OrderedDict

EPS = 0.005  # |net| below half a paisa counts as breakeven


def _net(t: dict) -> float:
    return t.get("net_pnl", t.get("pnl", 0.0)) or 0.0


def _gross(t: dict) -> float:
    return t.get("gross_pnl", _net(t)) or 0.0


def closed_on(history: list, date_str: str) -> list:
    """Trades whose exit_time starts with YYYY-MM-DD, newest first (history order is preserved)."""
    return [t for t in history if str(t.get("exit_time", "")).startswith(date_str)]


def summarize(trades: list) -> dict:
    """Counts, win rate (breakevens excluded) and gross/charges/net totals."""
    wins = sum(1 for t in trades if _net(t) > EPS)
    losses = sum(1 for t in trades if _net(t) < -EPS)
    breakevens = len(trades) - wins - losses
    decided = wins + losses
    nets = [_net(t) for t in trades]
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": round(wins / decided * 100, 1) if decided else 0.0,
        "gross": round(sum(_gross(t) for t in trades), 2),
        "charges": round(sum(t.get("charges", 0.0) or 0.0 for t in trades), 2),
        "net": round(sum(nets), 2),
        "best": round(max(nets), 2) if nets else 0.0,
        "worst": round(min(nets), 2) if nets else 0.0,
    }


def by_asset(trades: list) -> "OrderedDict[str, dict]":
    """Per asset class summary in a stable EQUITY, CURRENCY, COMMODITY order (only classes that traded)."""
    order = ["EQUITY", "CURRENCY", "COMMODITY"]
    groups = {}
    for t in trades:
        groups.setdefault(t.get("asset_type", "EQUITY"), []).append(t)
    keys = [k for k in order if k in groups] + sorted(k for k in groups if k not in order)
    return OrderedDict((k, summarize(groups[k])) for k in keys)


def by_strategy(trades: list) -> "OrderedDict[str, dict]":
    """Per strategy summary, best net first."""
    groups = {}
    for t in trades:
        groups.setdefault(t.get("strategy") or "UNKNOWN", []).append(t)
    rows = sorted(((k, summarize(v)) for k, v in groups.items()), key=lambda kv: kv[1]["net"], reverse=True)
    return OrderedDict(rows)


def money(x: float) -> str:
    """Signed rupee string with grouping, e.g. +₹1,234.50 / -₹99.00."""
    return f"{'+' if x >= 0 else '-'}₹{abs(x):,.2f}"


def trade_line(t: dict) -> str:
    """One compact Telegram (HTML) line per closed trade: icon, symbol, side, net, fees, exit reason, time."""
    net = _net(t)
    icon = "🟢" if net > EPS else ("🔴" if net < -EPS else "⚪")
    side = str(t.get("direction", "?"))[:1]
    when = str(t.get("exit_time", ""))[5:16]  # MM-DD HH:MM
    return (f"{icon} <b>{t.get('symbol')}</b> {side} <b>{money(net)}</b> "
            f"(fees ₹{t.get('charges', 0.0) or 0.0:,.0f}) {t.get('status', '')} {when}")


def trades_label(n: int) -> str:
    return f"{n} trade" if n == 1 else f"{n} trades"


def asset_rows(groups: dict) -> list:
    """Per-group lines: '  • Currency: 5 trades, 3W/2L, net +₹1,204.00'."""
    return [f"  • {k.title()}: {trades_label(s['trades'])}, {s['wins']}W/{s['losses']}L, net {money(s['net'])}"
            for k, s in groups.items()]


def strategy_rows(groups: dict) -> list:
    """Per-strategy lines: '  • TREND_MOMENTUM: 95 trades, WR 54.7%, net +₹12,232.96'."""
    return [f"  • {k}: {trades_label(s['trades'])}, WR {s['win_rate']}%, net {money(s['net'])}"
            for k, s in groups.items()]
