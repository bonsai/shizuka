package main

import (
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

// RateLimitInfo holds parsed Anthropic rate-limit response headers.
type RateLimitInfo struct {
	Provider          string
	RequestsLimit     int
	RequestsRemaining int
	RequestsReset     time.Time
	TokensLimit       int
	TokensRemaining   int
	TokensReset       time.Time
	InputTokensLimit     int
	InputTokensRemaining int
	InputTokensReset     time.Time
	CheckedAt         time.Time
	Model             string
}

// CheckAnthropicQuota makes a minimal 1-token API call and reads rate-limit headers.
// Uses the cheapest model (haiku) to minimise cost.
func CheckAnthropicQuota() (*RateLimitInfo, error) {
	apiKey, src := resolveAnthropicKey()
	if apiKey == "" {
		return nil, fmt.Errorf(
			"ANTHROPIC_API_KEY not found. Set it with: setx ANTHROPIC_API_KEY sk-ant-...",
		)
	}
	_ = src // used for debug if needed

	model := "claude-haiku-4-5-20251001"
	reqBody := fmt.Sprintf(
		`{"model":%q,"max_tokens":1,"messages":[{"role":"user","content":"0"}]}`,
		model,
	)

	req, err := http.NewRequest("POST",
		"https://api.anthropic.com/v1/messages",
		strings.NewReader(reqBody),
	)
	if err != nil {
		return nil, err
	}
	req.Header.Set("x-api-key", apiKey)
	req.Header.Set("anthropic-version", "2023-06-01")
	req.Header.Set("content-type", "application/json")

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("API request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == 401 {
		return nil, fmt.Errorf(
			"401 Unauthorized\n" +
				"  Key: " + apiKey[:min(20, len(apiKey))] + "...\n" +
				"  The key may be expired or revoked.\n" +
				"  Get a new key at https://console.anthropic.com/settings/keys\n" +
				"  Then: setx ANTHROPIC_API_KEY sk-ant-...   (in cmd/powershell)",
		)
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("API error %s", resp.Status)
	}

	info := &RateLimitInfo{
		Provider:  "anthropic",
		Model:     model,
		CheckedAt: time.Now(),
	}

	h := resp.Header
	fmt.Sscanf(h.Get("anthropic-ratelimit-requests-limit"), "%d", &info.RequestsLimit)
	fmt.Sscanf(h.Get("anthropic-ratelimit-requests-remaining"), "%d", &info.RequestsRemaining)
	fmt.Sscanf(h.Get("anthropic-ratelimit-tokens-limit"), "%d", &info.TokensLimit)
	fmt.Sscanf(h.Get("anthropic-ratelimit-tokens-remaining"), "%d", &info.TokensRemaining)
	fmt.Sscanf(h.Get("anthropic-ratelimit-input-tokens-limit"), "%d", &info.InputTokensLimit)
	fmt.Sscanf(h.Get("anthropic-ratelimit-input-tokens-remaining"), "%d", &info.InputTokensRemaining)

	parse := func(s string) time.Time {
		t, _ := time.Parse(time.RFC3339, s)
		return t
	}
	info.RequestsReset = parse(h.Get("anthropic-ratelimit-requests-reset"))
	info.TokensReset = parse(h.Get("anthropic-ratelimit-tokens-reset"))
	info.InputTokensReset = parse(h.Get("anthropic-ratelimit-input-tokens-reset"))

	saveQuotaToDB(info)
	return info, nil
}

func saveQuotaToDB(info *RateLimitInfo) {
	if db == nil {
		return
	}
	pct := 100.0
	if info.TokensLimit > 0 {
		pct = float64(info.TokensRemaining) / float64(info.TokensLimit) * 100
	}
	_, _ = db.Exec(
		`INSERT OR REPLACE INTO quota (provider, model_id, remaining, checked_at) VALUES (?,?,?,?)`,
		info.Provider, "", pct, info.CheckedAt.Format(time.RFC3339),
	)
}

// resolveAnthropicKey tries env var only (crush.json fallback removed).
func resolveAnthropicKey() (key, source string) {
	if v := os.Getenv("ANTHROPIC_API_KEY"); v != "" {
		return v, "env:ANTHROPIC_API_KEY"
	}
	return "", ""
}

// FormatQuota renders the rate-limit info as a human-readable string.
func FormatQuota(info *RateLimitInfo) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Anthropic rate limits  (checked %s)\n\n",
		info.CheckedAt.Format("2006-01-02 15:04:05"))

	type row struct {
		label     string
		remaining int
		limit     int
		reset     time.Time
	}
	rows := []row{
		{"Requests ", info.RequestsRemaining, info.RequestsLimit, info.RequestsReset},
		{"Tokens   ", info.TokensRemaining, info.TokensLimit, info.TokensReset},
	}
	if info.InputTokensLimit > 0 {
		rows = append(rows, row{"Input tok", info.InputTokensRemaining, info.InputTokensLimit, info.InputTokensReset})
	}

	for _, r := range rows {
		if r.limit == 0 {
			continue
		}
		pct := float64(r.remaining) / float64(r.limit) * 100
		bar := quotaBar(pct, 28)

		resetIn := ""
		if !r.reset.IsZero() {
			d := time.Until(r.reset).Truncate(time.Second)
			if d < 0 {
				d = 0
			}
			resetIn = fmt.Sprintf("  reset in %s", d)
		}
		fmt.Fprintf(&b, "  %s  %8s / %-8s  %s  %5.1f%%%s\n",
			r.label,
			fmtInt(r.remaining), fmtInt(r.limit),
			bar, pct, resetIn,
		)
	}

	fmt.Fprintf(&b, "\n  Model used for check: %s\n", info.Model)
	return b.String()
}

