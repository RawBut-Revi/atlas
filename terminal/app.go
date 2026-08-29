package main

import (
	"context"
	"runtime"
)

// App struct
type App struct {
	ctx context.Context
}

// NewApp creates a new App application struct
func NewApp() *App {
	return &App{}
}

// startup is called when the app starts. The context is saved
// so we can call the runtime methods
func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
}

func (a *App) GetSystemStatus() map[string]interface{} {
	return map[string]interface{}{
		"appName": "Project Atlas Terminal",
		"version": "0.1.0",
		"platform": runtime.GOOS,
	}
}
