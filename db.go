package main

import (
	"database/sql"
	_ "embed"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

//go:embed seed.db
var seedDB []byte

var db *sql.DB

func dbPath() string {
	appData := os.Getenv("APPDATA")
	if appData == "" {
		appData = filepath.Join(homeDir(), "AppData", "Roaming")
	}
	dir := filepath.Join(appData, "model-manager")
	_ = os.MkdirAll(dir, 0755)
	return filepath.Join(dir, "data.db")
}

func openDB() error {
	p := dbPath()

	// Extract embedded seed.db on first run (file absent or empty)
	if fi, err := os.Stat(p); err != nil || fi.Size() == 0 {
		if err := os.WriteFile(p, seedDB, 0644); err != nil {
			return fmt.Errorf("extract seed.db: %w", err)
		}
	}

	var err error
	db, err = sql.Open("sqlite", p)
	if err != nil {
		return fmt.Errorf("openDB: %w", err)
	}
	// Always run migrate so new tables added in later versions are created
	return migrate()
}

// dbReset wipes the live DB and replaces it with the embedded seed.
func dbReset() error {
	if db != nil {
		_ = db.Close()
		db = nil
	}
	if err := os.WriteFile(dbPath(), seedDB, 0644); err != nil {
		return err
	}
	return openDB()
}

func migrate() error {
	ddl := `
CREATE TABLE IF NOT EXISTS providers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cli          TEXT NOT NULL,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    priority     INTEGER NOT NULL,
    note         TEXT DEFAULT '',
    use_rotation INTEGER DEFAULT 0,
    cost_in      REAL DEFAULT 0,
    quality_low  INTEGER DEFAULT 5,
    quality_mid  INTEGER DEFAULT 5,
    quality_high INTEGER DEFAULT 5,
    quality_code INTEGER DEFAULT 5,
    tags         TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS qwen_rotation (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   TEXT NOT NULL UNIQUE,
    expires    TEXT DEFAULT '',
    exhausted  INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quota (
    provider   TEXT NOT NULL,
    model_id   TEXT DEFAULT '',
    remaining  REAL DEFAULT 100.0,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (provider, model_id)
);

CREATE TABLE IF NOT EXISTS usage_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cli        TEXT NOT NULL,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    task       TEXT DEFAULT '',
    tokens_in  INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    logged_at  TEXT NOT NULL
);
`
	if _, err := db.Exec(ddl); err != nil {
		return fmt.Errorf("migrate: %w", err)
	}
	return seedIfEmpty()
}

// seedIfEmpty populates the DB from hardcoded defaults on first run.
func seedIfEmpty() error {
	var n int
	if err := db.QueryRow("SELECT COUNT(*) FROM providers").Scan(&n); err != nil {
		return err
	}
	if n > 0 {
		return nil // already seeded
	}
	return seed()
}

func seed() error {
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	// seed providers from hardcoded Priorities + ModelSpecs
	for _, pri := range Priorities {
		for i, e := range pri.Entries {
			useRot := 0
			if e.UseRotation {
				useRot = 1
			}
			spec := lookupSpec(e.Provider, e.resolvedModel())
			costIn := 0.0
			qLow, qMid, qHigh, qCode := 5, 5, 5, 5
			if spec != nil {
				costIn = spec.CostIn
				qLow = spec.QualityLow
				qMid = spec.QualityMid
				qHigh = spec.QualityHigh
				qCode = spec.QualityCode
			}
			_, err := tx.Exec(
				`INSERT INTO providers (cli,provider,model,priority,note,use_rotation,cost_in,quality_low,quality_mid,quality_high,quality_code)
				 VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
				pri.Name, e.Provider, e.Model, i+1, e.Note, useRot,
				costIn, qLow, qMid, qHigh, qCode,
			)
			if err != nil {
				return err
			}
		}
	}

	// seed qwen rotation
	for i, r := range QwenRotation {
		ex := 0
		if r.Exhausted {
			ex = 1
		}
		_, err := tx.Exec(
			`INSERT INTO qwen_rotation (model_id,expires,exhausted,sort_order) VALUES (?,?,?,?)`,
			r.ModelID, r.Expires, ex, i,
		)
		if err != nil {
			return err
		}
	}

	return tx.Commit()
}

// ── DB read helpers ───────────────────────────────────────────────────────────

func dbLoadPriorities() []CLIPri {
	rows, err := db.Query(`
		SELECT cli, provider, model, priority, note, use_rotation
		FROM providers
		ORDER BY cli, priority
	`)
	if err != nil {
		return nil
	}
	defer rows.Close()

	byName := map[string]*CLIPri{}
	var order []string

	for rows.Next() {
		var cli, prov, model, note string
		var pri, useRot int
		if err := rows.Scan(&cli, &prov, &model, &pri, &note, &useRot); err != nil {
			continue
		}
		if _, ok := byName[cli]; !ok {
			byName[cli] = &CLIPri{Name: cli}
			order = append(order, cli)
		}
		byName[cli].Entries = append(byName[cli].Entries, PEntry{
			Provider:    prov,
			Model:       model,
			Note:        note,
			UseRotation: useRot == 1,
		})
	}

	result := make([]CLIPri, 0, len(order))
	for _, name := range order {
		result = append(result, *byName[name])
	}
	return result
}

func dbLoadRotation() []RotEntry {
	rows, err := db.Query(`SELECT model_id, expires, exhausted FROM qwen_rotation ORDER BY sort_order`)
	if err != nil {
		return nil
	}
	defer rows.Close()

	var result []RotEntry
	for rows.Next() {
		var r RotEntry
		var ex int
		if err := rows.Scan(&r.ModelID, &r.Expires, &ex); err != nil {
			continue
		}
		r.Exhausted = ex == 1
		result = append(result, r)
	}
	return result
}

func dbLoadModelSpecs() []ModelSpec {
	rows, err := db.Query(`
		SELECT provider, model, cost_in, quality_low, quality_mid, quality_high, quality_code
		FROM providers
		WHERE cost_in IS NOT NULL
		GROUP BY provider, model
		ORDER BY provider, model
	`)
	if err != nil {
		return nil
	}
	defer rows.Close()

	seen := map[string]bool{}
	var result []ModelSpec
	for rows.Next() {
		var s ModelSpec
		if err := rows.Scan(&s.Provider, &s.Model, &s.CostIn, &s.QualityLow, &s.QualityMid, &s.QualityHigh, &s.QualityCode); err != nil {
			continue
		}
		key := s.Provider + "|" + s.Model
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, s)
	}
	return result
}

// ── DB write helpers ──────────────────────────────────────────────────────────

func dbMarkRotationExhausted(modelID string) error {
	_, err := db.Exec(`UPDATE qwen_rotation SET exhausted=1 WHERE model_id=?`, modelID)
	return err
}

func dbCurrentRotation() string {
	var modelID string
	err := db.QueryRow(`SELECT model_id FROM qwen_rotation WHERE exhausted=0 ORDER BY sort_order LIMIT 1`).Scan(&modelID)
	if err != nil {
		return "qwen3.5-27b"
	}
	return modelID
}

func dbNextRotation(current string) string {
	var modelID string
	err := db.QueryRow(`
		SELECT model_id FROM qwen_rotation
		WHERE exhausted=0 AND sort_order > (
			SELECT sort_order FROM qwen_rotation WHERE model_id=?
		)
		ORDER BY sort_order LIMIT 1
	`, current).Scan(&modelID)
	if err != nil {
		return dbCurrentRotation()
	}
	return modelID
}

// ── DB management commands ────────────────────────────────────────────────────

func dbAddProvider(cli, provider, model string, priority int, costIn float64, note string) error {
	_, err := db.Exec(
		`INSERT INTO providers (cli,provider,model,priority,cost_in,note) VALUES (?,?,?,?,?,?)`,
		cli, provider, model, priority, costIn, note,
	)
	return err
}

func dbRemoveProvider(cli, provider, model string) error {
	_, err := db.Exec(
		`DELETE FROM providers WHERE cli=? AND provider=? AND model=?`,
		cli, provider, model,
	)
	return err
}

func dbSetCost(provider, model string, costIn float64) error {
	_, err := db.Exec(
		`UPDATE providers SET cost_in=? WHERE provider=? AND model=?`,
		costIn, provider, model,
	)
	return err
}

func dbAddRotation(modelID, expires string) error {
	var maxOrder int
	_ = db.QueryRow(`SELECT COALESCE(MAX(sort_order),0) FROM qwen_rotation`).Scan(&maxOrder)
	_, err := db.Exec(
		`INSERT OR REPLACE INTO qwen_rotation (model_id,expires,exhausted,sort_order) VALUES (?,?,0,?)`,
		modelID, expires, maxOrder+1,
	)
	return err
}

func printDBStatus() {
	rows, err := db.Query(`
		SELECT cli, provider, model, priority, cost_in, note
		FROM providers ORDER BY cli, priority
	`)
	if err != nil {
		fmt.Println("error:", err)
		return
	}
	defer rows.Close()
	fmt.Printf("%-13s  %-20s  %-40s  %3s  %7s  %s\n", "CLI", "Provider", "Model", "Pri", "$/1M", "Note")
	fmt.Println(string(make([]byte, 95)))
	for rows.Next() {
		var cli, prov, model, note string
		var pri int
		var costIn float64
		_ = rows.Scan(&cli, &prov, &model, &pri, &costIn, &note)
		cost := "free"
		if costIn > 0 {
			cost = fmt.Sprintf("$%.3f", costIn)
		}
		if len(model) > 38 {
			model = model[:35] + "..."
		}
		fmt.Printf("%-13s  %-20s  %-40s  P%d  %7s  %s\n", cli, prov, model, pri, cost, note)
	}
}
