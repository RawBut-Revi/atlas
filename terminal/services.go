package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// ─── Market Service ────────────────────────────────────────────

type MarketService struct{}

func NewMarketService() *MarketService {
	return &MarketService{}
}

var defaultWatchlist = map[string]string{
	"RELIANCE":   "NSE_EQ|INE002A01018",
	"TCS":        "NSE_EQ|INE467B01029",
	"HDFCBANK":   "NSE_EQ|INE040A01034",
	"INFY":       "NSE_EQ|INE009A01021",
	"ITC":        "NSE_EQ|INE154A01025",
	"ICICIBANK":  "NSE_EQ|INE090A01021",
	"SBIN":       "NSE_EQ|INE062A01020",
	"KOTAKBANK":  "NSE_EQ|INE237A01036",
	"HINDUNILVR": "NSE_EQ|INE030A01027",
	"BHARTIARTL": "NSE_EQ|INE397D01024",
}

type upstoxHistResp struct {
	Status string `json:"status"`
	Data   struct {
		Candles [][]interface{} `json:"candles"`
	} `json:"data"`
}

func (m *MarketService) FetchMarketData() ([]map[string]interface{}, error) {
	client := &http.Client{Timeout: 10 * time.Second}
	ist, _ := time.LoadLocation("Asia/Kolkata")
	now := time.Now().In(ist)
	toDate := now.Format("2006-01-02")
	fromDate := now.AddDate(0, 0, -7).Format("2006-01-02")

	// Deterministic order
	symbolOrder := []string{"RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC", "ICICIBANK", "SBIN", "KOTAKBANK", "HINDUNILVR", "BHARTIARTL"}
	var results []map[string]interface{}

	for _, symbol := range symbolOrder {
		instKey := defaultWatchlist[symbol]
		encoded := url.PathEscape(instKey)
		apiURL := fmt.Sprintf("https://api.upstox.com/v2/historical-candle/%s/day/%s/%s", encoded, toDate, fromDate)

		req, err := http.NewRequest("GET", apiURL, nil)
		if err != nil {
			continue
		}
		req.Header.Set("Accept", "application/json")

		resp, err := client.Do(req)
		if err != nil {
			continue
		}

		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			continue
		}

		var histResp upstoxHistResp
		if err := json.Unmarshal(body, &histResp); err != nil || histResp.Status != "success" {
			continue
		}

		if len(histResp.Data.Candles) == 0 {
			continue
		}

		candle := histResp.Data.Candles[0]
		if len(candle) < 6 {
			continue
		}

		open, _ := candle[1].(float64)
		high, _ := candle[2].(float64)
		low, _ := candle[3].(float64)
		close_, _ := candle[4].(float64)
		vol, _ := candle[5].(float64)

		change := close_ - open
		changePct := 0.0
		if open != 0 {
			changePct = (change / open) * 100
		}

		results = append(results, map[string]interface{}{
			"symbol":    symbol,
			"ltp":       close_,
			"change":    change,
			"changePct": changePct,
			"volume":    int64(vol),
			"high":      high,
			"low":       low,
		})

		time.Sleep(100 * time.Millisecond)
	}

	if len(results) == 0 {
		return nil, fmt.Errorf("no market data available")
	}
	return results, nil
}

func (m *MarketService) GetMarketStatus() map[string]interface{} {
	ist, _ := time.LoadLocation("Asia/Kolkata")
	now := time.Now().In(ist)
	weekday := now.Weekday()

	status := "CLOSED"
	hour, min := now.Hour(), now.Minute()
	minuteOfDay := hour*60 + min
	marketOpen := 9*60 + 15   // 9:15 AM
	marketClose := 15*60 + 30 // 3:30 PM

	if weekday >= time.Monday && weekday <= time.Friday {
		if minuteOfDay >= marketOpen && minuteOfDay <= marketClose {
			status = "OPEN"
		}
	}

	return map[string]interface{}{
		"exchange":  "NSE",
		"status":    status,
		"timestamp": now.Format(time.RFC3339),
	}
}

// ─── Chat Service ──────────────────────────────────────────────

type ChatService struct {
	pythonCmd *exec.Cmd
}

func NewChatService() *ChatService {
	return &ChatService{}
}

