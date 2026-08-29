package upstox

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"time"
)

const (
	HistoricalCandleURL = "https://api.upstox.com/v2/historical-candle"
)

// Candle represents a single OHLCV candle from Upstox.
type Candle struct {
	Timestamp    time.Time
	Open         float64
	High         float64
	Low          float64
	Close        float64
	Volume       int64
	OpenInterest float64
}

// HistoricalResponse represents the Upstox historical candle API response.
type HistoricalResponse struct {
	Status string `json:"status"`
	Data   struct {
		Candles [][]interface{} `json:"candles"`
	} `json:"data"`
}

// Client is the main Upstox API client.
type Client struct {
	AccessToken string
	HTTPClient  *http.Client
}

// NewClient creates a new Upstox API client with the given access token.
func NewClient(accessToken string) *Client {
	return &Client{
		AccessToken: accessToken,
		HTTPClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// GetHistoricalCandles fetches historical OHLCV data for a given instrument.
//
// Parameters:
//   - instrumentKey: e.g., "NSE_EQ|INE002A01018" for RELIANCE
//   - interval: "1minute", "30minute", "day", "week", "month"
//   - fromDate: start date (YYYY-MM-DD)
//   - toDate: end date (YYYY-MM-DD)
//
// Note: Upstox limits to ~7 days per request for intraday data.
// For daily data, larger ranges work.
func (c *Client) GetHistoricalCandles(instrumentKey, interval, fromDate, toDate string) ([]Candle, error) {
	// URL-encode the instrument key (contains | character)
	encodedKey := url.PathEscape(instrumentKey)

	apiURL := fmt.Sprintf("%s/%s/%s/%s/%s",
		HistoricalCandleURL,
		encodedKey,
		interval,
		toDate,
		fromDate,
	)

	slog.Debug("Fetching historical candles",
		"instrument", instrumentKey,
		"interval", interval,
		"from", fromDate,
		"to", toDate,
	)

	req, err := http.NewRequest("GET", apiURL, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.AccessToken)
	req.Header.Set("Accept", "application/json")

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("API returned status %d: %s", resp.StatusCode, string(body))
	}

	var histResp HistoricalResponse
	if err := json.Unmarshal(body, &histResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if histResp.Status != "success" {
		return nil, fmt.Errorf("API returned non-success status: %s", histResp.Status)
	}

	// Parse the candle arrays into typed structs
	candles := make([]Candle, 0, len(histResp.Data.Candles))
	for _, raw := range histResp.Data.Candles {
		if len(raw) < 6 {
			slog.Warn("Skipping malformed candle", "data", raw)
			continue
		}

		candle, err := parseCandle(raw)
		if err != nil {
			slog.Warn("Failed to parse candle", "error", err, "data", raw)
			continue
		}
		candles = append(candles, candle)
	}

	slog.Info("Fetched historical candles",
		"instrument", instrumentKey,
		"count", len(candles),
	)

	return candles, nil
}

// FetchDailyHistory fetches daily candles over a large date range by
// splitting into manageable chunks to respect API limits.
func (c *Client) FetchDailyHistory(instrumentKey string, fromDate, toDate time.Time) ([]Candle, error) {
	var allCandles []Candle

	// For daily data, fetch in 365-day chunks
	chunkDays := 365
	current := fromDate

	for current.Before(toDate) {
		chunkEnd := current.AddDate(0, 0, chunkDays)
		if chunkEnd.After(toDate) {
			chunkEnd = toDate
		}

		fromStr := current.Format("2006-01-02")
		toStr := chunkEnd.Format("2006-01-02")

		candles, err := c.GetHistoricalCandles(instrumentKey, "day", fromStr, toStr)
		if err != nil {
			return allCandles, fmt.Errorf("failed to fetch chunk %s to %s: %w", fromStr, toStr, err)
		}

		allCandles = append(allCandles, candles...)

		// Rate limiting: respect 50 req/sec but be conservative
		time.Sleep(100 * time.Millisecond)

		current = chunkEnd.AddDate(0, 0, 1)
	}

	slog.Info("Completed full history fetch",
		"instrument", instrumentKey,
		"total_candles", len(allCandles),
		"from", fromDate.Format("2006-01-02"),
		"to", toDate.Format("2006-01-02"),
	)

	return allCandles, nil
}

// parseCandle converts a raw JSON array into a Candle struct.
// Upstox format: [timestamp, open, high, low, close, volume, oi]
func parseCandle(raw []interface{}) (Candle, error) {
	var candle Candle

	// Parse timestamp
	tsStr, ok := raw[0].(string)
	if !ok {
		return candle, fmt.Errorf("invalid timestamp type: %T", raw[0])
	}
	ts, err := time.Parse(time.RFC3339, tsStr)
	if err != nil {
		// Try alternative format
		ts, err = time.Parse("2006-01-02T15:04:05-07:00", tsStr)
		if err != nil {
			return candle, fmt.Errorf("failed to parse timestamp '%s': %w", tsStr, err)
		}
	}
	candle.Timestamp = ts

	// Parse OHLCV (JSON numbers come as float64)
	open, ok := raw[1].(float64)
	if !ok {
		return candle, fmt.Errorf("invalid open type: %T", raw[1])
	}
	candle.Open = open

	high, ok := raw[2].(float64)
	if !ok {
		return candle, fmt.Errorf("invalid high type: %T", raw[2])
	}
	candle.High = high

	low, ok := raw[3].(float64)
	if !ok {
		return candle, fmt.Errorf("invalid low type: %T", raw[3])
	}
	candle.Low = low

	close_, ok := raw[4].(float64)
	if !ok {
		return candle, fmt.Errorf("invalid close type: %T", raw[4])
	}
	candle.Close = close_

	vol, ok := raw[5].(float64)
	if !ok {
		return candle, fmt.Errorf("invalid volume type: %T", raw[5])
	}
	candle.Volume = int64(vol)

	// Open interest (optional, index 6)
	if len(raw) > 6 {
		if oi, ok := raw[6].(float64); ok {
			candle.OpenInterest = oi
		}
	}

	return candle, nil
}
