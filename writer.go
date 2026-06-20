package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
)

// Apply sets the given CLI to the specified provider+model
func Apply(cli, provider, model string) error {
	switch cli {
	case "qwen":
		return applyQwen(provider, model)
	case "opencode":
		return applyOpencode(provider, model)
	case "cline":
		return applyCline(provider, model)
	case "codex":
		return applyCodex(model)
	case "gemini":
		return applyGemini(model)
	case "kilo":
		return applyKilo(provider, model)
	case "kiro":
		return applyKiro(provider, model)
	case "qwencode":
		return applyQwencode(provider, model)
	case "goose":
		return applyGoose(provider, model)
	case "claude":
		return applyClaude(provider, model)
	case "crush-large":
		return applyCrush("large", provider, model)
	case "crush-small":
		return applyCrush("small", provider, model)
	default:
		return fmt.Errorf("unknown CLI: %s", cli)
	}
}

// ApplyPriority sets CLI to the Nth priority entry (1-based)
func ApplyPriority(cli string, n int) error {
	pri := findPri(cli)
	if pri == nil {
		return fmt.Errorf("no priority list for %s", cli)
	}
	if n < 1 || n > len(pri.Entries) {
		return fmt.Errorf("priority %d out of range (1-%d) for %s", n, len(pri.Entries), cli)
	}
	e := pri.Entries[n-1]
	model := e.resolvedModel()
	return Apply(cli, e.Provider, model)
}

// NextPriority advances cli to the next priority entry
func NextPriority(cli string, current CLIState) error {
	pri := findPri(cli)
	if pri == nil {
		return fmt.Errorf("no priority list for %s", cli)
	}
	next := current.Priority // 1-based
	if next <= 0 {
		next = 1
	} else {
		next++
	}
	if next > len(pri.Entries) {
		next = 1
	}
	return ApplyPriority(cli, next)
}

// ── Qwen ─────────────────────────────────────────────────────────────────────

func applyQwen(provider, model string) error {
	path := filepath.Join(homeDir(), ".qwen", "settings.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}

	// Determine baseUrl and envKey
	baseURL := "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
	envKey := "DASHSCOPE_API_KEY"
	switch provider {
	case "sakura":
		baseURL = "https://api.ai.sakura.ad.jp/v1"
		envKey = "SAKURA_API_KEY"
	case "aihubmix":
		baseURL = "https://aihubmix.com/v1"
		envKey = "AIHUBMIX_API_KEY"
	case "openrouter":
		baseURL = "https://openrouter.ai/api/v1"
		envKey = "OPENROUTER_API_KEY"
	}

	// Ensure model is in modelProviders.openai[]
	var mpRaw map[string]json.RawMessage
	if err := json.Unmarshal(raw["modelProviders"], &mpRaw); err != nil {
		return err
	}
	var providers []map[string]interface{}
	if err := json.Unmarshal(mpRaw["openai"], &providers); err != nil {
		return err
	}
	found := false
	for _, p := range providers {
		if p["id"] == model {
			found = true
			break
		}
	}
	if !found {
		namePrefix := "[Free Quota]"
		if provider == "sakura" {
			namePrefix = "[Sakura]"
		} else if provider == "anthropic" {
			namePrefix = "[Anthropic]"
		}
		providers = append(providers, map[string]interface{}{
			"id":      model,
			"name":    fmt.Sprintf("%s %s", namePrefix, model),
			"baseUrl": baseURL,
			"envKey":  envKey,
		})
		newOpenAI, _ := json.Marshal(providers)
		mpRaw["openai"] = newOpenAI
		newMP, _ := json.Marshal(mpRaw)
		raw["modelProviders"] = newMP
	}

	// Set model.name
	modelObj := map[string]string{"name": model}
	raw["model"], _ = json.Marshal(modelObj)

	out, err := json.MarshalIndent(raw, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0644)
}

// ── OpenCode ─────────────────────────────────────────────────────────────────

func applyOpencode(provider, model string) error {
	path := filepath.Join(homeDir(), ".config", "opencode", "opencode.jsonc")
	// Build full model ref: "provider/model"
	modelRef := provider + "/" + model

	cfg := map[string]interface{}{
		"$schema": "https://opencode.ai/config.json",
		"model":   modelRef,
		"provider": map[string]interface{}{
			provider: map[string]interface{}{},
		},
	}
	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0644)
}

