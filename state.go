package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// CLIState is the current live state of one CLI slot
type CLIState struct {
	CLI      string
	Provider string
	Model    string
	Priority int // 1-based; 0 = unknown
}

func homeDir() string {
	h, _ := os.UserHomeDir()
	return h
}

func localAppData() string {
	if v := os.Getenv("LOCALAPPDATA"); v != "" {
		return v
	}
	return filepath.Join(homeDir(), "AppData", "Local")
}

// ReadAll returns the current state for every CLI
func ReadAll() []CLIState {
	readers := []func() CLIState{
		readQwen,
		readOpencode,
		readCline,
		readCodex,
		readGemini,
		readKilo,
		readKiro,
		readClaude,
		readQwencode,
		readGoose,
		readCrushLarge,
		readCrushSmall,
	}
	out := make([]CLIState, 0, len(readers))
	for _, r := range readers {
		out = append(out, r())
	}
	return out
}

// ── Qwen ────────────────────────────────────────────────────────────────────

type qwenSettings struct {
	Model struct {
		Name string `json:"name"`
	} `json:"model"`
	ModelProviders struct {
		OpenAI []struct {
			ID      string `json:"id"`
			BaseURL string `json:"baseUrl"`
		} `json:"openai"`
	} `json:"modelProviders"`
}

func readQwen() CLIState {
	s := CLIState{CLI: "qwen"}
	path := filepath.Join(homeDir(), ".qwen", "settings.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return s
	}
	var cfg qwenSettings
	if err := json.Unmarshal(data, &cfg); err != nil {
		return s
	}
	s.Model = cfg.Model.Name
	// determine provider from baseUrl
	for _, p := range cfg.ModelProviders.OpenAI {
		if p.ID == s.Model {
			if strings.Contains(p.BaseURL, "sakura") {
				s.Provider = "sakura"
			} else {
				s.Provider = "dashscope"
			}
			break
		}
	}
	if s.Provider == "" && s.Model != "" {
		s.Provider = "dashscope"
	}
	s.Priority = inferPriority("qwen", s.Provider, s.Model)
	return s
}

// ── OpenCode ─────────────────────────────────────────────────────────────────

func readOpencode() CLIState {
	s := CLIState{CLI: "opencode"}
	path := filepath.Join(homeDir(), ".config", "opencode", "opencode.jsonc")
	data, err := os.ReadFile(path)
	if err != nil {
		return s
	}
	// strip // comments for JSONC
	cleaned := stripComments(string(data))
	var cfg map[string]interface{}
	if err := json.Unmarshal([]byte(cleaned), &cfg); err != nil {
		return s
	}
	if m, ok := cfg["model"].(string); ok {
		// model field may be "provider/model-id" or just a bare model id.
		// Model ids themselves can contain "/" (e.g. "preview/Kimi-K2.6"),
		// so only treat the prefix as a provider if it's a configured one.
		if i := strings.Index(m, "/"); i > 0 {
			prefix := m[:i]
			if provMap, ok := cfg["provider"].(map[string]interface{}); ok {
				if _, exists := provMap[prefix]; exists {
					s.Provider = prefix
					s.Model = m[i+1:]
				}
			}
		}
		if s.Model == "" {
			s.Model = m
		}
	}
	if s.Provider == "" {
		// bare model id: match against the priority table first
		if pri := findPri("opencode"); pri != nil {
			for _, e := range pri.Entries {
				if e.Model == s.Model {
					s.Provider = e.Provider
					break
				}
			}
		}
	}
	if s.Provider == "" {
		// fall back to the first configured provider (sorted for determinism)
		if provMap, ok := cfg["provider"].(map[string]interface{}); ok {
			keys := make([]string, 0, len(provMap))
			for k := range provMap {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			if len(keys) > 0 {
				s.Provider = keys[0]
			}
		}
	}
	s.Priority = inferPriority("opencode", s.Provider, s.Model)
	return s
}

// stripComments removes // line comments from JSONC, but never inside string
// literals (a naive regexp would mangle "https://..." values).
func stripComments(s string) string {
	var b strings.Builder
	inStr := false
	escaped := false
	for i := 0; i < len(s); i++ {
		c := s[i]
		if inStr {
			b.WriteByte(c)
			if escaped {
				escaped = false
			} else if c == '\\' {
				escaped = true
			} else if c == '"' {
				inStr = false
			}
			continue
		}
		if c == '"' {
			inStr = true
			b.WriteByte(c)
			continue
		}
		if c == '/' && i+1 < len(s) && s[i+1] == '/' {
			for i < len(s) && s[i] != '\n' {
				i++
			}
			if i < len(s) {
				b.WriteByte('\n')
			}
			continue
		}
		b.WriteByte(c)
	}
	return b.String()
}

// ── Cline ────────────────────────────────────────────────────────────────────

func readCline() CLIState {
	s := CLIState{CLI: "cline"}
	// Cline stores auth in ~/.cline/ - try to read provider info
	dir := filepath.Join(homeDir(), ".cline")
	// Check for a config or auth file
	for _, name := range []string{"config.json", "auth.json", "settings.json"} {
		data, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			continue
		}
		var cfg map[string]interface{}
		if err := json.Unmarshal(data, &cfg); err != nil {
			continue
		}
		if p, ok := cfg["provider"].(string); ok {
			s.Provider = p
		}
		if m, ok := cfg["model"].(string); ok {
			s.Model = m
		}
		break
	}
	// fallback: we set it via `cline auth --provider openai-native`
	if s.Provider == "" {
		s.Provider = "openai-native"
		s.Model = "Qwen3-Coder-480B-A35B-Instruct-FP8"
	}
	s.Priority = inferPriority("cline", s.Provider, s.Model)
	return s
}

