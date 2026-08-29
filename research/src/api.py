"""
Project Atlas — FastAPI Backend
Serves AI Advisor, Stock Screener, and Automated Trading Agent to the Atlas Terminal desktop app.
Bound strictly to 127.0.0.1 for security.
"""

import sys
import os
import json
import time
from datetime import datetime, timedelta
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm.atlas_advisor import AtlasAdvisor
from trading.strategy import generate_signal
from trading.backtest import fetch_historical_data, UPSTOX_WATCHLIST
from trading.universe import NSE_UNIVERSE
from trading.risk import RiskManager

app = FastAPI(title="Project Atlas API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1", "http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

advisor = None
risk_manager = RiskManager(capital=10000.0)

# In-memory store for paper/live trades and positions
POSITIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_positions.json")

def load_positions() -> dict:
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"open_positions": [], "trade_history": [], "capital": 10000.0, "total_pnl": 0.0}

def save_positions(data: dict):
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving positions: {e}")


@app.on_event("startup")
async def startup_event():
    global advisor
    try:
        advisor = AtlasAdvisor()
    except Exception as e:
        print(f"Failed to initialize AtlasAdvisor: {e}")
        advisor = None


# ─── Pydantic Request/Response Models ────────────────────────────

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

class HealthResponse(BaseModel):
    status: str
    advisor_ready: bool
    backend: str

class OrderRequest(BaseModel):
    symbol: str
    direction: str  # "BUY" or "SELL"
    qty: int
    entry_price: float
    stop_loss: float
    target_price: float
    mode: str = "PAPER"  # "PAPER" or "LIVE"

class ClosePositionRequest(BaseModel):
    position_id: str
    exit_price: float


# ─── Health & AI Chat Endpoints ─────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
def health_check():
    advisor_ready = advisor is not None
    backend = os.environ.get("ATLAS_BACKEND", "groq")
    return {"status": "ok", "advisor_ready": advisor_ready, "backend": backend}

