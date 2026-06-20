package main

import (
	"fmt"
	"strings"
	"time"
)

// ── write ─────────────────────────────────────────────────────────────────────

// LogUsage inserts one usage record into usage_log.
func LogUsage(cli, provider, model, task string, tokensIn, tokensOut int) error {
	if db == nil {
		return fmt.Errorf("DB unavailable")
	}
	_, err := db.Exec(
		`INSERT INTO usage_log (cli,provider,model,task,tokens_in,tokens_out,logged_at)
		 VALUES (?,?,?,?,?,?,?)`,
		cli, provider, model, task, tokensIn, tokensOut,
		time.Now().UTC().Format(time.RFC3339),
	)
	return err
}

// ── read ──────────────────────────────────────────────────────────────────────

type DayUsage struct {
	Date      string // YYYY-MM-DD
	TokensIn  int
	TokensOut int
	Total     int
}

type CLIUsage struct {
	CLI       string
	TokensIn  int
	TokensOut int
	Total     int
}

type ProviderUsage struct {
	Provider  string
	TokensIn  int
	TokensOut int
	Total     int
}

func queryDailyUsage(days int) []DayUsage {
	if db == nil {
		return nil
	}
	rows, err := db.Query(`
		SELECT date(logged_at) AS day, SUM(tokens_in), SUM(tokens_out)
		FROM usage_log
		WHERE date(logged_at) >= date('now',?)
		GROUP BY day ORDER BY day
	`, fmt.Sprintf("-%d days", days))
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []DayUsage
	for rows.Next() {
		var d DayUsage
		_ = rows.Scan(&d.Date, &d.TokensIn, &d.TokensOut)
		d.Total = d.TokensIn + d.TokensOut
		out = append(out, d)
	}
	return out
}

func queryCLIUsage(days int) []CLIUsage {
	if db == nil {
		return nil
	}
	rows, err := db.Query(`
		SELECT cli, SUM(tokens_in), SUM(tokens_out)
		FROM usage_log
		WHERE date(logged_at) >= date('now',?)
		GROUP BY cli ORDER BY SUM(tokens_in+tokens_out) DESC
	`, fmt.Sprintf("-%d days", days))
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []CLIUsage
	for rows.Next() {
		var u CLIUsage
		_ = rows.Scan(&u.CLI, &u.TokensIn, &u.TokensOut)
		u.Total = u.TokensIn + u.TokensOut
		out = append(out, u)
	}
	return out
}

func queryProviderUsage(days int) []ProviderUsage {
	if db == nil {
		return nil
	}
	rows, err := db.Query(`
		SELECT provider, SUM(tokens_in), SUM(tokens_out)
		FROM usage_log
		WHERE date(logged_at) >= date('now',?)
		GROUP BY provider ORDER BY SUM(tokens_in+tokens_out) DESC
	`, fmt.Sprintf("-%d days", days))
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []ProviderUsage
	for rows.Next() {
		var p ProviderUsage
		_ = rows.Scan(&p.Provider, &p.TokensIn, &p.TokensOut)
		p.Total = p.TokensIn + p.TokensOut
		out = append(out, p)
	}
	return out
}

// ── interval (intraday) ───────────────────────────────────────────────────────

type IntervalUsage struct {
	Bucket    string // HH:MM
	TokensIn  int
	TokensOut int
	Total     int
}

func queryIntervalUsage(intervalMins, windowHours int) []IntervalUsage {
	if db == nil {
		return nil
	}
	window := fmt.Sprintf("-%d hours", windowHours)
	rows, err := db.Query(`
		SELECT
			strftime('%H:', logged_at) ||
			printf('%02d', (CAST(strftime('%M', logged_at) AS INTEGER) / ?) * ?) AS bucket,
			SUM(tokens_in), SUM(tokens_out)
		FROM usage_log
		WHERE logged_at >= datetime('now', ?)
		GROUP BY bucket ORDER BY bucket
	`, intervalMins, intervalMins, window)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []IntervalUsage
	for rows.Next() {
		var iv IntervalUsage
		_ = rows.Scan(&iv.Bucket, &iv.TokensIn, &iv.TokensOut)
		iv.Total = iv.TokensIn + iv.TokensOut
		out = append(out, iv)
	}
	return out
}

func FormatIntervalGraph(intervalMins, windowHours int) string {
	data := queryIntervalUsage(intervalMins, windowHours)

	var b strings.Builder

	if len(data) == 0 {
		fmt.Fprintf(&b, "No usage data in the last %d hours.\n", windowHours)
		return b.String()
	}

	grand := 0
	maxVal := 0
	for _, iv := range data {
		grand += iv.Total
		if iv.Total > maxVal {
			maxVal = iv.Total
		}
	}

	fmt.Fprintf(&b, "Token consumption — last %dh  (%d-min buckets)  total: %s\n",
		windowHours, intervalMins, fmtSI2(grand))

	// sparkline
	spark := []rune{'▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'}
	fmt.Fprintln(&b)
	fmt.Fprint(&b, "  ")
	for _, iv := range data {
		idx := 0
		if maxVal > 0 {
			idx = int(float64(iv.Total)/float64(maxVal)*float64(len(spark)-1) + 0.5)
		}
		fmt.Fprintf(&b, "%c", spark[idx])
	}
	if len(data) > 0 {
		fmt.Fprintf(&b, "  %s → %s\n", data[0].Bucket, data[len(data)-1].Bucket)
	}

	// horizontal bars
	fmt.Fprintln(&b)
	for _, iv := range data {
		blen := 0
		if maxVal > 0 {
			blen = int(float64(iv.Total) / float64(maxVal) * barWidth)
		}
		fmt.Fprintf(&b, "  %s  %s%s  %s\n",
			iv.Bucket,
			strings.Repeat("█", blen),
			strings.Repeat("░", barWidth-blen),
			fmtSI2(iv.Total),
		)
	}

	return b.String()
}