// ── Codex-local ──────────────────────────────────────────────────────────────

func readCodex() CLIState {
	s := CLIState{CLI: "codex"}
	// Project-local env file
	path := filepath.Join(".", ".codex-local", "sakura-ai.env")
	if _, err := os.Stat(path); err != nil {
		// try MEGA root
		path = filepath.Join(homeDir(), "Documents", "MEGA", ".codex-local", "sakura-ai.env")
	}
	f, err := os.Open(path)
	if err != nil {
		return s
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		key, val := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		switch key {
		case "OPENAI_MODEL":
			s.Model = val
		case "OPENAI_BASE_URL":
			if strings.Contains(val, "sakura") {
				s.Provider = "sakura"
			}
		}
	}
	s.Priority = inferPriority("codex", s.Provider, s.Model)
	return s
}

// ── Gemini ───────────────────────────────────────────────────────────────────

func readGemini() CLIState {
	s := CLIState{CLI: "gemini", Provider: "google-oauth"}
	path := filepath.Join(homeDir(), ".gemini", "settings.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return s
	}
	var cfg map[string]interface{}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return s
	}
	if m, ok := cfg["model"].(string); ok {
		s.Model = m
	}
	if s.Model == "" {
		s.Model = "gemini-2.5-pro-preview-05-06"
	}
	s.Priority = inferPriority("gemini", s.Provider, s.Model)
	return s
}

// ── Kilo ─────────────────────────────────────────────────────────────────────

func readKilo() CLIState {
	s := CLIState{CLI: "kilo"}
	// kilo is npm-based; check common config locations
	for _, path := range []string{
		filepath.Join(homeDir(), ".kilo", "config.json"),
		filepath.Join(homeDir(), ".kilo", "settings.json"),
		filepath.Join(localAppData(), "kilo", "settings.json"),
	} {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var cfg map[string]interface{}
		if err := json.Unmarshal(data, &cfg); err != nil {
			continue
		}
		if p, ok := cfg["provider"].(string); ok {
			s.Provider = p
		}
		if m, ok := cfg["model"].(string); ok {
			s.Model = m
		}
		break
	}
	s.Priority = inferPriority("kilo", s.Provider, s.Model)
	return s
}

// ── Kiro ─────────────────────────────────────────────────────────────────────

