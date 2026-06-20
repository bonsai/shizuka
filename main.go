package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func main() {
	// 1. Load JSON config (source of truth for priorities + model specs)
	if err := LoadModelsFromJSON(); err != nil {
		fmt.Fprintln(os.Stderr, "warning: no models.json, using built-in fallback:", err)
	}

	// 2. Open DB for runtime state (exhausted flags, usage logs, quota)
	if err := openDB(); err != nil {
		fmt.Fprintln(os.Stderr, "warning: DB unavailable:", err)
	} else {
		// Merge runtime exhausted state from DB into QwenRotation
		if loaded := dbLoadRotation(); len(loaded) > 0 {
			for i, r := range QwenRotation {
				for _, dr := range loaded {
					if dr.ModelID == r.ModelID {
						QwenRotation[i].Exhausted = dr.Exhausted
						break
					}
				}
			}
		}
	}

	args := os.Args[1:]

	switch {
	case len(args) == 0:
		if err := RunTUI(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}

	case args[0] == "--mcp":
		RunMCP()

	case args[0] == "list" || args[0] == "status":
		states := ReadAll()
		fmt.Print(FormatStatus(states))
		for _, a := range args[1:] {
			if a == "--meta" {
				fmt.Print(FormatMeta(states))
			}
		}

	case args[0] == "csv":
		csv := FormatCSV(ReadAll())
		fmt.Print(csv)
		out := filepath.Join(homeDir(), "Documents", "MEGA", "cli-models.csv")
		_ = os.WriteFile(out, []byte(csv), 0644)
		fmt.Fprintln(os.Stderr, "saved →", out)

	case args[0] == "priority":
		printPriorityTable()

	case args[0] == "rotate":
		if err := RotateQwen(); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		s := readQwen()
		fmt.Println("✓ qwen →", s.Model)

	case args[0] == "exhausted" && len(args) >= 3:
		if err := ExhaustQwen(args[2]); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		s := readQwen()
		fmt.Println("✓ exhausted", args[2], "→ qwen now:", s.Model)

	case args[0] == "quota":
		target := "claude"
		if len(args) > 1 {
			target = args[1]
		}
		switch target {
		case "history":
			fmt.Print(QuotaHistory())
		case "claude", "anthropic":
			info, err := CheckAnthropicQuota()
			if err != nil {
				fmt.Fprintln(os.Stderr, "error:", err)
				fmt.Fprintln(os.Stderr)
				fmt.Print(QuotaHistory())
				os.Exit(1)
			}
			fmt.Print(FormatQuota(info))
		default:
			fmt.Fprintf(os.Stderr, "quota: unsupported provider %q (supported: claude, history)\n", target)
			os.Exit(1)
		}

	case args[0] == "usage":
		days, intervalMins, windowHours := 14, 0, 3
		for i, a := range args[1:] {
			if i+1 >= len(args[1:]) {
				break
			}
			switch a {
			case "--days":
				fmt.Sscanf(args[i+2], "%d", &days)
			case "--interval":
				fmt.Sscanf(args[i+2], "%d", &intervalMins)
			case "--hours":
				fmt.Sscanf(args[i+2], "%d", &windowHours)
			}
		}
		if intervalMins > 0 {
			fmt.Print(FormatIntervalGraph(intervalMins, windowHours))
		} else {
			fmt.Print(FormatUsageGraph(days))
		}

	case args[0] == "log" && len(args) >= 4:
		// log <cli> <tokens-in> <tokens-out> [--task T] [--provider P] [--model M]
		cli := args[1]
		var tokIn, tokOut int
		fmt.Sscanf(args[2], "%d", &tokIn)
		fmt.Sscanf(args[3], "%d", &tokOut)
		task, provider, model := "mid", "", ""
		for i := 4; i < len(args); i++ {
			switch args[i] {
			case "--task":
				if i+1 < len(args) {
					task = args[i+1]; i++
				}
			case "--provider":
				if i+1 < len(args) {
					provider = args[i+1]; i++
				}
			case "--model":
				if i+1 < len(args) {
					model = args[i+1]; i++
				}
			}
		}
		if provider == "" || model == "" {
			states := ReadAll()
			for _, s := range states {
				if s.CLI == cli {
					if provider == "" { provider = s.Provider }
					if model == "" { model = s.Model }
					break
				}
			}
		}
		if err := LogUsage(cli, provider, model, task, tokIn, tokOut); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		fmt.Printf("✓ logged %s  in:%s  out:%s\n", cli, fmtSI2(tokIn), fmtSI2(tokOut))

	case args[0] == "db":
		handleDB(args[1:])

	case args[0] == "recommend":
		task := "mid"
		for i, a := range args[1:] {
			if a == "--task" && i+1 < len(args[1:]) {
				task = args[i+2]
			} else if !strings.HasPrefix(a, "--") {
				task = a
			}
		}
		recs := Recommend(task)
		fmt.Print(FormatRecommend(task, recs))

	case len(args) >= 2:
		handleCLICmd(args[0], args[1], args[2:])

	default:
		usage()
		os.Exit(1)
	}
}

