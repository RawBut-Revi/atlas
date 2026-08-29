package logger

import (
	"log/slog"
	"os"
)

// InitLogger sets up the global slog logger.
// It uses JSON formatting for production (structured logs) and text for development.
func InitLogger(env string) {
	var handler slog.Handler

	opts := &slog.HandlerOptions{
		AddSource: true, // Includes file and line number
	}

	if env == "production" {
		// JSON format for easier log aggregation (e.g. ELK/Datadog)
		opts.Level = slog.LevelInfo
		handler = slog.NewJSONHandler(os.Stdout, opts)
	} else {
		// Text format for easier human reading during dev
		opts.Level = slog.LevelDebug
		handler = slog.NewTextHandler(os.Stdout, opts)
	}

	logger := slog.New(handler)
	
	// Set it as the default logger for the whole application
	slog.SetDefault(logger)
	
	slog.Debug("Logger initialized successfully", "env", env)
}
