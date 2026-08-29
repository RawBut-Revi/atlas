package upstox

// InstrumentKey maps common NSE stock symbols to their Upstox instrument keys.
// Format: EXCHANGE|ISIN
//
// To find an instrument key for a new stock:
//   1. Look up the ISIN on NSE website (e.g., https://www.nseindia.com)
//   2. Format as NSE_EQ|{ISIN}
//   3. Or use the Upstox instrument search API: GET /v2/instruments?query={symbol}

// Common NIFTY50 instrument keys for NSE equities
var InstrumentKeys = map[string]string{
	// Banking & Financial
	"HDFCBANK":  "NSE_EQ|INE040A01034",
	"ICICIBANK": "NSE_EQ|INE090A01021",
	"KOTAKBANK": "NSE_EQ|INE237A01036",
	"SBIN":      "NSE_EQ|INE062A01020",
	"AXISBANK":  "NSE_EQ|INE238A01034",
	"BAJFINANCE":"NSE_EQ|INE296A01024",

	// IT
	"TCS":       "NSE_EQ|INE467B01029",
	"INFY":      "NSE_EQ|INE009A01021",
	"WIPRO":     "NSE_EQ|INE075A01022",
	"HCLTECH":   "NSE_EQ|INE860A01027",
	"TECHM":     "NSE_EQ|INE669C01036",

	// Energy & Industrial
	"RELIANCE":  "NSE_EQ|INE002A01018",
	"ONGC":      "NSE_EQ|INE213A01029",
	"NTPC":      "NSE_EQ|INE733E01010",
	"POWERGRID": "NSE_EQ|INE752E01010",
	"ADANIENT":  "NSE_EQ|INE423A01024",

	// Consumer & Pharma
	"HINDUNILVR":"NSE_EQ|INE030A01027",
	"ITC":       "NSE_EQ|INE154A01025",
	"SUNPHARMA": "NSE_EQ|INE044A01036",
	"DRREDDY":   "NSE_EQ|INE089A01023",
	"NESTLEIND": "NSE_EQ|INE239A01016",

	// Auto
	"TATAMOTORS":"NSE_EQ|INE155A01022",
	"MARUTI":    "NSE_EQ|INE585B01010",
	"M&M":       "NSE_EQ|INE101A01026",

	// Metals & Materials
	"TATASTEEL": "NSE_EQ|INE081A01020",
	"JSWSTEEL":  "NSE_EQ|INE019A01038",
	"HINDALCO":  "NSE_EQ|INE038A01020",

	// Others
	"LT":        "NSE_EQ|INE018A01030",
	"ASIANPAINT":"NSE_EQ|INE021A01026",
	"TITAN":     "NSE_EQ|INE280A01028",
	"ULTRACEMCO":"NSE_EQ|INE481G01011",
	"BHARTIARTL":"NSE_EQ|INE397D01024",

	// Indices
	"NIFTY50":    "NSE_INDEX|Nifty 50",
	"BANKNIFTY":  "NSE_INDEX|Nifty Bank",
}

// GetInstrumentKey returns the Upstox instrument key for a given symbol.
// Returns the key and a boolean indicating if it was found.
func GetInstrumentKey(symbol string) (string, bool) {
	key, ok := InstrumentKeys[symbol]
	return key, ok
}

// DefaultWatchlist returns a curated list of liquid, large-cap symbols
// that are good for initial research and testing.
func DefaultWatchlist() []string {
	return []string{
		"RELIANCE",
		"TCS",
		"INFY",
		"HDFCBANK",
		"ICICIBANK",
		"SBIN",
		"KOTAKBANK",
		"ITC",
		"HINDUNILVR",
		"BHARTIARTL",
	}
}