// ── Cline ─────────────────────────────────────────────────────────────────────

func applyCline(provider, model string) error {
	// Map provider names to cline --provider values
	clineProvider := provider
	extraArgs := []string{}
	switch provider {
	case "openai-native", "sakura":
		clineProvider = "openai-native"
		key := os.Getenv("SAKURA_API_KEY")
		if key == "" {
			return fmt.Errorf("SAKURA_API_KEY not set")
		}
		extraArgs = []string{"--baseurl", "https://api.ai.sakura.ad.jp/v1", "--apikey", key}
	case "openrouter":
		clineProvider = "openrouter"
		key := os.Getenv("OPENROUTER_API_KEY")
		if key == "" {
			return fmt.Errorf("OPENROUTER_API_KEY not set")
		}
		extraArgs = []string{"--apikey", key}
	case "anthropic":
		clineProvider = "anthropic"
	}
	args := append([]string{"auth", "--provider", clineProvider, "--modelid", model}, extraArgs...)
	cmd := exec.Command("cline", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

// ── Codex ─────────────────────────────────────────────────────────────────────

func applyCodex(model string) error {
	path := filepath.Join(homeDir(), "Documents", "MEGA", ".codex-local", "sakura-ai.env")
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	lines := strings.Split(string(data), "\n")
	re := regexp.MustCompile(`^OPENAI_MODEL=.*`)
	replaced := false
	for i, l := range lines {
		if re.MatchString(l) {
			lines[i] = "OPENAI_MODEL=" + model
			replaced = true
		}
	}
	if !replaced {
		lines = append(lines, "OPENAI_MODEL="+model)
	}
	return os.WriteFile(path, []byte(strings.Join(lines, "\n")), 0644)
}

// ── Gemini ─────────────────────────────────────────────────────────────────────

func applyGemini(model string) error {
	path := filepath.Join(homeDir(), ".gemini", "settings.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var cfg map[string]interface{}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return err
	}
	cfg["model"] = model
	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0644)
}

// ── Kilo ─────────────────────────────────────────────────────────────────────

func applyKilo(provider, model string) error {
	// kilo uses OPENROUTER_API_KEY or ANTHROPIC_API_KEY env vars
	// Write to ~/.kilo/settings.json (create if missing)
	dir := filepath.Join(homeDir(), ".kilo")
	_ = os.MkdirAll(dir, 0755)
	path := filepath.Join(dir, "settings.json")
	cfg := map[string]interface{}{
		"provider": provider,
		"model":    model,
	}
	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0644)
}

// ── Kiro ─────────────────────────────────────────────────────────────────────

func applyKiro(provider, model string) error {
	// Kiro uses project-local .kiro/settings.json
	path := filepath.Join(homeDir(), "Documents", "MEGA", ".kiro", "settings.json")
	data, err := os.ReadFile(path)
	cfg := map[string]interface{}{}
	if err == nil {
		_ = json.Unmarshal(data, &cfg)
	}
	cfg["provider"] = provider
	cfg["model"] = model
	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0644)
}

// ── Claude ─────────────────────────────────────────────────────────────────────

// applyClaude switches Claude Code between Anthropic direct and the Sakura AI
// Engine. Sakura is applied via the settings.json "env" section
// (ANTHROPIC_BASE_URL override); switching back to anthropic removes it.
func applyClaude(provider, model string) error {
	path := filepath.Join(homeDir(), ".claude", "settings.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	cfg := map[string]interface{}{}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return fmt.Errorf("parse %s: %w", path, err)
	}

	env, _ := cfg["env"].(map[string]interface{})

	switch provider {
	case "sakura":
		key, err := sakuraAPIKey()
		if err != nil {
			return err
		}
		if env == nil {
			env = map[string]interface{}{}
		}
		env["ANTHROPIC_BASE_URL"] = "https://api.ai.sakura.ad.jp"
		env["ANTHROPIC_AUTH_TOKEN"] = key
		env["ANTHROPIC_MODEL"] = model
		env["ANTHROPIC_SMALL_FAST_MODEL"] = "gpt-oss-120b"
		cfg["env"] = env
	default: // anthropic
		if env != nil {
			delete(env, "ANTHROPIC_BASE_URL")
			delete(env, "ANTHROPIC_AUTH_TOKEN")
			delete(env, "ANTHROPIC_MODEL")
			delete(env, "ANTHROPIC_SMALL_FAST_MODEL")
			if len(env) == 0 {
				delete(cfg, "env")
			}
		}
		cfg["model"] = model
	}

	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0644)
}