func handleCLICmd(cli, sub string, rest []string) {
	// <cli> next
	if sub == "next" {
		states := ReadAll()
		var cur CLIState
		for _, s := range states {
			if s.CLI == cli {
				cur = s
				break
			}
		}
		if err := NextPriority(cli, cur); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		s := ReadAll()
		for _, st := range s {
			if st.CLI == cli {
				fmt.Printf("✓ %s → P%d %s/%s\n", cli, st.Priority, st.Provider, st.Model)
			}
		}
		return
	}

	// <cli> p<N> or <cli> P<N>
	if strings.HasPrefix(strings.ToLower(sub), "p") {
		nStr := sub[1:]
		n, err := strconv.Atoi(nStr)
		if err == nil && n > 0 {
			if err := ApplyPriority(cli, n); err != nil {
				fmt.Fprintln(os.Stderr, "error:", err)
				os.Exit(1)
			}
			fmt.Printf("✓ %s → P%d\n", cli, n)
			return
		}
	}

	// <cli> <provider> [model]
	provider := sub
	model := ""
	if len(rest) > 0 {
		model = rest[0]
	}
	// if model not given, use default from priority table
	if model == "" {
		pri := findPri(cli)
		if pri != nil {
			for _, e := range pri.Entries {
				if e.Provider == provider {
					model = e.resolvedModel()
					break
				}
			}
		}
	}
	if err := Apply(cli, provider, model); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	fmt.Printf("✓ %s → %s/%s\n", cli, provider, model)
}

func printPriorityTable() {
	fmt.Printf("%-14s  %s\n", "CLI", "Priority list")
	fmt.Println(strings.Repeat("─", 80))
	for _, pri := range Priorities {
		for i, e := range pri.Entries {
			model := e.resolvedModel()
			if e.UseRotation {
				model = "<rotation: " + model + ">"
			}
			note := ""
			if e.Note != "" {
				note = " (" + e.Note + ")"
			}
			if i == 0 {
				fmt.Printf("%-14s  P%d  %-20s  %s%s\n", pri.Name, i+1, e.Provider, model, note)
			} else {
				fmt.Printf("%-14s  P%d  %-20s  %s%s\n", "", i+1, e.Provider, model, note)
			}
		}
	}
}