func quotaBar(pct float64, width int) string {
	if pct < 0 {
		pct = 0
	}
	if pct > 100 {
		pct = 100
	}
	filled := int(pct / 100 * float64(width))
	color := "█"
	switch {
	case pct < 20:
		color = "▓" // visually different for low quota
	case pct < 50:
		color = "█"
	}
	return "[" + strings.Repeat(color, filled) + strings.Repeat("░", width-filled) + "]"
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// QuotaHistory reads the last saved quota from DB.
func QuotaHistory() string {
	if db == nil {
		return "DB unavailable\n"
	}
	rows, err := db.Query(
		`SELECT provider, model_id, remaining, checked_at FROM quota ORDER BY checked_at DESC LIMIT 10`,
	)
	if err != nil {
		return fmt.Sprintf("error: %v\n", err)
	}
	defer rows.Close()

	var b strings.Builder
	fmt.Fprintln(&b, "Cached quota (from DB):")
	fmt.Fprintf(&b, "%-12s  %-10s  %8s  %s\n", "Provider", "Model", "Remaining", "Checked at")
	fmt.Fprintln(&b, strings.Repeat("─", 60))
	found := false
	for rows.Next() {
		var prov, modelID, checkedAt string
		var remaining float64
		if err := rows.Scan(&prov, &modelID, &remaining, &checkedAt); err != nil {
			continue
		}
		if modelID == "" {
			modelID = "(all)"
		}
		fmt.Fprintf(&b, "%-12s  %-10s  %7.1f%%  %s\n", prov, modelID, remaining, checkedAt)
		found = true
	}
	if !found {
		fmt.Fprintln(&b, "  (no records yet — run: model-manager quota claude)")
	}
	return b.String()
}

func fmtInt(n int) string {
	s := fmt.Sprintf("%d", n)
	if len(s) <= 3 {
		return s
	}
	// insert commas
	out := make([]byte, 0, len(s)+len(s)/3)
	for i, c := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			out = append(out, ',')
		}
		out = append(out, byte(c))
	}
	return string(out)
}