// ── graph ─────────────────────────────────────────────────────────────────────

const barWidth = 30

func FormatUsageGraph(days int) string {
	daily := queryDailyUsage(days)
	cliStats := queryCLIUsage(days)
	provStats := queryProviderUsage(days)

	var b strings.Builder

	if len(daily) == 0 && len(cliStats) == 0 {
		fmt.Fprintln(&b, "No usage data yet.")
		fmt.Fprintln(&b)
		fmt.Fprintln(&b, "Log usage:")
		fmt.Fprintln(&b, "  model-manager log <cli> <tokens-in> <tokens-out> [--task low]")
		fmt.Fprintln(&b, "  mm_log_usage  (MCP tool — other CLIs call this)")
		return b.String()
	}

	// total
	grand := 0
	for _, d := range daily {
		grand += d.Total
	}
	fmt.Fprintf(&b, "Token consumption — last %d days  (total: %s)\n", days, fmtSI2(grand))

	// ── sparkline (1 char per day) ─────────────────────────────────────────
	if len(daily) > 0 {
		fmt.Fprintln(&b)
		fmt.Fprintln(&b, "Daily trend:")
		maxDay := 0
		for _, d := range daily {
			if d.Total > maxDay {
				maxDay = d.Total
			}
		}
		spark := []rune{'▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'}
		fmt.Fprint(&b, "  ")
		for _, d := range daily {
			idx := 0
			if maxDay > 0 {
				idx = int(float64(d.Total)/float64(maxDay)*float64(len(spark)-1) + 0.5)
			}
			fmt.Fprintf(&b, "%c", spark[idx])
		}
		// date range
		if len(daily) > 0 {
			first := daily[0].Date
			last := daily[len(daily)-1].Date
			fmt.Fprintf(&b, "  %s → %s\n", first, last)
		}
	}

	// ── daily horizontal bars ─────────────────────────────────────────────
	if len(daily) > 0 {
		maxDay := 0
		for _, d := range daily {
			if d.Total > maxDay {
				maxDay = d.Total
			}
		}
		fmt.Fprintln(&b)
		fmt.Fprintln(&b, "Per day (in + out):")
		for _, d := range daily {
			blen := 0
			if maxDay > 0 {
				blen = int(float64(d.Total) / float64(maxDay) * barWidth)
			}
			date := d.Date
			if len(date) == 10 {
				date = date[5:] // MM-DD
			}
			fmt.Fprintf(&b, "  %s  %s%s  %s\n",
				date,
				strings.Repeat("█", blen),
				strings.Repeat("░", barWidth-blen),
				fmtSI2(d.Total),
			)
		}
	}

	// ── per CLI ───────────────────────────────────────────────────────────
	if len(cliStats) > 0 {
		maxCLI := cliStats[0].Total
		fmt.Fprintln(&b)
		fmt.Fprintln(&b, "Per CLI:")
		for _, c := range cliStats {
			pct := 0.0
			if grand > 0 {
				pct = float64(c.Total) / float64(grand) * 100
			}
			blen := 0
			if maxCLI > 0 {
				blen = int(float64(c.Total) / float64(maxCLI) * barWidth)
			}
			fmt.Fprintf(&b, "  %-13s  %s%s  %5.1f%%  in:%s  out:%s\n",
				c.CLI,
				strings.Repeat("█", blen),
				strings.Repeat("░", barWidth-blen),
				pct,
				fmtSI2(c.TokensIn),
				fmtSI2(c.TokensOut),
			)
		}
	}

	// ── per provider ──────────────────────────────────────────────────────
	if len(provStats) > 0 {
		maxProv := provStats[0].Total
		fmt.Fprintln(&b)
		fmt.Fprintln(&b, "Per provider:")
		for _, p := range provStats {
			pct := 0.0
			if grand > 0 {
				pct = float64(p.Total) / float64(grand) * 100
			}
			blen := 0
			if maxProv > 0 {
				blen = int(float64(p.Total) / float64(maxProv) * barWidth)
			}
			fmt.Fprintf(&b, "  %-22s  %s%s  %5.1f%%  %s\n",
				p.Provider,
				strings.Repeat("█", blen),
				strings.Repeat("░", barWidth-blen),
				pct,
				fmtSI2(p.Total),
			)
		}
	}

	return b.String()
}

func fmtSI2(n int) string {
	switch {
	case n >= 1_000_000:
		return fmt.Sprintf("%.2fM", float64(n)/1_000_000)
	case n >= 1_000:
		return fmt.Sprintf("%.1fK", float64(n)/1_000)
	default:
		return fmt.Sprintf("%d", n)
	}
}
