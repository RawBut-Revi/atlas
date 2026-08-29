package main

import (
	"context"
	"embed"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
)

//go:embed all:frontend/dist
var assets embed.FS

func main() {
	app := NewApp()
	authService := NewAuthService()
	marketService := NewMarketService()
	chatService := NewChatService()
	tradingService := NewTradingService()

	err := wails.Run(&options.App{
		Title:       "Project Atlas Terminal",
		Width:       1920,
		Height:      1080,
		MinWidth:    1280,
		MinHeight:   720,
		Frameless:   false,
		StartHidden: false,
		AssetServer: &assetserver.Options{
			Assets: assets,
		},
		BackgroundColour: &options.RGBA{R: 13, G: 17, B: 23, A: 1},
		OnStartup:        app.startup,
		OnShutdown: func(ctx context.Context) {
			chatService.StopPythonServer()
		},
		Bind: []interface{}{
			app,
			authService,
			marketService,
			chatService,
			tradingService,
		},
	})

	if err != nil {
		println("Error:", err.Error())
	}
}