// sakuraAPIKey reads the canonical Sakura AI Engine key from opencode.jsonc
// (single source of truth; see /model-manager skill notes).
func sakuraAPIKey() (string, error) {
	path := filepath.Join(homeDir(), ".config", "opencode", "opencode.jsonc")
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("sakura key source not found: %w", err)
	}
	cleaned := stripComments(string(data))
	var cfg struct {
		Provider map[string]struct {
			Options struct {
				APIKey string `json:"apiKey"`
			} `json:"options"`
			APIKey string `json:"apiKey"`
		} `json:"provider"`
	}
	if err := json.Unmarshal([]byte(cleaned), &cfg); err != nil {
		return "", fmt.Errorf("parse opencode.jsonc: %w", err)
	}
	if p, ok := cfg.Provider["sakura"]; ok {
		if p.APIKey != "" {
			return p.APIKey, nil
		}
		if p.Options.APIKey != "" {
			return p.Options.APIKey, nil
		}
	}
	return "", fmt.Errorf("sakura apiKey not found in opencode.jsonc")
}

// ── QwenCode ───────────────────────────────────────────────────────────────────

func applyQwencode(provider, model string) error {
	dir := filepath.Join(homeDir(), ".qwencode")
	_ = os.MkdirAll(dir, 0755)
	path := filepath.Join(dir, "settings.json")
	cfg := map[string]interface{}{
		"provider": provider,
		"model":    model,
	}
	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0644)
}

// ── Goose ─────────────────────────────────────────────────────────────────────

func applyGoose(provider, model string) error {
	configDir := filepath.Join(os.Getenv("APPDATA"), "Block", "goose", "config")
	path := filepath.Join(configDir, "config.yaml")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		return err
	}

	baseURL := ""
	switch {
	case strings.HasPrefix(model, "deepseek"):
		baseURL = "https://api.deepseek.com/v1"
	}

	var yamlContent string
	if baseURL != "" {
		providerBlock := fmt.Sprintf(`  %s:
    enabled: true
    model: %s
    base_url: %s
    configured: true`, provider, model, baseURL)

		yamlContent = fmt.Sprintf(`active_provider: %s
providers:
%s
`, provider, providerBlock)
	} else {
		providerBlock := fmt.Sprintf(`  %s:
    enabled: true
    model: %s
    configured: true`, provider, model)

		yamlContent = fmt.Sprintf(`active_provider: %s
providers:
%s
`, provider, providerBlock)
	}

	return os.WriteFile(path, []byte(yamlContent), 0644)
}

// ── Qwen rotation ─────────────────────────────────────────────────────────────

// ── Crush ──────────────────────────────────────────────────────────────────────

func applyCrush(which, provider, model string) error {
	dir := filepath.Join(localAppData(), "crush")
	_ = os.MkdirAll(dir, 0755)
	path := filepath.Join(dir, "crush.json")

	data, err := os.ReadFile(path)
	cfg := map[string]map[string]string{}
	if err == nil {
		_ = json.Unmarshal(data, &cfg)
	}
	if cfg["large"] == nil {
		cfg["large"] = map[string]string{}
	}
	if cfg["small"] == nil {
		cfg["small"] = map[string]string{}
	}
	entry := cfg[which]
	entry["provider"] = provider
	entry["model"] = model
	cfg[which] = entry

	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0644)
}

func RotateQwen() error {
	state := readQwen()
	nextModel := nextRotation(state.Model)
	return applyQwen("dashscope", nextModel)
}

func ExhaustQwen(model string) error {
	markRotationExhausted(model)
	return RotateQwen()
}
