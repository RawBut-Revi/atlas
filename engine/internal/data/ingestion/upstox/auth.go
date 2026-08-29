package upstox

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"time"
)

const (
	AuthDialogURL = "https://api.upstox.com/v2/login/authorization/dialog"
	TokenURL      = "https://api.upstox.com/v2/login/authorization/token"
	TokenFile     = ".upstox_token"
)

// TokenResponse is the JSON returned by Upstox after exchanging the auth code.
type TokenResponse struct {
	AccessToken string `json:"access_token"`
	TokenType   string `json:"token_type"`
	ExpiresIn   int    `json:"expires_in"`
	Scope       string `json:"scope"`
}

// StoredToken wraps the token with metadata for persistence.
type StoredToken struct {
	AccessToken string    `json:"access_token"`
	ObtainedAt  time.Time `json:"obtained_at"`
}

// Auth handles the Upstox OAuth 2.0 authorization code flow.
type Auth struct {
	APIKey      string
	APISecret   string
	RedirectURL string
}

// NewAuth creates a new Auth instance.
func NewAuth(apiKey, apiSecret, redirectURL string) *Auth {
	return &Auth{
		APIKey:      apiKey,
		APISecret:   apiSecret,
		RedirectURL: redirectURL,
	}
}

// GetAccessToken returns a valid access token.
// It first checks for a cached token from today, then initiates the OAuth flow if needed.
// Upstox tokens expire at 3:30 AM the next day, so we consider a token valid
// if it was obtained today.
func (a *Auth) GetAccessToken() (string, error) {
	// Try to load cached token
	token, err := a.loadCachedToken()
	if err == nil && a.isTokenValid(token) {
		slog.Info("Using cached access token", "obtained_at", token.ObtainedAt.Format(time.RFC3339))
		return token.AccessToken, nil
	}

	slog.Info("No valid cached token found, initiating OAuth login flow...")

	// Start the OAuth flow
	accessToken, err := a.startOAuthFlow()
	if err != nil {
		return "", fmt.Errorf("oauth flow failed: %w", err)
	}

	// Cache the token
	if err := a.saveToken(accessToken); err != nil {
		slog.Warn("Failed to cache token", "error", err)
		// Non-fatal, we still have the token
	}

	return accessToken, nil
}

// isTokenValid checks if a cached token is still usable.
// Upstox tokens expire at 3:30 AM IST the next day.
func (a *Auth) isTokenValid(token *StoredToken) bool {
	ist, _ := time.LoadLocation("Asia/Kolkata")
	now := time.Now().In(ist)
	obtainedAt := token.ObtainedAt.In(ist)

	// Token obtained today is valid
	if obtainedAt.Year() == now.Year() && obtainedAt.YearDay() == now.YearDay() {
		return true
	}

	// Token obtained yesterday is valid if current time is before 3:30 AM
	yesterday := now.AddDate(0, 0, -1)
	if obtainedAt.Year() == yesterday.Year() && obtainedAt.YearDay() == yesterday.YearDay() {
		cutoff := time.Date(now.Year(), now.Month(), now.Day(), 3, 30, 0, 0, ist)
		return now.Before(cutoff)
	}

	return false
}

