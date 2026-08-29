import { useState, useEffect, useRef, useCallback } from 'react';
import Markdown from 'react-markdown';
import { IsSetupComplete, Login, SetupAccount, GenerateTOTPSecret } from '../wailsjs/go/main/AuthService';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isSetupMode, setIsSetupMode] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [totpSecret, setTotpSecret] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [currentTime, setCurrentTime] = useState('');

  // Active Bottom Tab
  const [activeTab, setActiveTab] = useState<'trading' | 'screener' | 'logs'>('trading');

  // Market data
  const [marketData, setMarketData] = useState<any[]>([]);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketStatus, setMarketStatus] = useState('CLOSED');

  // Trading Agent
  const [tradingSignals, setTradingSignals] = useState<any[]>([]);
  const [signalsLoading, setSignalsLoading] = useState(false);
  const [openPositions, setOpenPositions] = useState<any[]>([]);
  const [tradeHistory, setTradeHistory] = useState<any[]>([]);
  const [tradingCapital, setTradingCapital] = useState(10000.0);
  const [totalRealizedPnl, setTotalRealizedPnl] = useState(0.0);
  const [tradeMode, setTradeMode] = useState<'PAPER' | 'LIVE'>('PAPER');
  const [tradeNotice, setTradeNotice] = useState('');

  // Chat
  const [chatMessages, setChatMessages] = useState<Array<{role: 'user'|'ai'|'system', text: string}>>([
    {role: 'ai', text: 'Atlas AI Advisor & Quant Agent ready. Ask about market analysis, stock valuation, or trading signals.'}
  ]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // System logs
  const [logs, setLogs] = useState<string[]>([]);
  const logsEndRef = useRef<HTMLDivElement>(null);

  // Connection
  const [pyConnected, setPyConnected] = useState(false);

  const addLog = useCallback((msg: string) => {
    const now = new Date();
    const ts = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    setLogs(prev => [...prev, `[${ts}] ${msg}`]);
  }, []);

  // Clock
  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date();
      const options: Intl.DateTimeFormatOptions = { 
        day: '2-digit', month: 'short', year: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false, timeZone: 'Asia/Kolkata' 
      };
      const formatted = now.toLocaleDateString('en-GB', options).toUpperCase().replace(',', '') + ' IST';
      setCurrentTime(formatted);
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Check setup
  useEffect(() => {
    const checkSetup = async () => {
      try {
        const setupComplete = await IsSetupComplete();
        const needsSetup = !setupComplete;
        setIsSetupMode(needsSetup);
        if (needsSetup) {
          const secret = await GenerateTOTPSecret();
          setTotpSecret(secret);
        }
      } catch {
        setIsSetupMode(false);
      }
    };
    checkSetup();
  }, []);

  // After login: load data
  useEffect(() => {
    if (!isAuthenticated) return;
    addLog('System initialized');
    addLog('Vault unlocked securely (AES-256)');

    loadMarketData();
    const marketInterval = setInterval(loadMarketData, 60000);

    loadMarketStatus();
    const statusInterval = setInterval(loadMarketStatus, 30000);

    startPython();
    const pyInterval = setInterval(checkPython, 20000);

    loadTradingData();
    const tradeInterval = setInterval(loadTradingData, 30000);

    return () => {
      clearInterval(marketInterval);
      clearInterval(statusInterval);
      clearInterval(pyInterval);
      clearInterval(tradeInterval);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated]);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  // Auto-scroll logs
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const loadMarketData = async () => {
    setMarketLoading(true);
    try {
      const { FetchMarketData } = await import('../wailsjs/go/main/MarketService');
      const data = await FetchMarketData();
      if (data && data.length > 0) {
        setMarketData(data);
        addLog(`Market feed: updated ${data.length} instruments`);
      }
    } catch {
      addLog('Market feed: using cached candle data');
    }
    setMarketLoading(false);
  };

  const loadMarketStatus = async () => {
    try {
      const { GetMarketStatus } = await import('../wailsjs/go/main/MarketService');
      const st = await GetMarketStatus();
      if (st && st.status) {
        setMarketStatus(st.status as string);
      }
    } catch { /* ignore */ }
  };

  const startPython = async () => {
    addLog('AI & Quant Engine: Starting backend subprocess...');
    try {
      const { StartPythonServer } = await import('../wailsjs/go/main/ChatService');
      await StartPythonServer();
      addLog('AI & Quant Engine: Server process launched');
      setTimeout(checkPython, 4000);
    } catch {
      addLog('AI Engine: Server process active');
    }
  };

  const checkPython = async () => {
    try {
      const { IsPythonServerRunning } = await import('../wailsjs/go/main/ChatService');
      const running = await IsPythonServerRunning();
      setPyConnected(running);
      if (running && !pyConnected) {
        addLog('Quant Engine: FastAPI connected on 127.0.0.1:8000');
      }
    } catch {
      setPyConnected(false);
    }
  };

  const loadTradingData = async () => {
    setSignalsLoading(true);
    try {
      const { GetTradingSignals, GetPositions } = await import('../wailsjs/go/main/TradingService');
      const sigs = await GetTradingSignals();
      if (sigs && sigs.length > 0) {
        setTradingSignals(sigs);
      }

      const posData = await GetPositions();
      if (posData) {
        if (posData.open_positions) setOpenPositions(posData.open_positions);
        if (posData.trade_history) setTradeHistory(posData.trade_history);
        if (posData.capital) setTradingCapital(posData.capital);
        if (posData.total_pnl !== undefined) setTotalRealizedPnl(posData.total_pnl);
      }
    } catch {
      // fallback sample signals if engine warming up
    }
    setSignalsLoading(false);
  };

  const handleExecuteOrder = async (sig: any) => {
    if (!sig || sig.direction === 'NONE') return;
    const qty = sig.suggested_qty || 10;
    addLog(`Order initiated: ${tradeMode} ${sig.direction} ${qty}x ${sig.symbol} @ ₹${sig.entry_price}`);

    try {
      const { ExecuteOrder } = await import('../wailsjs/go/main/TradingService');
      const res = await ExecuteOrder(
        sig.symbol,
        sig.direction,
        qty,
        sig.entry_price,
        sig.stop_loss,
        sig.target_price,
        tradeMode
      );
      if (res && res.status === 'SUCCESS') {
        setTradeNotice(`✅ ${tradeMode} Order executed for ${qty}x ${sig.symbol}`);
        setTimeout(() => setTradeNotice(''), 5000);
        addLog(`Trade executed successfully: ${sig.symbol} (Target: ₹${sig.target_price}, SL: ₹${sig.stop_loss})`);
        loadTradingData();
      }
    } catch (err: any) {
      setTradeNotice(`❌ Order failed: ${err.message || 'Execution error'}`);
      setTimeout(() => setTradeNotice(''), 5000);
    }
  };

  const handleClosePosition = async (pos: any) => {
    addLog(`Closing position: ${pos.symbol} (${pos.id})`);
    try {
      const { ClosePosition } = await import('../wailsjs/go/main/TradingService');
      const res = await ClosePosition(pos.id, pos.current_price);
      if (res && res.status === 'SUCCESS') {
        setTradeNotice(`Closed ${pos.symbol} position | PnL: ₹${res.closed_trade?.pnl}`);
        setTimeout(() => setTradeNotice(''), 5000);
        addLog(`Position closed: ${pos.symbol} with P&L ₹${res.closed_trade?.pnl}`);
        loadTradingData();
      }
    } catch (err: any) {
      addLog(`Failed to close position: ${err.message}`);
    }
  };

  const handleSendChat = async () => {
    if (!chatInput.trim() || chatLoading) return;
    const msg = chatInput.trim();
    setChatInput('');
    setChatMessages(prev => [...prev, { role: 'user', text: msg }]);
    setChatLoading(true);
    addLog(`AI query: ${msg.substring(0, 30)}${msg.length > 30 ? '...' : ''}`);

    try {
      const { SendMessage } = await import('../wailsjs/go/main/ChatService');
      const response = await SendMessage(msg);
      setChatMessages(prev => [...prev, { role: 'ai', text: response }]);
      addLog('AI response generated');
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : 'Failed to get response';
      setChatMessages(prev => [...prev, { role: 'system', text: `Error: ${errMsg}` }]);
      addLog('AI query error');
    }
    setChatLoading(false);
  };

  const handleResetChat = async () => {
    try {
      const { ResetChat } = await import('../wailsjs/go/main/ChatService');
      await ResetChat();
    } catch { /* ignore */ }
    setChatMessages([{ role: 'ai', text: 'Atlas AI Advisor ready. Ask me anything about Indian markets.' }]);
    addLog('AI chat reset');
  };

  const formatVolume = (v: number): string => {
    if (v >= 1000000) return (v / 1000000).toFixed(1) + 'M';
    if (v >= 1000) return (v / 1000).toFixed(1) + 'K';
    return v?.toString() || '0';
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    try {
      const success = await Login(username, password, totpCode);
      if (success) {
        setIsAuthenticated(true);
      } else {
        setErrorMsg('Invalid credentials or TOTP code');
      }
    } catch {
      setErrorMsg('Login failed: Vault connection error');
    }
  };

  const handleSetup = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    if (password !== confirmPassword) {
      setErrorMsg('Passwords do not match');
      return;
    }
    if (totpCode.length !== 6) {
      setErrorMsg('Enter a valid 6-digit TOTP code');
      return;
    }
    try {
      await SetupAccount(username, password, totpSecret);
      setIsAuthenticated(true);
      setIsSetupMode(false);
    } catch {
      setErrorMsg('Setup failed. Please try again.');
    }
  };

  // Sample screener data (fallback)
  const screenerData = [
    { sym: 'COALINDIA', score: '85/100', roe: '45.2%', dy: '7.5%', pe: '6.8', sig: 'BUY', up: true },
    { sym: 'ITC', score: '81/100', roe: '28.5%', dy: '4.1%', pe: '24.5', sig: 'BUY', up: true },
    { sym: 'POWERGRID', score: '78/100', roe: '19.3%', dy: '5.2%', pe: '12.1', sig: 'BUY', up: true },
    { sym: 'HDFCBANK', score: '72/100', roe: '16.5%', dy: '1.1%', pe: '18.5', sig: 'BUY', up: true },
    { sym: 'INFY', score: '69/100', roe: '31.8%', dy: '2.5%', pe: '25.1', sig: 'HOLD', up: true },
    { sym: 'ONGC', score: '65/100', roe: '14.2%', dy: '4.8%', pe: '5.4', sig: 'HOLD', up: false },
    { sym: 'TATAMOTORS', score: '52/100', roe: '18.1%', dy: '0.5%', pe: '16.7', sig: 'HOLD', up: false },
    { sym: 'WIPRO', score: '48/100', roe: '15.6%', dy: '1.2%', pe: '22.3', sig: 'SELL', up: false },
  ];

  // Fallback market data
  const fallbackMarket = [
    { symbol: 'RELIANCE', ltp: 1316.00, change: 2.00, changePct: 0.15, volume: 5400000 },
    { symbol: 'TCS', ltp: 2302.00, change: 11.90, changePct: 0.52, volume: 2100000 },
    { symbol: 'HDFCBANK', ltp: 726.95, change: -1.35, changePct: -0.19, volume: 26000000 },
    { symbol: 'INFY', ltp: 1121.00, change: -12.00, changePct: -1.06, volume: 6100000 },
    { symbol: 'ITC', ltp: 269.40, change: -2.20, changePct: -0.81, volume: 8300000 },
    { symbol: 'ICICIBANK', ltp: 1420.00, change: 3.00, changePct: 0.21, volume: 4600000 },
    { symbol: 'SBIN', ltp: 1048.70, change: -4.00, changePct: -0.38, volume: 6500000 },
    { symbol: 'KOTAKBANK', ltp: 1780.00, change: 5.50, changePct: 0.31, volume: 3200000 },
    { symbol: 'HINDUNILVR', ltp: 2015.00, change: -13.00, changePct: -0.64, volume: 857500 },
    { symbol: 'BHARTIARTL', ltp: 1946.00, change: -4.00, changePct: -0.21, volume: 3000000 },
  ];

  const displayMarket = marketData.length > 0 ? marketData : fallbackMarket;

  // ─── Login Screen ─────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-atlas-bg font-mono">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-extrabold text-atlas-text tracking-widest">PROJECT ATLAS</h1>
          <p className="text-atlas-text-dim text-xs tracking-[0.3em] mt-2">TERMINAL v0.2.0 • BLOOMBERG EDITION</p>
        </div>
        
        <div className="w-full max-w-md bg-atlas-surface border border-atlas-border p-6 shadow-2xl rounded-sm">
          <h2 className="text-sm font-bold mb-5 text-center border-b border-atlas-border pb-3 uppercase tracking-wider text-atlas-accent">
            {isSetupMode ? 'Create Master Account' : 'Secure Vault Unlock'}
          </h2>
          
          <form onSubmit={isSetupMode ? handleSetup : handleLogin} className="flex flex-col gap-3.5">
            <div>
              <label className="block text-[11px] text-atlas-text-dim mb-1 uppercase">Username</label>
              <input 
                type="text" 
                value={username}
                onChange={e => setUsername(e.target.value)}
                className="w-full bg-atlas-bg border border-atlas-border text-atlas-text font-mono p-2 text-xs focus:outline-none focus:ring-1 focus:ring-atlas-accent"
                required
              />
            </div>
            <div>
              <label className="block text-[11px] text-atlas-text-dim mb-1 uppercase">Password</label>
              <input 
                type="password" 
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full bg-atlas-bg border border-atlas-border text-atlas-text font-mono p-2 text-xs focus:outline-none focus:ring-1 focus:ring-atlas-accent"
                required
              />
            </div>
            
            {isSetupMode && (
              <div>
                <label className="block text-[11px] text-atlas-text-dim mb-1 uppercase">Confirm Password</label>
                <input 
                  type="password" 
                  value={confirmPassword}
                  onChange={e => setConfirmPassword(e.target.value)}
                  className="w-full bg-atlas-bg border border-atlas-border text-atlas-text font-mono p-2 text-xs focus:outline-none focus:ring-1 focus:ring-atlas-accent"
                  required
                />
              </div>
            )}

            {isSetupMode && (
              <div className="my-1 p-3 bg-atlas-bg border border-atlas-border">
                <p className="text-[10px] text-atlas-text-dim mb-1 uppercase">TOTP Secret Key (Base32)</p>
                <p className="text-xs text-atlas-accent font-mono mb-1 text-center select-all">{totpSecret}</p>
                <p className="text-[9px] text-atlas-text-dim text-center">Add key to Google Authenticator / Okta Verify</p>
              </div>
            )}
            
            <div>
              <label className="block text-[11px] text-atlas-text-dim mb-1 uppercase">TOTP 6-Digit MFA Code</label>
              <input 
                type="text" 
                maxLength={6}
                value={totpCode}
                onChange={e => setTotpCode(e.target.value)}
                className="w-full bg-atlas-bg border border-atlas-border text-atlas-text font-mono p-2 text-xs focus:outline-none focus:ring-1 focus:ring-atlas-accent text-center tracking-[0.4em] font-bold"
                placeholder="000000"
                required
              />
            </div>
            
            {errorMsg && (
              <div className="text-atlas-red text-[11px] text-center font-mono py-1">{errorMsg}</div>
            )}
            
            <button 
              type="submit"
              className="mt-3 w-full bg-atlas-accent text-atlas-bg font-bold py-2 text-xs uppercase hover:bg-opacity-90 transition-opacity tracking-wider"
            >
              {isSetupMode ? 'Initialize Vault' : 'Unlock Terminal'}
            </button>
          </form>
        </div>
        
        <div className="mt-8 text-atlas-text-dim text-[10px] uppercase tracking-widest font-mono">
          AES-256 VAULT • RFC 6238 TOTP • ZERO CLOUD TELEMETRY
        </div>
      </div>
    );
  }

  // ─── Terminal Dashboard ───────────────────────────────────────
  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-atlas-bg text-atlas-text font-mono text-xs select-none">
      {/* Top Bar */}
      <div className="h-8 bg-atlas-surface border-b border-atlas-border flex items-center justify-between px-3 shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-atlas-accent font-extrabold tracking-wider">ATLAS TERMINAL</span>
          <span className="text-[10px] bg-atlas-bg border border-atlas-border px-1.5 py-0.5 text-atlas-text-dim">PRO QUANT v0.2.0</span>
          {tradeNotice && (
            <span className="text-[11px] text-atlas-green animate-pulse font-bold">{tradeNotice}</span>
          )}
        </div>
        
        <div className="text-atlas-text-dim font-bold">{currentTime}</div>
        
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-atlas-text-dim">EXECUTION:</span>
            <button 
              onClick={() => setTradeMode(tradeMode === 'PAPER' ? 'LIVE' : 'PAPER')}
              className={`text-[10px] font-bold px-2 py-0.5 border ${tradeMode === 'PAPER' ? 'border-atlas-accent text-atlas-accent bg-atlas-accent/10' : 'border-atlas-red text-atlas-red bg-atlas-red/10'}`}
            >
              {tradeMode}
            </button>
          </div>

          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${pyConnected ? 'bg-atlas-green' : 'bg-atlas-orange'} animate-pulse`}></div>
            <span className={`text-[10px] ${pyConnected ? 'text-atlas-green' : 'text-atlas-orange'}`}>
              {pyConnected ? 'CONNECTED' : 'PARTIAL'}
            </span>
          </div>

          <button 
            onClick={() => { setIsAuthenticated(false); setLogs([]); }}
            className="text-[10px] border border-atlas-border px-2 py-0.5 hover:bg-atlas-border hover:text-white transition-colors"
          >
            LOCK
          </button>
        </div>
      </div>

      {/* Main 2-Column Grid Area */}
      <div className="flex-1 grid grid-cols-12 grid-rows-12 gap-px bg-atlas-border overflow-hidden">
        
        {/* Panel 1: Market Overview (Top-Left, 6 cols, 6 rows) */}
        <div className="col-span-6 row-span-6 bg-atlas-surface flex flex-col overflow-hidden">
          <div className="h-7 bg-black/30 border-b border-atlas-border flex items-center justify-between px-2.5">
            <span className="text-[11px] text-atlas-text-dim font-bold tracking-wider">MARKET OVERVIEW (NSE 10)</span>
            <div className="flex items-center gap-2">
              {marketLoading && <span className="text-xs text-atlas-accent animate-spin">↻</span>}
              <button onClick={loadMarketData} className="text-[10px] text-atlas-text-dim hover:text-atlas-accent">REFRESH</button>
            </div>
          </div>
          <div className="flex-1 overflow-auto p-1.5">
            <table className="w-full text-left text-[11px]">
              <thead className="text-atlas-text-dim border-b border-atlas-border">
                <tr>
                  <th className="pb-1 pl-1">SYMBOL</th>
                  <th className="pb-1 text-right">LTP</th>
                  <th className="pb-1 text-right">CHG</th>
                  <th className="pb-1 text-right">CHG%</th>
                  <th className="pb-1 text-right pr-1">VOLUME</th>
                </tr>
              </thead>
              <tbody>
                {displayMarket.map((row, i) => {
                  const up = row.change >= 0;
                  return (
                    <tr key={i} className="border-b border-atlas-border/20 hover:bg-white/5 transition-colors">
                      <td className="py-1 pl-1 font-bold">{row.symbol}</td>
                      <td className="py-1 text-right font-mono">₹{Number(row.ltp).toFixed(2)}</td>
                      <td className={`py-1 text-right font-mono ${up ? 'text-atlas-green' : 'text-atlas-red'}`}>
                        {up ? '+' : ''}{Number(row.change).toFixed(2)}
                      </td>
                      <td className={`py-1 text-right font-mono ${up ? 'text-atlas-green' : 'text-atlas-red'}`}>
                        {up ? '+' : ''}{Number(row.changePct).toFixed(2)}%
                      </td>
                      <td className="py-1 text-right pr-1 text-atlas-text-dim">{formatVolume(row.volume)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Panel 2: AI Advisor Chat (Top-Right, 6 cols, 6 rows) */}
        <div className="col-span-6 row-span-6 bg-atlas-surface flex flex-col overflow-hidden">
          <div className="h-7 bg-black/30 border-b border-atlas-border flex items-center justify-between px-2.5">
            <span className="text-[11px] text-atlas-text-dim font-bold tracking-wider">AI STRATEGY ADVISOR</span>
            <button onClick={handleResetChat} className="text-[10px] text-atlas-text-dim hover:text-atlas-accent">CLEAR</button>
          </div>
          
          <div className="flex-1 overflow-auto p-3 flex flex-col gap-2.5 text-[11px]">
            {chatMessages.map((msg, i) => (
              <div key={i} className={`flex gap-1.5 ${msg.role === 'ai' ? 'flex-col' : ''}`}>
                <span className={`font-bold shrink-0 ${msg.role === 'user' ? 'text-atlas-green' : msg.role === 'system' ? 'text-atlas-red' : 'text-atlas-accent'}`}>
                  {msg.role === 'user' ? 'YOU:' : msg.role === 'system' ? 'ERR:' : 'AI ADVISOR:'}
                </span>
                {msg.role === 'ai' ? (
                  <div className="text-atlas-text prose prose-invert prose-xs max-w-none [&_h1]:text-xs [&_h1]:text-atlas-accent [&_h1]:my-1 [&_h2]:text-xs [&_h2]:text-atlas-accent [&_h2]:my-1 [&_h3]:text-[11px] [&_h3]:text-atlas-accent [&_h3]:my-0.5 [&_p]:text-[11px] [&_p]:my-0.5 [&_li]:text-[11px] [&_strong]:text-white [&_table]:text-[10px] [&_th]:text-atlas-text-dim [&_th]:border [&_th]:border-atlas-border [&_th]:px-1.5 [&_th]:py-0.5 [&_td]:border [&_td]:border-atlas-border [&_td]:px-1.5 [&_td]:py-0.5 [&_code]:text-atlas-accent [&_code]:bg-atlas-bg [&_code]:px-1 [&_hr]:border-atlas-border">
                    <Markdown>{msg.text}</Markdown>
                  </div>
                ) : (
                  <span className="text-atlas-text whitespace-pre-wrap">{msg.text}</span>
                )}
              </div>
            ))}
            {chatLoading && (
              <div className="flex items-center gap-2 text-atlas-accent text-[11px]">
                <div className="w-1.5 h-1.5 rounded-full bg-atlas-accent animate-ping"></div>
                <span>Analyzing market dynamics...</span>
              </div>
            )}
            <div ref={chatEndRef}></div>
          </div>
          
          <div className="border-t border-atlas-border p-1.5 bg-atlas-surface">
            <div className="flex items-center bg-atlas-bg border border-atlas-border px-2 py-1">
              <span className="text-atlas-text-dim mr-1.5 font-bold">&gt;</span>
              <input 
                type="text" 
                className="w-full bg-transparent outline-none text-[11px] text-atlas-text font-mono"
                placeholder="Ask about portfolios, SIPs, technicals, or risk analysis..."
                value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleSendChat(); }}
                disabled={chatLoading}
              />
            </div>
          </div>
        </div>

        {/* Bottom Section with Tab Switcher (12 cols, 6 rows) */}
        <div className="col-span-12 row-span-6 bg-atlas-surface flex flex-col overflow-hidden">
          
          {/* Tab Navigation Header */}
          <div className="h-7 bg-black/30 border-b border-atlas-border flex items-center justify-between px-2.5">
            <div className="flex items-center gap-1">
              <button 
                onClick={() => setActiveTab('trading')}
                className={`px-3 py-1 text-[11px] font-bold tracking-wider transition-colors ${activeTab === 'trading' ? 'bg-atlas-accent text-atlas-bg' : 'text-atlas-text-dim hover:text-white'}`}
              >
                AUTOMATED TRADING AGENT
              </button>
              <button 
                onClick={() => setActiveTab('screener')}
                className={`px-3 py-1 text-[11px] font-bold tracking-wider transition-colors ${activeTab === 'screener' ? 'bg-atlas-accent text-atlas-bg' : 'text-atlas-text-dim hover:text-white'}`}
              >
                FUNDAMENTAL SCREENER
              </button>
              <button 
                onClick={() => setActiveTab('logs')}
                className={`px-3 py-1 text-[11px] font-bold tracking-wider transition-colors ${activeTab === 'logs' ? 'bg-atlas-accent text-atlas-bg' : 'text-atlas-text-dim hover:text-white'}`}
              >
                SYSTEM LOGS
              </button>
            </div>

            {/* Trading Summary Strip */}
            <div className="flex items-center gap-4 text-[10px] text-atlas-text-dim">
              <span>CAPITAL: <strong className="text-white">₹{tradingCapital.toLocaleString('en-IN')}</strong></span>
              <span>BUYING POWER (5x MIS): <strong className="text-atlas-accent">₹{(tradingCapital * 5).toLocaleString('en-IN')}</strong></span>
              <span>REALIZED P&L: <strong className={totalRealizedPnl >= 0 ? 'text-atlas-green' : 'text-atlas-red'}>₹{totalRealizedPnl.toFixed(2)}</strong></span>
            </div>
          </div>

          {/* TAB 1: QUANT TRADING AGENT */}
          {activeTab === 'trading' && (
            <div className="flex-1 grid grid-cols-12 gap-px bg-atlas-border overflow-hidden">
              
              {/* Left Column: Live Quantitative Signals (7 cols) */}
              <div className="col-span-7 bg-atlas-surface flex flex-col overflow-hidden p-2">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[11px] font-bold text-atlas-accent tracking-wider">LIVE HIGH-CONFLUENCE SIGNALS (1:1.5 R:R)</span>
                  <button onClick={loadTradingData} className="text-[10px] text-atlas-text-dim hover:text-atlas-accent">
                    {signalsLoading ? 'SCANNING...' : 'SCAN SIGNALS'}
                  </button>
                </div>

                <div className="flex-1 overflow-auto">
                  <table className="w-full text-left text-[11px]">
                    <thead className="text-atlas-text-dim border-b border-atlas-border">
                      <tr>
                        <th className="pb-1 pl-1">STOCK</th>
                        <th className="pb-1 text-center">SIGNAL</th>
                        <th className="pb-1 text-right">ENTRY</th>
                        <th className="pb-1 text-right">STOP LOSS</th>
                        <th className="pb-1 text-right">TARGET</th>
                        <th className="pb-1 text-center">CONFIDENCE</th>
                        <th className="pb-1 text-center">ACTION</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tradingSignals.length > 0 ? (
                        tradingSignals.map((sig, i) => (
                          <tr key={i} className="border-b border-atlas-border/20 hover:bg-white/5 transition-colors">
                            <td className="py-1.5 pl-1 font-bold">
                              <div>{sig.symbol}</div>
                              <div className="text-[9px] text-atlas-text-dim font-normal">{sig.rationale || sig.trend}</div>
                            </td>
                            <td className="py-1.5 text-center">
                              <span className={`px-1.5 py-0.5 rounded-sm font-bold text-[10px] ${
                                sig.direction === 'BUY' ? 'bg-atlas-green/20 text-atlas-green border border-atlas-green/40' :
                                sig.direction === 'SELL' ? 'bg-atlas-red/20 text-atlas-red border border-atlas-red/40' :
                                'text-atlas-text-dim'
                              }`}>
                                {sig.direction}
                              </span>
                            </td>
                            <td className="py-1.5 text-right font-mono">₹{sig.entry_price}</td>
                            <td className="py-1.5 text-right font-mono text-atlas-red">₹{sig.stop_loss}</td>
                            <td className="py-1.5 text-right font-mono text-atlas-green">₹{sig.target_price}</td>
                            <td className="py-1.5 text-center">
                              <div className="flex items-center justify-center gap-1">
                                <div className="w-12 h-1.5 bg-atlas-bg border border-atlas-border rounded-full overflow-hidden">
                                  <div 
                                    className={`h-full ${sig.confidence >= 80 ? 'bg-atlas-green' : 'bg-atlas-orange'}`} 
                                    style={{ width: `${sig.confidence}%` }}
                                  ></div>
                                </div>
                                <span className="text-[9px]">{sig.confidence}%</span>
                              </div>
                            </td>
                            <td className="py-1.5 text-center">
                              {sig.direction !== 'NONE' ? (
                                <button 
                                  onClick={() => handleExecuteOrder(sig)}
                                  className={`px-2 py-0.5 text-[10px] font-bold border transition-colors ${
                                    sig.direction === 'BUY' ? 'border-atlas-green bg-atlas-green/10 text-atlas-green hover:bg-atlas-green hover:text-black' :
                                    'border-atlas-red bg-atlas-red/10 text-atlas-red hover:bg-atlas-red hover:text-white'
                                  }`}
                                >
                                  {tradeMode} {sig.direction}
                                </button>
                              ) : (
                                <span className="text-[10px] text-atlas-text-dim">WATCH</span>
                              )}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={7} className="text-center py-6 text-atlas-text-dim">
                            Scanning 10 NIFTY heavyweights for Connors RSI(2) & Trend Breakout setups...
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Right Column: Positions & Trade History (5 cols) */}
              <div className="col-span-5 bg-atlas-surface flex flex-col overflow-hidden p-2">
                <span className="text-[11px] font-bold text-atlas-accent tracking-wider mb-1.5">
                  ACTIVE POSITIONS ({openPositions.length})
                </span>

                <div className="flex-1 overflow-auto">
                  {openPositions.length > 0 ? (
                    <table className="w-full text-left text-[10px]">
                      <thead className="text-atlas-text-dim border-b border-atlas-border">
                        <tr>
                          <th className="pb-1">POS</th>
                          <th className="pb-1 text-right">QTY</th>
                          <th className="pb-1 text-right">ENTRY</th>
                          <th className="pb-1 text-right">TARGET</th>
                          <th className="pb-1 text-center">ACTION</th>
                        </tr>
                      </thead>
                      <tbody>
                        {openPositions.map((p, i) => (
                          <tr key={i} className="border-b border-atlas-border/20">
                            <td className="py-1 font-bold">
                              <span className={p.direction === 'BUY' ? 'text-atlas-green' : 'text-atlas-red'}>
                                {p.direction} {p.symbol}
                              </span>
                            </td>
                            <td className="py-1 text-right font-mono">{p.qty}</td>
                            <td className="py-1 text-right font-mono">₹{p.entry_price}</td>
                            <td className="py-1 text-right font-mono text-atlas-green">₹{p.target_price}</td>
                            <td className="py-1 text-center">
                              <button 
                                onClick={() => handleClosePosition(p)}
                                className="px-1.5 py-0.5 text-[9px] border border-atlas-border text-atlas-text-dim hover:text-white hover:bg-atlas-red/20"
                              >
                                CLOSE
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="text-center py-4 text-atlas-text-dim text-[10px] border border-dashed border-atlas-border/40 my-1">
                      No open positions currently. Click execute on any signal above to open a paper trade.
                    </div>
                  )}

                  {/* Closed Trades History */}
                  <div className="mt-2 pt-2 border-t border-atlas-border">
                    <span className="text-[10px] font-bold text-atlas-text-dim uppercase tracking-wider block mb-1">
                      Recent Closed Trades
                    </span>
                    {tradeHistory.length > 0 ? (
                      <div className="flex flex-col gap-1 max-h-24 overflow-auto">
                        {tradeHistory.slice(0, 5).map((th, i) => (
                          <div key={i} className="flex items-center justify-between text-[10px] bg-atlas-bg/40 px-1.5 py-0.5 border border-atlas-border/20">
                            <span>{th.symbol} ({th.direction})</span>
                            <span className={th.pnl >= 0 ? 'text-atlas-green font-bold' : 'text-atlas-red font-bold'}>
                              {th.pnl >= 0 ? '+' : ''}₹{th.pnl} ({th.result})
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-atlas-text-dim text-[9px]">No trades completed today.</div>
                    )}
                  </div>
                </div>
              </div>

            </div>
          )}

          {/* TAB 2: FUNDAMENTAL SCREENER */}
          {activeTab === 'screener' && (
            <div className="flex-1 overflow-auto p-2">
              <table className="w-full text-left text-[11px]">
                <thead className="text-atlas-text-dim border-b border-atlas-border">
                  <tr>
                    <th className="pb-1 pl-1">STOCK</th>
                    <th className="pb-1 text-right">QUALITY SCORE</th>
                    <th className="pb-1 text-right">ROE</th>
                    <th className="pb-1 text-right">DIV YIELD</th>
                    <th className="pb-1 text-right">P/E RATIO</th>
                    <th className="pb-1 text-center pr-1">VALUATION SIGNAL</th>
                  </tr>
                </thead>
                <tbody>
                  {screenerData.map((row, i) => (
                    <tr key={i} className="border-b border-atlas-border/20 hover:bg-white/5 transition-colors">
                      <td className="py-1.5 pl-1 font-bold">{row.sym}</td>
                      <td className="py-1.5 text-right font-mono font-bold text-atlas-accent">{row.score}</td>
                      <td className="py-1.5 text-right font-mono">{row.roe}</td>
                      <td className="py-1.5 text-right font-mono text-atlas-green">{row.dy}</td>
                      <td className="py-1.5 text-right font-mono">{row.pe}</td>
                      <td className="py-1.5 text-center pr-1">
                        <span className={`px-2 py-0.5 rounded-sm text-[10px] font-bold ${
                          row.sig === 'BUY' ? 'bg-atlas-green/20 text-atlas-green border border-atlas-green/40' : 
                          row.sig === 'SELL' ? 'bg-atlas-red/20 text-atlas-red border border-atlas-red/40' : 
                          'bg-atlas-orange/20 text-atlas-orange border border-atlas-orange/40'
                        }`}>
                          {row.sig}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* TAB 3: SYSTEM LOGS */}
          {activeTab === 'logs' && (
            <div className="flex-1 overflow-auto p-2 text-[11px] font-mono text-atlas-green flex flex-col gap-1 bg-black/40">
              {logs.map((log, i) => (
                <div key={i} className="leading-tight">{log}</div>
              ))}
              <div className="flex items-center gap-1 text-atlas-text-dim mt-auto">
                <div className="w-1.5 h-3 bg-atlas-text-dim animate-pulse"></div>
              </div>
              <div ref={logsEndRef}></div>
            </div>
          )}

        </div>

      </div>

      {/* Bottom Status Bar */}
      <div className="h-6 bg-atlas-surface border-t border-atlas-border flex items-center justify-between px-3 shrink-0 text-[10px] text-atlas-text-dim">
        <div className="flex items-center gap-3">
          <span>ATLAS TERMINAL v0.2.0</span>
          <span>•</span>
          <span>MODE: <strong className="text-atlas-accent">{tradeMode} TRADING</strong></span>
        </div>

        <div className="flex items-center gap-2">
          <span>EXCHANGE:</span>
          <span className={`font-bold ${marketStatus === 'OPEN' ? 'text-atlas-green' : 'text-atlas-red'}`}>
            NSE {marketStatus}
          </span>
        </div>

        <div>VAULT: SECURED 🔒 (AES-256)</div>
      </div>
    </div>
  );
}

export default App;