func readKiro() CLIState {
	s := CLIState{CLI: "kiro"}
	// Kiro config is project-local .kiro/settings.json
	for _, base := range []string{".", filepath.Join(homeDir(), "Documents", "MEGA")} {
		path := filepath.Join(base, ".kiro", "settings.json")
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var cfg map[string]interface{}
		if err := json.Unmarshal(data, &cfg); err != nil {
			continue
		}
		if p, ok := cfg["provider"].(string); ok {
			s.Provider = p
		}
		if m, ok := cfg["model"].(string); ok {
			s.Model = m
		}
		if s.Provider == "" {
			s.Provider = "amazon-bedrock"
		}
		break
	}
	if s.Provider == "" {
		s.Provider = "amazon-bedrock"
	}
	s.Priority = inferPriority("kiro", s.Provider, s.Model)
	return s
}

// ── Claude Code ───────────────────────────────────────────────────────────────

func readClaude() CLIState {
	s := CLIState{CLI: "claude", Provider: "anthropic", Model: "(default)"}
	path := filepath.Join(homeDir(), ".claude", "settings.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return s
	}
	var cfg map[string]interface{}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return s
	}
	// env-based override (e.g. Sakura AI Engine via ANTHROPIC_BASE_URL)
	if env, ok := cfg["env"].(map[string]interface{}); ok {
		if base, ok := env["ANTHROPIC_BASE_URL"].(string); ok && strings.Contains(base, "sakura") {
			s.Provider = "sakura"
			if m, ok := env["ANTHROPIC_MODEL"].(string); ok {
				s.Model = m
			}
			s.Priority = inferPriority("claude", s.Provider, s.Model)
			return s
		}
	}
	if m, ok := cfg["model"].(string); ok && m != "" {
		s.Model = m
	}
	s.Priority = inferPriority("claude", s.Provider, s.Model)
	if s.Priority == 0 && s.Model == "(default)" {
		s.Priority = 1 // no override = stock Anthropic default
	}
	return s
}

// ── QwenCode ──────────────────────────────────────────────────────────────────

func readQwencode() CLIState {
	s := CLIState{CLI: "qwencode"}
	path := filepath.Join(homeDir(), ".qwencode", "settings.json")
	data, err := os.ReadFile(path)
	if err != nil {
		// default to first priority
		if pri := findPri("qwencode"); pri != nil && len(pri.Entries) > 0 {
			s.Provider = pri.Entries[0].Provider
			s.Model = pri.Entries[0].resolvedModel()
		}
		return s
	}
	var cfg struct {
		Provider string `json:"provider"`
		Model    string `json:"model"`
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return s
	}
	s.Provider = cfg.Provider
	s.Model = cfg.Model
	s.Priority = inferPriority("qwencode", s.Provider, s.Model)
	return s
}

// ── Goose ─────────────────────────────────────────────────────────────────────

func readGoose() CLIState {
	s := CLIState{CLI: "goose"}
	path := filepath.Join(os.Getenv("APPDATA"), "Block", "goose", "config", "config.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		if pri := findPri("goose"); pri != nil && len(pri.Entries) > 0 {
			s.Provider = pri.Entries[0].Provider
			s.Model = pri.Entries[0].resolvedModel()
		}
		return s
	}
	lines := strings.Split(string(data), "\n")
	var activeProvider string
	for i := 0; i < len(lines); i++ {
		line := strings.TrimSpace(lines[i])
		switch {
		case strings.HasPrefix(line, "active_provider:"):
			activeProvider = strings.TrimSpace(strings.TrimPrefix(line, "active_provider:"))
		case line == "active_provider":
			// multiline format not expected
		}
	}
	if activeProvider != "" {
		s.Provider = activeProvider
		// find model inside providers.<activeProvider>.model
		inProvider := false
		for _, line := range lines {
			trimmed := strings.TrimSpace(line)
			indent := len(line) - len(strings.TrimLeft(line, " "))
			if strings.HasPrefix(trimmed, "providers:") {
				continue
			}
			if indent == 2 && strings.HasPrefix(trimmed, activeProvider+":") {
				inProvider = true
				continue
			}
			if indent == 2 && !strings.HasPrefix(trimmed, activeProvider+":") && inProvider {
				inProvider = false
			}
			if inProvider && indent == 4 && strings.HasPrefix(trimmed, "model:") {
				s.Model = strings.TrimSpace(strings.TrimPrefix(trimmed, "model:"))
			}
		}
	}
	s.Priority = inferPriority("goose", s.Provider, s.Model)
	return s
}