func (c *ChatService) SendMessage(message string) (string, error) {
	client := &http.Client{Timeout: 120 * time.Second}

	payload, _ := json.Marshal(map[string]string{"message": message})
	resp, err := client.Post("http://127.0.0.1:8000/api/chat", "application/json", bytes.NewReader(payload))
	if err != nil {
		return "", fmt.Errorf("AI server not running. Start it from System Log or run 'python api.py' in research/src/")
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("failed to read AI response: %w", err)
	}

	if resp.StatusCode != 200 {
		return "", fmt.Errorf("AI error (status %d): %s", resp.StatusCode, string(body))
	}

	var result struct {
		Response string `json:"response"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("failed to parse AI response: %w", err)
	}

	return result.Response, nil
}

func (c *ChatService) ResetChat() (string, error) {
	client := &http.Client{Timeout: 10 * time.Second}

	resp, err := client.Post("http://127.0.0.1:8000/api/chat/reset", "application/json", nil)
	if err != nil {
		return "", fmt.Errorf("AI server not running")
	}
	defer resp.Body.Close()

	return "Chat history reset", nil
}

func (c *ChatService) StartPythonServer() error {
	// Find research/src directory
	exePath, _ := os.Executable()
	exeDir := filepath.Dir(exePath)

	searchPaths := []string{
		filepath.Join(exeDir, "..", "..", "research", "src"),
		filepath.Join(exeDir, "..", "research", "src"),
		filepath.Join(exeDir, "research", "src"),
	}

	var srcDir string
	for _, p := range searchPaths {
		if info, err := os.Stat(filepath.Join(p, "api.py")); err == nil && !info.IsDir() {
			srcDir = p
			break
		}
	}

	if srcDir == "" {
		return fmt.Errorf("could not find research/src/api.py")
	}

	// Find python executable - try venv first
	venvPython := filepath.Join(srcDir, "..", ".venv", "Scripts", "python.exe")
	pythonExe := "python"
	if runtime.GOOS == "windows" {
		pythonExe = "python.exe"
	}
	if _, err := os.Stat(venvPython); err == nil {
		pythonExe = venvPython
	}

	c.pythonCmd = exec.Command(pythonExe, "api.py")
	c.pythonCmd.Dir = srcDir
	c.pythonCmd.Stdout = os.Stdout
	c.pythonCmd.Stderr = os.Stderr

	if err := c.pythonCmd.Start(); err != nil {
		return fmt.Errorf("failed to start Python server: %w", err)
	}

	return nil
}

func (c *ChatService) StopPythonServer() {
	if c.pythonCmd != nil && c.pythonCmd.Process != nil {
		c.pythonCmd.Process.Kill()
		c.pythonCmd = nil
	}
}

func (c *ChatService) IsPythonServerRunning() bool {
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://127.0.0.1:8000/api/health")
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == 200
}

// ─── Trading Service ───────────────────────────────────────────

type TradingService struct{}

func NewTradingService() *TradingService {
	return &TradingService{}
}

func (t *TradingService) GetTradingSignals() ([]map[string]interface{}, error) {
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Get("http://127.0.0.1:8000/api/trading/signals")
	if err != nil {
		return nil, fmt.Errorf("trading engine offline: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var signals []map[string]interface{}
	if err := json.Unmarshal(body, &signals); err != nil {
		return nil, err
	}

	return signals, nil
}

func (t *TradingService) ExecuteOrder(symbol, direction string, qty int, entryPrice, stopLoss, targetPrice float64, mode string) (map[string]interface{}, error) {
	client := &http.Client{Timeout: 10 * time.Second}

	payload, _ := json.Marshal(map[string]interface{}{
		"symbol":       symbol,
		"direction":    direction,
		"qty":          qty,
		"entry_price":  entryPrice,
		"stop_loss":    stopLoss,
		"target_price": targetPrice,
		"mode":         mode,
	})

	resp, err := client.Post("http://127.0.0.1:8000/api/trading/order", "application/json", bytes.NewReader(payload))
	if err != nil {
		return nil, fmt.Errorf("failed to submit order: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var result map[string]interface{}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, err
	}

	return result, nil
}

func (t *TradingService) GetPositions() (map[string]interface{}, error) {
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get("http://127.0.0.1:8000/api/trading/positions")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var result map[string]interface{}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, err
	}

	return result, nil
}

func (t *TradingService) ClosePosition(positionID string, exitPrice float64) (map[string]interface{}, error) {
	client := &http.Client{Timeout: 10 * time.Second}

	payload, _ := json.Marshal(map[string]interface{}{
		"position_id": positionID,
		"exit_price":  exitPrice,
	})

	resp, err := client.Post("http://127.0.0.1:8000/api/trading/close_position", "application/json", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var result map[string]interface{}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, err
	}

	return result, nil
}


// ─── DRIP (dividend reinvestment) Service ──────────────────────

type DripService struct{}

func NewDripService() *DripService {
	return &DripService{}
}

// dripCall talks to the local Python API and turns its error responses (FastAPI "detail")
// into Go errors, so the UI shows the real reason, e.g. "live orders are switched off".
func dripCall(method, path string, payload interface{}, timeout time.Duration) (map[string]interface{}, error) {
	client := &http.Client{Timeout: timeout}
	url := "http://127.0.0.1:8000" + path

	var resp *http.Response
	var err error
	if method == http.MethodGet {
		resp, err = client.Get(url)
	} else {
		b, _ := json.Marshal(payload)
		resp, err = client.Post(url, "application/json", bytes.NewReader(b))
	}
	if err != nil {
		return nil, fmt.Errorf("DRIP engine offline: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	var result map[string]interface{}
	if jsonErr := json.Unmarshal(body, &result); jsonErr != nil {
		return nil, fmt.Errorf("unexpected response from DRIP engine (HTTP %d)", resp.StatusCode)
	}
	if resp.StatusCode >= 400 {
		if detail, ok := result["detail"].(string); ok {
			return nil, fmt.Errorf("%s", detail)
		}
		return nil, fmt.Errorf("DRIP engine error (HTTP %d)", resp.StatusCode)
	}
	return result, nil
}

func dripRunPayload(mode string, confirm bool, maxDeploy float64, trackingStart string) map[string]interface{} {
	p := map[string]interface{}{"mode": mode, "confirm": confirm, "max_deploy": maxDeploy}
	if trackingStart != "" {
		p["tracking_start"] = trackingStart
	}
	return p
}

func (d *DripService) GetDripStatus() (map[string]interface{}, error) {
	return dripCall(http.MethodGet, "/api/drip/status", nil, 15*time.Second)
}

// PlanDrip is a dry run: it ranks stocks and shows the orders Atlas would place. Nothing is written.
func (d *DripService) PlanDrip(mode string, maxDeploy float64, trackingStart string) (map[string]interface{}, error) {
	return dripCall(http.MethodPost, "/api/drip/plan", dripRunPayload(mode, false, maxDeploy, trackingStart), 240*time.Second)
}

// ExecuteDrip places orders. Live mode additionally needs confirm=true and ATLAS_LIVE_DRIP=1 on the engine.
func (d *DripService) ExecuteDrip(mode string, confirm bool, maxDeploy float64, trackingStart string) (map[string]interface{}, error) {
	return dripCall(http.MethodPost, "/api/drip/execute", dripRunPayload(mode, confirm, maxDeploy, trackingStart), 240*time.Second)
}

func (d *DripService) SetDripKillSwitch(enabled bool) (map[string]interface{}, error) {
	return dripCall(http.MethodPost, "/api/drip/kill", map[string]interface{}{"enabled": enabled}, 15*time.Second)
}

func (d *DripService) InitDripPortfolio(holdings map[string]int) (map[string]interface{}, error) {
	return dripCall(http.MethodPost, "/api/drip/portfolio", map[string]interface{}{"holdings": holdings}, 15*time.Second)
}

// ─── Investment Scanner Service (paper only) ───────────────────

type ScannerService struct{}

func NewScannerService() *ScannerService {
	return &ScannerService{}
}

// scannerCall reuses the local Python API plumbing and words engine errors for the scanner.
func scannerCall(method, path string, payload interface{}, timeout time.Duration) (map[string]interface{}, error) {
	res, err := dripCall(method, path, payload, timeout)
	if err != nil {
		return nil, fmt.Errorf("%s", strings.ReplaceAll(err.Error(), "DRIP engine", "Scanner engine"))
	}
	return res, nil
}

// GetScannerPicks returns ranked picks, weights, quality rejections and the backtest reference.
func (s *ScannerService) GetScannerPicks() (map[string]interface{}, error) {
	return scannerCall(http.MethodGet, "/api/scanner/picks", nil, 30*time.Second)
}

// GetScannerBacktest returns the stored point-in-time backtest and the variants that were tried.
func (s *ScannerService) GetScannerBacktest() (map[string]interface{}, error) {
	return scannerCall(http.MethodGet, "/api/scanner/backtest", nil, 60*time.Second)
}

// GetScannerStudy returns the same fixed model run on India-all and other markets.
func (s *ScannerService) GetScannerStudy() (map[string]interface{}, error) {
	return scannerCall(http.MethodGet, "/api/scanner/study", nil, 30*time.Second)
}

// GetScannerPortfolio returns the paper portfolio vs the 25%/yr line and Nifty.
func (s *ScannerService) GetScannerPortfolio() (map[string]interface{}, error) {
	return scannerCall(http.MethodGet, "/api/scanner/portfolio", nil, 30*time.Second)
}

// RebalanceScanner plans (execute=false) or applies (execute=true) a PAPER rebalance. No real orders.
func (s *ScannerService) RebalanceScanner(execute bool, capital float64, force bool) (map[string]interface{}, error) {
	return scannerCall(http.MethodPost, "/api/scanner/rebalance",
		map[string]interface{}{"execute": execute, "capital": capital, "force": force}, 60*time.Second)
}

// RefreshScanner starts a background price refresh (1-2 minutes) and returns immediately.
func (s *ScannerService) RefreshScanner() (map[string]interface{}, error) {
	return scannerCall(http.MethodPost, "/api/scanner/refresh", map[string]interface{}{}, 15*time.Second)
}