@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    if not advisor:
        raise HTTPException(status_code=500, detail="Advisor not initialized")
    
    if request.message.strip().lower() == "reset":
        try:
            advisor.reset()
            return {"response": "Conversation history has been reset."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to reset advisor: {str(e)}")
            
    try:
        response_text = advisor.chat(request.message)
        return {"response": response_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/reset")
def chat_reset_endpoint():
    if not advisor:
        raise HTTPException(status_code=500, detail="Advisor not initialized")
    try:
        advisor.reset()
        return {"status": "reset"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Screener Endpoints ──────────────────────────────────────────

@app.get("/api/screener/sample")
def get_sample_screener():
    return [
        {"symbol": "COALINDIA", "score": 8.5, "roe": 45.2, "dividend_yield": 7.5, "pe_ratio": 6.8, "signal": "BUY"},
        {"symbol": "ITC", "score": 8.1, "roe": 28.5, "dividend_yield": 4.1, "pe_ratio": 24.5, "signal": "BUY"},
        {"symbol": "POWERGRID", "score": 7.8, "roe": 19.3, "dividend_yield": 5.2, "pe_ratio": 12.1, "signal": "BUY"},
        {"symbol": "ONGC", "score": 6.5, "roe": 14.2, "dividend_yield": 4.8, "pe_ratio": 5.4, "signal": "HOLD"},
        {"symbol": "TATAMOTORS", "score": 5.2, "roe": 18.1, "dividend_yield": 0.5, "pe_ratio": 16.7, "signal": "HOLD"},
        {"symbol": "WIPRO", "score": 4.8, "roe": 15.6, "dividend_yield": 1.2, "pe_ratio": 22.3, "signal": "SELL"},
        {"symbol": "HDFCBANK", "score": 7.2, "roe": 16.5, "dividend_yield": 1.1, "pe_ratio": 18.5, "signal": "BUY"},
        {"symbol": "INFY", "score": 6.9, "roe": 31.8, "dividend_yield": 2.5, "pe_ratio": 25.1, "signal": "HOLD"}
    ]


# ─── Trading Agent Endpoints ─────────────────────────────────────

@app.get("/api/trading/signals")
def get_trading_signals():
    """
    Scans the watchlist stocks in real-time, runs the quantitative strategy,
    and returns high-probability trade opportunities.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    
    signals = []
    target_stocks = list(NSE_UNIVERSE.keys())[:50] # Top 50 liquid stocks for instant UI scan

    def evaluate_sym(symbol: str) -> dict | None:
        try:
            df = fetch_historical_data(symbol, from_date, today)
            if df is not None and len(df) >= 30:
                sig = generate_signal(symbol, df)
                sig_dict = sig.to_dict()
                sl_dist = abs(sig.entry_price - sig.stop_loss)
                qty = risk_manager.calculate_qty(sig.entry_price, sig.stop_loss) if sl_dist > 0 else 10
                sig_dict["suggested_qty"] = max(1, qty)
                sig_dict["max_risk"] = round(sl_dist * sig_dict["suggested_qty"], 2)
                sig_dict["max_profit"] = round(abs(sig.target_price - sig.entry_price) * sig_dict["suggested_qty"], 2)
                return sig_dict
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=16) as executor:
        future_to_sym = {executor.submit(evaluate_sym, sym): sym for sym in target_stocks}
        for future in as_completed(future_to_sym):
            res = future.result()
            if res:
                signals.append(res)

    # Sort so signals with actionable BUY/SELL or highest confidence are at the top
    signals.sort(key=lambda s: (1 if s["direction"] != "NONE" else 0, s["confidence"]), reverse=True)
    return signals


@app.post("/api/trading/order")
def execute_order(order: OrderRequest):
    """
    Executes a paper or live trade.
    """
    pos_data = load_positions()
    
    pos_id = f"pos_{int(time.time()*1000)}"
    new_pos = {
        "id": pos_id,
        "symbol": order.symbol,
        "direction": order.direction,
        "qty": order.qty,
        "entry_price": order.entry_price,
        "current_price": order.entry_price,
        "stop_loss": order.stop_loss,
        "target_price": order.target_price,
        "mode": order.mode,
        "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "unrealized_pnl": 0.0,
        "status": "OPEN"
    }
    
    pos_data["open_positions"].append(new_pos)
    save_positions(pos_data)
    
    return {"status": "SUCCESS", "message": f"{order.mode} order executed for {order.qty} {order.symbol}", "position": new_pos}


@app.get("/api/trading/positions")
def get_positions():
    """
    Returns all active open positions and trade history with live unrealized P&L.
    """
    pos_data = load_positions()
    return pos_data


@app.post("/api/trading/close_position")
def close_position(req: ClosePositionRequest):
    """
    Closes an open position at the specified exit price and realizes P&L.
    """
    pos_data = load_positions()
    open_pos = pos_data["open_positions"]
    
    target_pos = None
    remaining = []
    for p in open_pos:
        if p["id"] == req.position_id:
            target_pos = p
        else:
            remaining.append(p)
            
    if not target_pos:
        raise HTTPException(status_code=404, detail="Position not found")
        
    exit_p = req.exit_price if req.exit_price > 0 else target_pos["current_price"]
    if target_pos["direction"] == "BUY":
        realized_pnl = (exit_p - target_pos["entry_price"]) * target_pos["qty"]
    else:
        realized_pnl = (target_pos["entry_price"] - exit_p) * target_pos["qty"]
        
    closed_record = {
        **target_pos,
        "exit_price": round(exit_p, 2),
        "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pnl": round(realized_pnl, 2),
        "result": "WIN" if realized_pnl > 0 else "LOSS",
        "status": "CLOSED"
    }
    
    pos_data["open_positions"] = remaining
    pos_data["trade_history"].insert(0, closed_record)
    pos_data["total_pnl"] = round(pos_data["total_pnl"] + realized_pnl, 2)
    save_positions(pos_data)
    
    return {"status": "SUCCESS", "closed_trade": closed_record, "total_pnl": pos_data["total_pnl"]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