// ── Crush ─────────────────────────────────────────────────────────────────────

type crushSettings struct {
	Large map[string]string `json:"large"`
	Small map[string]string `json:"small"`
}

func readCrushLarge() CLIState {
	s := CLIState{CLI: "crush-large"}
	path := filepath.Join(localAppData(), "crush", "crush.json")
	data, err := os.ReadFile(path)
	if err != nil {
		// default to first priority
		if pri := findPri("crush-large"); pri != nil && len(pri.Entries) > 0 {
			s.Provider = pri.Entries[0].Provider
			s.Model = pri.Entries[0].resolvedModel()
		}
		return s
	}
	var cfg crushSettings
	if err := json.Unmarshal(data, &cfg); err != nil {
		return s
	}
	if cfg.Large != nil {
		s.Provider = cfg.Large["provider"]
		s.Model = cfg.Large["model"]
	}
	s.Priority = inferPriority("crush-large", s.Provider, s.Model)
	return s
}

func readCrushSmall() CLIState {
	s := CLIState{CLI: "crush-small"}
	path := filepath.Join(localAppData(), "crush", "crush.json")
	data, err := os.ReadFile(path)
	if err != nil {
		// default to first priority
		if pri := findPri("crush-small"); pri != nil && len(pri.Entries) > 0 {
			s.Provider = pri.Entries[0].Provider
			s.Model = pri.Entries[0].resolvedModel()
		}
		return s
	}
	var cfg crushSettings
	if err := json.Unmarshal(data, &cfg); err != nil {
		return s
	}
	if cfg.Small != nil {
		s.Provider = cfg.Small["provider"]
		s.Model = cfg.Small["model"]
	}
	s.Priority = inferPriority("crush-small", s.Provider, s.Model)
	return s
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// inferPriority returns 1-based priority index, or 0 if not found
func inferPriority(cli, provider, model string) int {
	pri := findPri(cli)
	if pri == nil {
		return 0
	}
	for i, e := range pri.Entries {
		if e.Provider != provider {
			continue
		}
		if e.UseRotation || e.Model == model || model == "" {
			return i + 1
		}
	}
	return 0
}

// FormatStatus returns a human-readable status table
func FormatStatus(states []CLIState) string {
	var sb strings.Builder
	fmt.Fprintf(&sb, "%-14s  %-20s  %-45s  %s\n", "CLI", "PROVIDER", "MODEL", "PRI")
	fmt.Fprintf(&sb, "%s  %s  %s  %s\n", strings.Repeat("─", 14), strings.Repeat("─", 20), strings.Repeat("─", 45), strings.Repeat("─", 3))
	for _, s := range states {
		p := ""
		if s.Priority > 0 {
			p = fmt.Sprintf("P%d", s.Priority)
		}
		model := s.Model
		if len(model) > 45 {
			model = model[:42] + "..."
		}
		fmt.Fprintf(&sb, "%-14s  %-20s  %-45s  %s\n", s.CLI, s.Provider, model, p)
	}
	return sb.String()
}

// FormatCSV returns CSV string
func FormatCSV(states []CLIState) string {
	var sb strings.Builder
	sb.WriteString("cli,provider,model,priority\n")
	for _, s := range states {
		p := ""
		if s.Priority > 0 {
			p = fmt.Sprintf("P%d", s.Priority)
		}
		fmt.Fprintf(&sb, "%s,%s,%s,%s\n", s.CLI, s.Provider, s.Model, p)
	}
	return sb.String()
}