// startOAuthFlow runs the full OAuth authorization code flow:
// 1. Starts a local HTTP server to receive the callback
// 2. Opens the browser to the Upstox login page
// 3. Waits for the auth code via redirect
// 4. Exchanges the code for an access token
func (a *Auth) startOAuthFlow() (string, error) {
	// Parse redirect URL to extract host:port
	parsedURL, err := url.Parse(a.RedirectURL)
	if err != nil {
		return "", fmt.Errorf("invalid redirect URL: %w", err)
	}

	// Extract the path for the callback handler
	callbackPath := parsedURL.Path
	if callbackPath == "" {
		callbackPath = "/callback"
	}

	// Channel to receive the auth code
	codeChan := make(chan string, 1)
	errChan := make(chan error, 1)

	// Create a local HTTP server
	mux := http.NewServeMux()
	mux.HandleFunc(callbackPath, func(w http.ResponseWriter, r *http.Request) {
		code := r.URL.Query().Get("code")
		if code == "" {
			errMsg := r.URL.Query().Get("error")
			if errMsg == "" {
				errMsg = "no authorization code received"
			}
			fmt.Fprintf(w, "<html><body><h1>❌ Authorization Failed</h1><p>%s</p></body></html>", errMsg)
			errChan <- fmt.Errorf("authorization failed: %s", errMsg)
			return
		}

		fmt.Fprint(w, `<html><body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #1a1a2e; color: #e94560;">
			<div style="text-align: center;">
				<h1>✅ Project Atlas: Authorization Successful!</h1>
				<p style="color: #ccc;">You can close this browser tab and return to the terminal.</p>
			</div>
		</body></html>`)
		codeChan <- code
	})

	// Determine the listen address (use port from redirect URL)
	listenAddr := parsedURL.Host
	if !strings.Contains(listenAddr, ":") {
		listenAddr += ":443"
	}

	// For HTTPS redirect URLs on localhost, we listen on HTTP
	// (The redirect URL says https but for local dev we use http)
	_, port, _ := net.SplitHostPort(listenAddr)
	localAddr := "127.0.0.1:" + port

	server := &http.Server{
		Addr:    localAddr,
		Handler: mux,
	}

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		slog.Info("Local auth server started", "address", localAddr)
		if err := server.ListenAndServe(); err != http.ErrServerClosed {
			errChan <- fmt.Errorf("auth server error: %w", err)
		}
	}()

	// Give the server a moment to start
	time.Sleep(200 * time.Millisecond)

	// Build the authorization URL
	authURL := fmt.Sprintf("%s?response_type=code&client_id=%s&redirect_uri=%s",
		AuthDialogURL,
		url.QueryEscape(a.APIKey),
		url.QueryEscape(a.RedirectURL),
	)

	// Open the browser
	slog.Info("Opening browser for Upstox login...")
	fmt.Println("\n" + strings.Repeat("=", 60))
	fmt.Println("  Please login to Upstox in your browser.")
	fmt.Println("  If the browser didn't open, manually visit:")
	fmt.Printf("  %s\n", authURL)
	fmt.Println(strings.Repeat("=", 60) + "\n")

	openBrowser(authURL)

	// Wait for the auth code or error (timeout after 5 minutes)
	var code string
	select {
	case code = <-codeChan:
		slog.Info("Authorization code received!")
	case err := <-errChan:
		server.Shutdown(context.Background())
		return "", err
	case <-time.After(5 * time.Minute):
		server.Shutdown(context.Background())
		return "", fmt.Errorf("login timeout: no response received within 5 minutes")
	}

	// Shutdown the local server
	server.Shutdown(context.Background())
	wg.Wait()

	// Exchange the code for an access token
	return a.exchangeCodeForToken(code)
}

// exchangeCodeForToken calls the Upstox token endpoint to get an access token.
func (a *Auth) exchangeCodeForToken(code string) (string, error) {
	slog.Info("Exchanging authorization code for access token...")

	data := url.Values{
		"code":          {code},
		"client_id":     {a.APIKey},
		"client_secret": {a.APISecret},
		"redirect_uri":  {a.RedirectURL},
		"grant_type":    {"authorization_code"},
	}

	resp, err := http.Post(TokenURL, "application/x-www-form-urlencoded", strings.NewReader(data.Encode()))
	if err != nil {
		return "", fmt.Errorf("token request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("failed to read token response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("token request returned status %d: %s", resp.StatusCode, string(body))
	}

	var tokenResp TokenResponse
	if err := json.Unmarshal(body, &tokenResp); err != nil {
		return "", fmt.Errorf("failed to parse token response: %w", err)
	}

	slog.Info("Access token obtained successfully",
		"token_type", tokenResp.TokenType,
		"scope", tokenResp.Scope,
	)

	return tokenResp.AccessToken, nil
}

// saveToken persists the access token to disk for reuse.
func (a *Auth) saveToken(accessToken string) error {
	stored := StoredToken{
		AccessToken: accessToken,
		ObtainedAt:  time.Now(),
	}

	data, err := json.MarshalIndent(stored, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(TokenFile, data, 0600)
}

// loadCachedToken reads a previously saved token from disk.
func (a *Auth) loadCachedToken() (*StoredToken, error) {
	data, err := os.ReadFile(TokenFile)
	if err != nil {
		return nil, err
	}

	var token StoredToken
	if err := json.Unmarshal(data, &token); err != nil {
		return nil, err
	}

	return &token, nil
}

// openBrowser opens the given URL in the user's default browser.
func openBrowser(rawURL string) {
	var cmd *exec.Cmd

	switch runtime.GOOS {
	case "windows":
		// Use 'cmd /c start "" "url"' — the empty title string is required
		// because 'start' treats the first quoted argument as a window title.
		cmd = exec.Command("cmd", "/c", "start", "", rawURL)
	case "darwin":
		cmd = exec.Command("open", rawURL)
	default:
		cmd = exec.Command("xdg-open", rawURL)
	}

	if err := cmd.Start(); err != nil {
		slog.Warn("Failed to open browser automatically", "error", err)
	}
}
