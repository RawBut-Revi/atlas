import { useState, useEffect, useCallback } from 'react';
import {
  GetScannerPicks, GetScannerPortfolio, GetScannerBacktest, GetScannerStudy, RebalanceScanner, RefreshScanner,
} from '../wailsjs/go/main/ScannerService';

const inr = (n: number) => '₹' + (n ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const inr2 = (n: number) => '₹' + (n ?? 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (v: number | null | undefined, nd = 1) => (v === null || v === undefined ? 'n/a' : `${(v * 100).toFixed(nd)}%`);
const signed = (v: number, nd = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(nd)}%`;
const tone = (v: number) => (v >= 0 ? 'text-atlas-green' : 'text-atlas-red');

/**
 * Investment scanner: ranks the NSE universe by momentum (12-1m, 6m, trend, 52-week-high closeness,
 * volatility-penalised), applies a quality filter, and tracks a PAPER portfolio against the 25%/yr
 * goal and Nifty. The evidence banner is always shown: the backtest found no edge over holding the
 * whole universe, so 25% is a goal being measured, not a forecast.
 */
export default function ScannerPanel({ onLog }: { onLog: (msg: string) => void }) {
  const [picks, setPicks] = useState<any>(null);
  const [portfolio, setPortfolio] = useState<any>(null);
  const [backtest, setBacktest] = useState<any>(null);
  const [study, setStudy] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);
  const [capital, setCapital] = useState(150000);
  const [busy, setBusy] = useState<'' | 'load' | 'refresh' | 'plan' | 'exec'>('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showBacktest, setShowBacktest] = useState(false);
  const [showStudy, setShowStudy] = useState(false);

  const load = useCallback(async () => {
    setBusy('load');
    const [p, pf, bt, st] = await Promise.allSettled([GetScannerPicks(), GetScannerPortfolio(), GetScannerBacktest(), GetScannerStudy()]);
    if (p.status === 'fulfilled') { setPicks(p.value); setError(''); }
    else { setPicks(null); setError(String((p.reason as any)?.message ?? p.reason)); }
    if (pf.status === 'fulfilled') setPortfolio(pf.value);
    if (bt.status === 'fulfilled') setBacktest(bt.value);
    if (st.status === 'fulfilled') setStudy(st.value);
    setBusy('');
  }, []);

  useEffect(() => { load(); }, [load]);

  // While Yahoo prices refresh in the background, poll until they land.
  useEffect(() => {
    if (!picks?.refreshing) return;
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [picks?.refreshing, load]);

  const refresh = async () => {
    setBusy('refresh'); setError(''); setNotice('');
    try {
      const r = await RefreshScanner();
      setNotice(r.status === 'STARTED' ? 'Price refresh started (1-2 min). This page updates itself.' : 'A refresh is already running.');
      onLog(`Scanner price refresh: ${r.status}`);
      setTimeout(load, 3000);
    } catch (e: any) { setError(String(e?.message ?? e)); }
    setBusy('');
  };

  const rebalance = async (execute: boolean) => {
    if (execute) {
      const n = plan?.orders?.length ?? 0;
      if (n === 0) { setError('Nothing to apply: run PLAN first and check there are orders.'); return; }
      if (!window.confirm(`Apply ${n} PAPER order(s) to the scanner portfolio? (No real orders are placed.)`)) return;
    }
    setBusy(execute ? 'exec' : 'plan'); setError(''); setNotice('');
    try {
      const r = await RebalanceScanner(execute, capital, false);
      setPlan(r);
      if (r.status === 'NOT_DUE') setNotice(r.detail);
      onLog(`Scanner rebalance ${execute ? 'apply' : 'plan'}: ${r.status}`);
      if (execute && r.status === 'EXECUTED') { setNotice('Paper portfolio updated.'); setPlan(null); await load(); }
    } catch (e: any) { setError(String(e?.message ?? e)); }
    setBusy('');
  };

  const bt = picks?.backtest ?? backtest?.default;
  const pf = portfolio?.status === 'OK' ? portfolio : null;
  const years = bt ? Object.keys(bt.yearly || {}) : [];

  return (
    <div className="flex-1 overflow-auto p-2 text-[11px] flex flex-col gap-2">
      {/* Evidence banner: always visible, never hidden behind a click */}
      <div className="border border-atlas-orange/60 bg-atlas-orange/10 px-2 py-1.5 text-atlas-orange">
        <strong className="tracking-wider">HOW MUCH TO TRUST THIS</strong>{' '}
        {bt ? (
          <>
            Backtest {bt.first_date?.slice(0, 4)}-{bt.last_date?.slice(0, 4)}, costs included: scanner CAGR <strong>{pct(bt.cagr)}</strong>,
            max drawdown {pct(bt.max_drawdown, 0)}; simply holding the whole universe: {pct(bt.benchmark_cagr)}.
            The 25% goal was met in <strong>{bt.years_meeting_target} of {bt.years_total}</strong> calendar years.{' '}
          </>
        ) : 'Backtest not run yet. '}
        {picks?.study && (
          <>The same model on {picks.study.markets} markets was ahead of its own universe after costs in <strong>{picks.study.beat_universe}</strong> and
          statistically significant in <strong>{picks.study.significant}</strong>. </>
        )}
        No proven edge over the universe, and the universe is survivorship-flattered, so a live portfolio should expect less.
        This scans every NSE stock, so picks lean to volatile small and mid caps.
        25% is a goal to measure against, not a forecast. Paper only. Not investment advice.
      </div>

      {error && <div className="border border-atlas-red/60 bg-atlas-red/10 text-atlas-red px-2 py-1">{error}</div>}
      {notice && <div className="border border-atlas-green/50 bg-atlas-green/10 text-atlas-green px-2 py-1">{notice}</div>}

      <div className="flex flex-wrap items-center gap-3 border border-atlas-border bg-black/30 px-2 py-1.5">
        <span className="font-bold tracking-wider text-atlas-text-dim">PRICES</span>
        <span className={picks?.price_age_hours > 20 ? 'text-atlas-orange' : 'text-white'}>
          {picks ? `as of ${picks.asof} (${picks.price_age_hours ?? '?'}h old)` : 'none loaded'}
        </span>
        {picks?.refreshing && <span className="text-atlas-accent">refreshing in background...</span>}
        <button onClick={refresh} disabled={busy !== ''} className="px-2 py-0.5 border border-atlas-border hover:text-white text-atlas-text-dim">
          REFRESH PRICES
        </button>
        <button onClick={load} disabled={busy !== ''} className="px-2 py-0.5 border border-atlas-border hover:text-white text-atlas-text-dim">
          {busy === 'load' ? 'LOADING...' : 'RELOAD'}
        </button>
        {picks && (
          <span className="ml-auto text-atlas-text-dim">
            Scanned <strong className="text-white">{picks.universe_scanned}</strong> NSE stocks | eligible {picks.eligible} | invested <strong className="text-white">{picks.invested_pct}%</strong>
          </span>
        )}
      </div>

      {picks && !picks.risk_on && (
        <div className="border border-atlas-red/60 bg-atlas-red/10 text-atlas-red px-2 py-1">
          Risk-off: only {pct(picks.breadth, 0)} of stocks are above their 200-day average. Holding cash.
        </div>
      )}

      <div className="flex gap-2 items-start flex-wrap">
        {/* Picks */}
        <div className="flex-1 min-w-[560px] border border-atlas-border">
          <div className="px-2 py-1 bg-black/30 font-bold tracking-wider text-atlas-text-dim">TOP PICKS (momentum-led, monthly rebalance)</div>
          <table className="w-full text-right">
            <thead className="text-atlas-text-dim">
              <tr>
                <th className="text-left px-2 py-1">#</th><th className="text-left">SYMBOL</th><th className="text-left">SECTOR</th>
                <th>WEIGHT</th><th>PRICE</th><th>12-1M</th><th>6M</th><th>VS 200D</th><th>VOL</th>
              </tr>
            </thead>
            <tbody>
              {(picks?.picks ?? []).map((p: any, i: number) => (
                <tr key={p.symbol} className="border-t border-atlas-border/40">
                  <td className="text-left px-2 py-0.5 text-atlas-text-dim">{i + 1}</td>
                  <td className="text-left font-bold text-white">
                    {p.symbol}
                    {p.unverified?.length > 0 && <span title={`Unverified: ${p.unverified.join(', ')}`} className="text-atlas-orange"> ❔</span>}
                  </td>
                  <td className="text-left text-atlas-text-dim">{p.sector ?? ''}</td>
                  <td>{(p.weight * 100).toFixed(1)}%</td>
                  <td>{inr2(p.price)}</td>
                  <td className={tone(p.mom_12_1)}>{signed(p.mom_12_1 * 100, 0)}</td>
                  <td className={tone(p.mom_6)}>{signed(p.mom_6 * 100, 0)}</td>
                  <td className={tone(p.trend_pct)}>{signed(p.trend_pct)}</td>
                  <td className="pr-2">{(p.vol * 100).toFixed(0)}%</td>
                </tr>
              ))}
              {picks && picks.picks.length === 0 && (
                <tr><td colSpan={9} className="px-2 py-3 text-center text-atlas-text-dim">No stock passes the filters right now.</td></tr>
              )}
              {!picks && <tr><td colSpan={9} className="px-2 py-3 text-center text-atlas-text-dim">{busy === 'load' ? 'Loading...' : 'No data yet: refresh prices.'}</td></tr>}
            </tbody>
          </table>
          {picks?.rejected?.length > 0 && (
            <div className="px-2 py-1 border-t border-atlas-border/40 text-atlas-text-dim">
              Skipped on quality: {picks.rejected.slice(0, 5).map((r: any) => `${r.symbol} (${r.reasons.join(', ')})`).join(' | ')}
            </div>
          )}
          <div className="px-2 py-1 border-t border-atlas-border/40 text-atlas-text-dim">
            ❔ = fundamentals missing, quality filter could not verify. The quality filter uses today's fundamentals and is untested historically.
          </div>
        </div>

        {/* Paper portfolio vs 25% goal */}
        <div className="w-[380px] border border-atlas-border">
          <div className="px-2 py-1 bg-black/30 font-bold tracking-wider text-atlas-text-dim">PAPER PORTFOLIO vs 25% GOAL</div>
          {pf ? (
            <div className="p-2 flex flex-col gap-1">
              <div>Value <strong className="text-white">{inr(pf.value)}</strong> from {inr(pf.capital)} (day {pf.days})</div>
              <div>Return <strong className={tone(pf.return_pct)}>{signed(pf.return_pct, 2)}</strong> | annualized{' '}
                <strong>{pf.annualized_pct !== null ? `${pf.annualized_pct}%` : pf.annualized_note}</strong></div>
              <div className={pf.on_track ? 'text-atlas-green' : 'text-atlas-red'}>
                {pf.on_track ? 'AHEAD of' : 'BEHIND'} the 25%/yr line by {inr(Math.abs(pf.vs_hurdle))} (line = {inr(pf.hurdle_value)})
              </div>
              <div className="text-atlas-text-dim">
                Nifty since start: {pf.nifty_return_pct !== null ? signed(pf.nifty_return_pct, 2) : 'n/a'}
                {pf.vs_nifty_pct !== null && <> (you {signed(pf.vs_nifty_pct, 2)})</>} | max drawdown {pf.max_drawdown_pct}%
              </div>
              <div className="text-atlas-text-dim">Cash {inr(pf.cash)} | next rebalance {pf.next_rebalance}</div>
              <table className="w-full text-right mt-1">
                <thead className="text-atlas-text-dim"><tr><th className="text-left">HOLDING</th><th>QTY</th><th>VALUE</th><th>P&L</th></tr></thead>
                <tbody>
                  {pf.holdings.map((h: any) => (
                    <tr key={h.symbol} className="border-t border-atlas-border/40">
                      <td className="text-left font-bold text-white">{h.symbol}</td><td>{h.qty}</td>
                      <td>{inr(h.value)}</td><td className={tone(h.pnl_pct)}>{signed(h.pnl_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-2 flex items-center gap-2 text-atlas-text-dim">
              <span>No paper portfolio yet. Starting capital:</span>
              <input type="number" value={capital} min={10000} step={10000} onChange={(e) => setCapital(parseFloat(e.target.value) || 0)}
                className="bg-atlas-bg border border-atlas-border px-1 py-0.5 w-24 text-white" />
            </div>
          )}
          <div className="p-2 flex gap-2 border-t border-atlas-border/40">
            <button onClick={() => rebalance(false)} disabled={busy !== '' || !picks}
              className="px-2 py-0.5 border border-atlas-border hover:text-white text-atlas-text-dim">
              {busy === 'plan' ? 'PLANNING...' : 'PLAN REBALANCE'}
            </button>
            <button onClick={() => rebalance(true)} disabled={busy !== '' || !plan || plan.status !== 'PLAN'}
              className="px-2 py-0.5 border border-atlas-accent text-atlas-accent disabled:opacity-40 disabled:border-atlas-border disabled:text-atlas-text-dim">
              {busy === 'exec' ? 'APPLYING...' : 'APPLY (PAPER)'}
            </button>
          </div>
          {plan?.status === 'PLAN' && (
            <div className="px-2 pb-2">
              <div className="text-atlas-text-dim">Plan: {plan.orders.length} orders, est. costs {inr(plan.est_costs)}, cash after {inr(plan.cash_after)}</div>
              {plan.orders.map((o: any) => (
                <div key={o.symbol + o.side} className={o.side === 'BUY' ? 'text-atlas-green' : 'text-atlas-red'}>
                  {o.side} {o.qty} x {o.symbol} @ {inr2(o.price)} ({inr(o.value)})
                </div>
              ))}
              <div className="text-atlas-text-dim">{plan.note}</div>
            </div>
          )}
        </div>
      </div>

      {/* Multi-market study: does the same fixed model work outside India? */}
      {study && (
        <div className="border border-atlas-border">
          <button onClick={() => setShowStudy(!showStudy)} className="w-full text-left px-2 py-1 bg-black/30 font-bold tracking-wider text-atlas-text-dim hover:text-white">
            {showStudy ? '▼' : '▶'} MARKET STUDY: same fixed model on {study.summary.markets} markets. Ahead of universe in {study.summary.beat_universe}, statistically significant in {study.summary.significant}
          </button>
          {showStudy && (
            <div className="p-2 flex flex-col gap-2">
              <table className="w-full text-right">
                <thead className="text-atlas-text-dim">
                  <tr>
                    <th className="text-left">MARKET</th><th>STOCKS</th><th>SCANNER</th><th>UNIVERSE</th><th>DIFF</th><th>T-STAT</th>
                    <th title="Share of random portfolios (same eligible pool) the scanner beat BEFORE costs. About 50 = no selection skill.">SKILL %</th>
                    <th>MAX DD</th><th>YEARS &gt;=25%</th><th className="text-left pl-3">VERDICT</th>
                  </tr>
                </thead>
                <tbody>
                  {study.rows.map((r: any) => (
                    <tr key={r.market} className={`border-t border-atlas-border/40 ${r.reference ? 'text-atlas-text-dim' : ''}`}>
                      <td className="text-left font-bold text-white">{r.market.replace(/_/g, ' ')}{r.reference ? ' *' : ''}</td>
                      <td>{r.stocks}</td><td>{pct(r.scanner_cagr)}</td><td>{pct(r.universe_cagr)}</td>
                      <td className={tone(r.excess)}>{signed(r.excess * 100)}</td>
                      <td>{r.t_stat === null ? 'n/a' : r.t_stat.toFixed(2)}</td>
                      <td>{r.skill_percentile === null ? 'n/a' : r.skill_percentile.toFixed(0)}</td>
                      <td>{pct(r.max_drawdown, 0)}</td><td>{r.years_meeting_target}/{r.years_total}</td>
                      <td className={`text-left pl-3 ${r.verdict === 'behind universe' ? 'text-atlas-red' : 'text-atlas-orange'}`}>{r.verdict}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-atlas-text-dim">
                Same score weights, top 12, monthly rebalance in every market; only the market's own cost and liquidity floor change.
                T-STAT under 2 means the gap over the universe could be luck. SKILL % near 50 means the ranking does no better than random picks
                from the same pool. * = the old 176-stock list, for comparison.
              </div>
              <ul className="list-disc pl-4 text-atlas-orange">
                {(study.caveats ?? []).map((c: string) => <li key={c}>{c}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Backtest detail */}
      {bt && (
        <div className="border border-atlas-border">
          <button onClick={() => setShowBacktest(!showBacktest)} className="w-full text-left px-2 py-1 bg-black/30 font-bold tracking-wider text-atlas-text-dim hover:text-white">
            {showBacktest ? '▼' : '▶'} BACKTEST DETAIL: year by year and every variant tried
          </button>
          {showBacktest && (
            <div className="p-2 flex flex-col gap-2">
              <table className="text-right">
                <thead className="text-atlas-text-dim">
                  <tr><th className="text-left pr-3">YEAR</th>{years.map((y) => <th key={y} className="px-2">{y}</th>)}</tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="text-left pr-3 text-atlas-text-dim">Scanner</td>
                    {years.map((y) => (
                      <td key={y} className={`px-2 ${bt.yearly[y] >= 0.25 ? 'text-atlas-green font-bold' : tone(bt.yearly[y])}`}>{signed(bt.yearly[y] * 100, 0)}</td>
                    ))}
                  </tr>
                  <tr>
                    <td className="text-left pr-3 text-atlas-text-dim">Universe</td>
                    {years.map((y) => <td key={y} className="px-2 text-atlas-text-dim">{signed((bt.benchmark_yearly?.[y] ?? 0) * 100, 0)}</td>)}
                  </tr>
                </tbody>
              </table>
              <div className="text-atlas-text-dim">Green = year at or above the 25% goal. Half-by-half CAGR: {pct(bt.first_half_cagr)} then {pct(bt.second_half_cagr)}.</div>
              {backtest?.variants && (
                <table className="w-[420px] text-right">
                  <thead className="text-atlas-text-dim"><tr><th className="text-left">VARIANT (all reported)</th><th>CAGR</th><th>MAX DD</th></tr></thead>
                  <tbody>
                    <tr className="text-white"><td className="text-left">Default (12 stocks, switch off)</td><td>{pct(backtest.default.cagr)}</td><td>{pct(backtest.default.max_drawdown, 0)}</td></tr>
                    {Object.entries(backtest.variants).map(([name, v]: [string, any]) => (
                      <tr key={name}><td className="text-left text-atlas-text-dim">{name.replace(/_/g, ' ')}</td><td>{pct(v.cagr)}</td><td>{pct(v.max_drawdown, 0)}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
              <ul className="list-disc pl-4 text-atlas-orange">
                {(backtest?.caveats ?? []).map((c: string) => <li key={c}>{c}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
