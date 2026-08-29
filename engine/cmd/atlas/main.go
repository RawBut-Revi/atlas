package main

import (
	"fmt"
	"log/slog"
	"os"
	"strings"

	"atlas-engine/internal/core/config"
	"atlas-engine/internal/core/logger"
	"atlas-engine/internal/data/ingestion/upstox"
)

func main() {
	// 1. Load Configuration
	cfg, err := config.LoadConfig()
	if err != nil {
		println("CRITICAL ERROR: Failed to load configuration:", err.Error())
		os.Exit(1)
	}

	// 2. Initialize Centralized Logger
	logger.InitLogger(cfg.Environment)

	slog.Info("Project Atlas: Go Execution Engine Initialized")
	slog.Info("Configuration loaded",
		"broker", cfg.Broker.Provider,
		"database_path", cfg.Database.Path,
	)

	// 3. Check command line arguments
	if len(os.Args) < 2 {
		printUsage()
		return
	}

	command := os.Args[1]

	switch command {
	case "auth":
		runAuth(cfg)
	case "fetch":
		runFetch(cfg)
	case "status":
		runStatus(cfg)
	default:
		fmt.Printf("Unknown command: %s\n", command)
		printUsage()
	}
}

func printUsage() {
	fmt.Println(`
Project Atlas - Go Execution Engine
====================================

Commands:
  auth      Authenticate with Upstox (opens browser for OAuth login)
  fetch     Fetch historical data for default watchlist
  status    Check authentication status and configuration
	`)
}

func runAuth(cfg *config.Config) {
	slog.Info("Starting Upstox authentication...")

	auth := upstox.NewAuth(cfg.Broker.APIKey, cfg.Broker.Secret, cfg.Broker.RedirectURL)
	token, err := auth.GetAccessToken()
	if err != nil {
		slog.Error("Authentication failed", "error", err)
		os.Exit(1)
	}

	// Mask the token for display
	masked := token[:8] + "..." + token[len(token)-4:]
	slog.Info("Authentication successful!", "token_preview", masked)
}

func runFetch(cfg *config.Config) {
	slog.Info("Starting historical data fetch...")

	// First, ensure we have a valid token
	auth := upstox.NewAuth(cfg.Broker.APIKey, cfg.Broker.Secret, cfg.Broker.RedirectURL)
	token, err := auth.GetAccessToken()
	if err != nil {
		slog.Error("Authentication required before fetching data", "error", err)
		fmt.Println("\nRun 'atlas auth' first to authenticate.")
		os.Exit(1)
	}

	client := upstox.NewClient(token)
	watchlist := upstox.DefaultWatchlist()

	fmt.Printf("\nFetching daily candles for %d symbols...\n", len(watchlist))
	fmt.Println(strings.Repeat("-", 50))

	for _, symbol := range watchlist {
		instrumentKey, ok := upstox.GetInstrumentKey(symbol)
		if !ok {
			slog.Warn("No instrument key found for symbol", "symbol", symbol)
			continue
		}

		candles, err := client.GetHistoricalCandles(
			instrumentKey, "day",
			"2025-01-01", "2025-07-31",
		)
		if err != nil {
			slog.Error("Failed to fetch candles", "symbol", symbol, "error", err)
			continue
		}

		fmt.Printf("  %-12s: %d candles fetched\n", symbol, len(candles))

		if len(candles) > 0 {
			latest := candles[0]
			slog.Debug("Latest candle",
				"symbol", symbol,
				"date", latest.Timestamp.Format("2006-01-02"),
				"close", latest.Close,
				"volume", latest.Volume,
			)
		}
	}

	fmt.Println(strings.Repeat("-", 50))
	fmt.Println("Fetch complete!")
}

func runStatus(cfg *config.Config) {
	fmt.Println("\nProject Atlas: System Status")
	fmt.Println(strings.Repeat("=", 40))
	fmt.Printf("  Environment:    %s\n", cfg.Environment)
	fmt.Printf("  Broker:         %s\n", cfg.Broker.Provider)
	if len(cfg.Broker.APIKey) > 8 {
		fmt.Printf("  API Key:        %s...%s\n", cfg.Broker.APIKey[:4], cfg.Broker.APIKey[len(cfg.Broker.APIKey)-4:])
	}
	fmt.Printf("  Redirect URL:   %s\n", cfg.Broker.RedirectURL)
	fmt.Printf("  Database Path:  %s\n", cfg.Database.Path)

	// Only check for cached token — don't trigger OAuth flow
	if _, err := os.Stat(upstox.TokenFile); err == nil {
		fmt.Printf("  Auth Status:    ✅ Token file found (%s)\n", upstox.TokenFile)
		fmt.Println("                  (Run 'atlas auth' to re-authenticate if expired)")
	} else {
		fmt.Printf("  Auth Status:    ❌ Not authenticated\n")
		fmt.Println("                  (Run 'atlas auth' to login)")
	}

	fmt.Println()
}
