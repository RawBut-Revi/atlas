import { useState, useEffect, useCallback } from 'react';
import {
  GetDripStatus, PlanDrip, ExecuteDrip, SetDripKillSwitch, InitDripPortfolio,
} from '../wailsjs/go/main/DripService';

const inr = (n: number) => '₹' + (n ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const inr2 = (n: number) => '₹' + (n ?? 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Dividend reinvestment: ranks liquid NSE dividend payers by expected total return and buys with dividend cash. */
export default function DripPanel({ onLog }: { onLog: (msg: string) => void }) {
  const [status, setStatus] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);
  const [mode, setMode] = useState<'paper' | 'live'>('paper');
  const [maxDeploy, setMaxDeploy] = useState(50000);
  const [trackingStart, setTrackingStart] = useState('');
  const [liveText, setLiveText] = useState('');
  const [initText, setInitText] = useState('');
  const [busy, setBusy] = useState<'' | 'plan' | 'exec' | 'kill' | 'init'>('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await GetDripStatus());
      setError('');
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const run = async (kind: 'plan' | 'exec') => {
    setBusy(kind);
    setError('');
    setNotice('');
    try {
      const rep = kind === 'plan'
        ? await PlanDrip(mode, maxDeploy, trackingStart)
        : await ExecuteDrip(mode, mode === 'live' && liveText === 'LIVE', maxDeploy, trackingStart);
      setPlan(rep);
      onLog(`DRIP ${kind === 'plan' ? 'plan' : 'execute'} (${mode}): ${rep.status}`);
      if (kind === 'exec') {
        const filled = (rep.executed || []).filter((o: any) => o.status === 'FILLED').length;
        setNotice(`${rep.status}: ${filled} order(s) filled, ${(rep.executed || []).length - filled} not filled.`);
        loadStatus();
      }
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
    setBusy('');
  };

  const execute = () => {
    const n = plan?.plan?.orders?.length ?? 0;
    if (n === 0) { setError('Nothing to execute: run PLAN first and check there are orders.'); return; }
    if (!window.confirm(`Place ${n} ${mode.toUpperCase()} order(s) totalling ${inr(plan.plan.cash_spent)}?`)) return;
    run('exec');
  };

  const toggleKill = async () => {
    setBusy('kill');
    try {
      const r = await SetDripKillSwitch(!status?.killed);
      onLog(`DRIP kill switch ${r.killed ? 'ON' : 'OFF'}`);
      await loadStatus();
    } catch (e: any) { setError(String(e?.message ?? e)); }
    setBusy('');
  };

  const initPortfolio = async () => {
    setBusy('init');
    try {
      const holdings: Record<string, number> = {};
      for (const part of initText.split(',')) {
        const [sym, qty] = part.split(':');
        if (sym && qty) holdings[sym.trim().toUpperCase()] = parseInt(qty, 10);
      }
      await InitDripPortfolio(holdings);
      setInitText('');
      await loadStatus();
    } catch (e: any) { setError(String(e?.message ?? e)); }
    setBusy('');
  };

  const liveReady = mode === 'live' && liveText === 'LIVE' && status?.live_enabled;
  const hasHoldings = status && Object.keys(status.holdings || {}).length > 0;
  const p = plan?.plan;

  return (
    <div className="flex-1 overflow-auto p-2 text-[11px] flex flex-col gap-2">
      {/* Safety strip */}
      <div className="flex flex-wrap items-center gap-3 border border-atlas-border bg-black/30 px-2 py-1.5">
        <span className="font-bold tracking-wider text-atlas-text-dim">MODE</span>
        <select value={mode} onChange={(e) => { setMode(e.target.value as any); setPlan(null); setLiveText(''); }}
          className="bg-atlas-bg border border-atlas-border px-1 py-0.5 text-white">
          <option value="paper">PAPER (simulated)</option>
          <option value="live">LIVE (real Upstox portfolio)</option>
        </select>
        <span className={`px-2 py-0.5 font-bold border ${status?.killed ? 'border-atlas-red text-atlas-red' : 'border-atlas-green/50 text-atlas-green'}`}>
          {status?.killed ? 'KILL SWITCH: ON (no orders)' : 'KILL SWITCH: off'}
        </span>
        <button onClick={toggleKill} disabled={busy !== ''} className="px-2 py-0.5 border border-atlas-border hover:text-white text-atlas-text-dim">
          {status?.killed ? 'RESUME' : 'STOP ALL'}
        </button>
        <span className={status?.live_enabled ? 'text-atlas-orange' : 'text-atlas-text-dim'}>
          Live orders {status?.live_enabled ? 'ENABLED on engine' : 'disabled on engine (set ATLAS_LIVE_DRIP=1)'}
        </span>
        <span className="ml-auto text-atlas-text-dim">Paper cash at broker: <strong className="text-white">{inr2(status?.broker_funds ?? 0)}</strong> | Dividend pool: <strong className="text-white">{inr2(status?.ledger_pool ?? 0)}</strong></span>
      </div>

      {error && <div className="border border-atlas-red/60 bg-atlas-red/10 text-atlas-red px-2 py-1">{error}</div>}
      {notice && <div className="border border-atlas-green/50 bg-atlas-green/10 text-atlas-green px-2 py-1">{notice}</div>}

      {/* First-run setup */}
      {mode === 'paper' && status && !hasHoldings && (
        <div className="border border-atlas-border p-2 flex items-center gap-2">
          <span className="text-atlas-text-dim">Create a paper portfolio (SYMBOL:QTY, comma separated):</span>
          <input value={initText} onChange={(e) => setInitText(e.target.value)} placeholder="ITC:1000,ONGC:500"
            className="bg-atlas-bg border border-atlas-border px-1 py-0.5 w-64 text-white" />
          <button onClick={initPortfolio} disabled={busy !== '' || !initText}
            className="px-3 py-0.5 bg-atlas-accent text-atlas-bg font-bold disabled:opacity-40">CREATE</button>
        </div>
      )}

      {/* Controls */}
      {(hasHoldings || mode === 'live') && (
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-atlas-text-dim">Max spend per run ₹
            <input type="number" value={maxDeploy} onChange={(e) => setMaxDeploy(Number(e.target.value))}
              className="ml-1 w-24 bg-atlas-bg border border-atlas-border px-1 py-0.5 text-white" />
          </label>
          <label className="text-atlas-text-dim">Count dividends from
            <input type="date" value={trackingStart} onChange={(e) => setTrackingStart(e.target.value)}
              className="ml-1 bg-atlas-bg border border-atlas-border px-1 py-0.5 text-white" />
          </label>
          <button onClick={() => run('plan')} disabled={busy !== ''}
            className="px-3 py-1 bg-atlas-accent text-atlas-bg font-bold disabled:opacity-40">
            {busy === 'plan' ? 'PLANNING…' : 'PLAN (dry run)'}
          </button>
          {mode === 'live' && (
            <input value={liveText} onChange={(e) => setLiveText(e.target.value)} placeholder='type LIVE to arm'
              className="bg-atlas-bg border border-atlas-orange/60 px-1 py-0.5 w-32 text-white" />
          )}
          <button onClick={execute}
            disabled={busy !== '' || !plan || status?.killed || (mode === 'live' && !liveReady)}
            className={`px-3 py-1 font-bold disabled:opacity-30 ${mode === 'live' ? 'bg-atlas-red text-white' : 'bg-atlas-green text-atlas-bg'}`}>
            {busy === 'exec' ? 'PLACING…' : `EXECUTE ${mode.toUpperCase()}`}
          </button>
        </div>
      )}

      {plan && (
        <div className="grid grid-cols-12 gap-2">
          {/* Ranking */}
          <div className="col-span-7 border border-atlas-border">
            <div className="px-2 py-1 bg-black/30 text-atlas-text-dim font-bold tracking-wider">
              TOP BY EXPECTED TOTAL RETURN — {plan.universe_size} liquid dividend payers scanned
            </div>
            <table className="w-full text-left">
              <thead className="text-atlas-text-dim border-b border-atlas-border">
                <tr>
                  <th className="pl-2">STOCK</th><th>SECTOR</th>
                  <th className="text-right">YIELD</th><th className="text-right">GROWTH</th>
                  <th className="text-right">VAL</th><th className="text-right">TREND</th>
                  <th className="text-right pr-2">SCORE</th>
                </tr>
              </thead>
              <tbody>
                {(plan.ranking || []).map((r: any) => (
                  <tr key={r.symbol} className="border-b border-atlas-border/20 hover:bg-white/5" title={(r.flags || []).join('; ')}>
                    <td className="pl-2 py-1 font-bold">{r.symbol}{r.flags?.length ? <span className="text-atlas-orange"> ⚠</span> : null}</td>
                    <td className="text-atlas-text-dim">{r.sector}</td>
                    <td className="text-right font-mono text-atlas-green">{r.dividend_yield_pct.toFixed(1)}%</td>
                    <td className="text-right font-mono">{r.growth_pct.toFixed(1)}%</td>
                    <td className="text-right font-mono">{r.valuation_adj_pct >= 0 ? '+' : ''}{r.valuation_adj_pct.toFixed(1)}</td>
                    <td className="text-right font-mono">{r.trend_adj_pct >= 0 ? '+' : ''}{r.trend_adj_pct.toFixed(1)}</td>
                    <td className="text-right pr-2 font-mono font-bold text-atlas-accent">{r.efficiency.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-2 py-1 text-atlas-text-dim">
              Score = quality-adjusted (yield + growth + valuation + trend). A heuristic ranking, not a forecast. ⚠ = data flag (hover).
            </div>
          </div>

          {/* Plan */}
          <div className="col-span-5 flex flex-col gap-2">
            <div className="border border-atlas-border">
              <div className="px-2 py-1 bg-black/30 text-atlas-text-dim font-bold tracking-wider">
                PLAN — {plan.status}{plan.detail ? ` (${plan.detail})` : ''}
              </div>
              <div className="px-2 py-1 text-atlas-text-dim">
                Dividend pool {inr2(plan.ledger_pool)} · broker funds {inr2(plan.broker_funds)} · <strong className="text-white">spendable {inr2(plan.spendable)}</strong>
              </div>
              {(plan.credits || []).map((c: any) => (
                <div key={c.symbol + c.ex_date} className="px-2 text-atlas-text-dim">
                  + {c.symbol} ex {c.ex_date}: {c.qty} × ₹{c.dps} = {inr2(c.gross)}{c.tds > 0 ? ` (TDS ${inr2(c.tds)})` : ''}
                </div>
              ))}
              {(p?.orders || []).length === 0 && <div className="px-2 py-2 text-atlas-orange">No orders: cash carried forward ({inr2(p?.carry ?? 0)}).</div>}
              {(p?.orders || []).map((o: any) => (
                <div key={o.symbol} className="px-2 py-1 border-t border-atlas-border/30">
                  <div className="flex justify-between">
                    <strong className="text-atlas-green">BUY {o.symbol} × {o.qty}</strong>
                    <span className="font-mono">limit ₹{o.limit_price.toFixed(2)} · {inr(o.est_value)} + {inr(o.est_charges)} charges</span>
                  </div>
                  <div className="text-atlas-text-dim">{o.reason} → {o.weight_after_pct}% of portfolio</div>
                </div>
              ))}
              {(p?.skipped || []).slice(0, 4).map(([s, why]: [string, string]) => (
                <div key={s} className="px-2 text-atlas-text-dim">skip {s}: {why}</div>
              ))}
              <div className="px-2 py-1 border-t border-atlas-border/30 text-atlas-text-dim">
                Total {inr(p?.cash_spent ?? 0)} · carry forward {inr2(p?.carry ?? 0)}
              </div>
            </div>
            {(plan.executed || []).length > 0 && (
              <div className="border border-atlas-border px-2 py-1">
                {(plan.executed as any[]).map((e, i) => (
                  <div key={i} className={e.status === 'FILLED' ? 'text-atlas-green' : 'text-atlas-orange'}>
                    {e.symbol}: {e.status}{e.reason ? ` — ${e.reason}` : ''}{e.qty ? ` ${e.qty} @ ₹${e.fill_price}` : ''}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Holdings + history */}
      {hasHoldings && (
        <div className="grid grid-cols-12 gap-2">
          <div className="col-span-4 border border-atlas-border p-2">
            <div className="text-atlas-text-dim font-bold tracking-wider mb-1">PAPER HOLDINGS</div>
            {Object.entries(status.holdings).map(([s, q]: any) => (
              <div key={s} className="flex justify-between"><span>{s}</span><span className="font-mono">{q}</span></div>
            ))}
          </div>
          <div className="col-span-8 border border-atlas-border p-2">
            <div className="text-atlas-text-dim font-bold tracking-wider mb-1">RECENT REINVESTMENTS &amp; DIVIDENDS</div>
            {[...(status.orders || [])].reverse().slice(0, 5).map((o: any, i: number) => (
              <div key={'o' + i}>BUY {o.symbol} × {o.qty} @ ₹{o.fill_price} — {o.status}</div>
            ))}
            {[...(status.credited_dividends || [])].reverse().slice(0, 5).map((d: any) => (
              <div key={d.symbol + d.ex_date} className="text-atlas-text-dim">DIV {d.symbol} ex {d.ex_date}: net {inr2(d.net)}</div>
            ))}
            {(status.orders || []).length === 0 && (status.credited_dividends || []).length === 0 &&
              <div className="text-atlas-text-dim">Nothing yet. Run PLAN, then EXECUTE PAPER.</div>}
          </div>
        </div>
      )}
    </div>
  );
}