func handleDB(args []string) {
	if len(args) == 0 || args[0] == "list" {
		printDBStatus()
		return
	}
	switch args[0] {
	case "add":
		// db add <cli> <provider> <model> [--priority N] [--cost X] [--note TEXT]
		if len(args) < 4 {
			fmt.Fprintln(os.Stderr, "usage: db add <cli> <provider> <model> [--priority N] [--cost X] [--note TEXT]")
			return
		}
		cli, provider, model := args[1], args[2], args[3]
		priority, costIn, note := 99, 0.0, ""
		for i := 4; i < len(args); i++ {
			switch args[i] {
			case "--priority":
				if i+1 < len(args) {
					fmt.Sscanf(args[i+1], "%d", &priority)
					i++
				}
			case "--cost":
				if i+1 < len(args) {
					fmt.Sscanf(args[i+1], "%f", &costIn)
					i++
				}
			case "--note":
				if i+1 < len(args) {
					note = args[i+1]
					i++
				}
			}
		}
		if err := dbAddProvider(cli, provider, model, priority, costIn, note); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		fmt.Printf("✓ added %s / %s / %s (P%d)\n", cli, provider, model, priority)

	case "remove", "rm":
		if len(args) < 4 {
			fmt.Fprintln(os.Stderr, "usage: db remove <cli> <provider> <model>")
			return
		}
		if err := dbRemoveProvider(args[1], args[2], args[3]); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		fmt.Printf("✓ removed %s / %s / %s\n", args[1], args[2], args[3])

	case "set-cost":
		if len(args) < 4 {
			fmt.Fprintln(os.Stderr, "usage: db set-cost <provider> <model> <usd_per_1m>")
			return
		}
		var cost float64
		fmt.Sscanf(args[3], "%f", &cost)
		if err := dbSetCost(args[1], args[2], cost); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		fmt.Printf("✓ %s/%s → $%.3f/1M\n", args[1], args[2], cost)

	case "rotate-add":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "usage: db rotate-add <model-id> [expires]")
			return
		}
		expires := ""
		if len(args) >= 3 {
			expires = args[2]
		}
		if err := dbAddRotation(args[1], expires); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		fmt.Printf("✓ added %s to qwen rotation\n", args[1])

	case "path":
		fmt.Println(dbPath())

	case "reset":
		fmt.Print("Reset DB to embedded defaults? (y/N) ")
		var ans string
		fmt.Scanln(&ans)
		if ans == "y" || ans == "Y" {
			if err := dbReset(); err != nil {
				fmt.Fprintln(os.Stderr, "error:", err)
				os.Exit(1)
			}
			fmt.Println("✓ DB reset to embedded seed")
		} else {
			fmt.Println("cancelled")
		}

	default:
		fmt.Fprintln(os.Stderr, "db subcommands: list | add | remove | set-cost | rotate-add | path | reset")
	}
}

func usage() {
	fmt.Println(`model-manager — unified CLI model switcher + MCP server

Usage:
  model-manager                         TUI (arrow key navigation)
  model-manager --mcp                   MCP server (stdio JSON-RPC)
  model-manager list                    show current state
  model-manager csv                     output CSV + save to cli-models.csv
  model-manager priority                show priority table
  model-manager rotate                  rotate qwen dashscope model
  model-manager exhausted <cli> <model>       mark model exhausted + rotate
  model-manager recommend [low|mid|high|code]  recommend best CLI+model for task

  model-manager db list                        show DB contents
  model-manager db add <cli> <prov> <model>    add provider entry [--priority N] [--cost X]
  model-manager db remove <cli> <prov> <model> remove provider entry
  model-manager db set-cost <prov> <model> <$> set USD/1M cost
  model-manager db rotate-add <model> [exp]    add qwen rotation model
  model-manager db path                        show DB file path

  model-manager quota [claude]                 check Anthropic rate-limit usage
  model-manager usage [--days N]               show token consumption graph (default 14d)
  model-manager log <cli> <in> <out>           record token usage [--task T] [--provider P] [--model M]

  model-manager <cli> next              advance to next priority
  model-manager <cli> p<N>              set to priority N
  model-manager <cli> <provider> [model] set provider (+ optional model)

CLIs: qwen  opencode  cline  codex  goose
      gemini  kilo  kiro  claude  qwencode`)
}
