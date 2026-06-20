package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
)

// Minimal JSON-RPC 2.0 / MCP stdio server

type rpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      interface{}     `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

type rpcResponse struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      interface{} `json:"id"`
	Result  interface{} `json:"result,omitempty"`
	Error   interface{} `json:"error,omitempty"`
}

type mcpTool struct {
	Name        string      `json:"name"`
	Description string      `json:"description"`
	InputSchema interface{} `json:"inputSchema"`
}

var mcpTools = []mcpTool{
	{
		Name:        "mm_list",
		Description: "Get current model/provider state for all CLIs",
		InputSchema: map[string]interface{}{"type": "object", "properties": map[string]interface{}{}},
	},
	{
		Name:        "mm_set",
		Description: "Set a CLI to a specific provider and model",
		InputSchema: map[string]interface{}{
			"type": "object",
			"required": []string{"cli", "provider", "model"},
			"properties": map[string]interface{}{
				"cli":      map[string]string{"type": "string", "description": "CLI name: qwen|opencode|cline|codex|gemini|kilo|kiro|claude|goose"},
				"provider": map[string]string{"type": "string"},
				"model":    map[string]string{"type": "string"},
			},
		},
	},
	{
		Name:        "mm_priority",
		Description: "Set a CLI to priority level N (1=highest)",
		InputSchema: map[string]interface{}{
			"type":     "object",
			"required": []string{"cli", "n"},
			"properties": map[string]interface{}{
				"cli": map[string]string{"type": "string"},
				"n":   map[string]string{"type": "integer", "description": "Priority number (1-based)"},
			},
		},
	},
	{
		Name:        "mm_next",
		Description: "Advance a CLI to its next priority level",
		InputSchema: map[string]interface{}{
			"type":     "object",
			"required": []string{"cli"},
			"properties": map[string]interface{}{
				"cli": map[string]string{"type": "string"},
			},
		},
	},
	{
		Name:        "mm_rotate",
		Description: "Rotate Qwen CLI to the next dashscope free-quota model",
		InputSchema: map[string]interface{}{"type": "object", "properties": map[string]interface{}{}},
	},
	{
		Name:        "mm_exhausted",
		Description: "Mark a qwen dashscope model as exhausted and rotate",
		InputSchema: map[string]interface{}{
			"type":     "object",
			"required": []string{"model"},
			"properties": map[string]interface{}{
				"model": map[string]string{"type": "string"},
			},
		},
	},
	{
		Name:        "mm_csv",
		Description: "Get current state as CSV",
		InputSchema: map[string]interface{}{"type": "object", "properties": map[string]interface{}{}},
	},
	{
		Name:        "mm_recommend",
		Description: "Recommend best CLI+model for a task. Scores by quality and cost (free > paid). task: low|mid|high|code",
		InputSchema: map[string]interface{}{
			"type": "object",
			"properties": map[string]interface{}{
				"task": map[string]string{"type": "string", "description": "Task difficulty: low | mid | high | code"},
			},
		},
	},
	{
		Name:        "mm_quota",
		Description: "Check Anthropic (Claude) API rate-limit usage. Returns requests and token remaining/limit/reset.",
		InputSchema: map[string]interface{}{
			"type":       "object",
			"properties": map[string]interface{}{},
		},
	},
	{
		Name:        "mm_log_usage",
		Description: "Log token usage for a CLI session. Call this after each AI response to track consumption.",
		InputSchema: map[string]interface{}{
			"type":     "object",
			"required": []string{"cli", "tokens_in", "tokens_out"},
			"properties": map[string]interface{}{
				"cli":        map[string]string{"type": "string", "description": "CLI name: qwen|opencode|cline|codex|gemini|kilo|kiro|claude"},
				"tokens_in":  map[string]string{"type": "integer", "description": "Input tokens consumed"},
				"tokens_out": map[string]string{"type": "integer", "description": "Output tokens generated"},
				"provider":   map[string]string{"type": "string", "description": "Provider (auto-detected from current state if omitted)"},
				"model":      map[string]string{"type": "string", "description": "Model ID (auto-detected from current state if omitted)"},
				"task":       map[string]string{"type": "string", "description": "Task level: low|mid|high|code (default: mid)"},
			},
		},
	},
	{
		Name:        "mm_usage",
		Description: "Show token consumption graph for the last N days. Returns sparkline + bar charts per day/CLI/provider.",
		InputSchema: map[string]interface{}{
			"type": "object",
			"properties": map[string]interface{}{
				"days": map[string]string{"type": "integer", "description": "Number of days to show (default: 14)"},
			},
		},
	},
}

func RunMCP() {
	scanner := bufio.NewScanner(os.Stdin)
	enc := json.NewEncoder(os.Stdout)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var req rpcRequest
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			continue
		}
		resp := handleRPC(req)
		_ = enc.Encode(resp)
	}
}

func handleRPC(req rpcRequest) rpcResponse {
	switch req.Method {
	case "initialize":
		return rpcResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Result: map[string]interface{}{
				"protocolVersion": "2024-11-05",
				"serverInfo":      map[string]string{"name": "model-manager", "version": "1.0.0"},
				"capabilities":    map[string]interface{}{"tools": map[string]interface{}{}},
			},
		}

	case "notifications/initialized":
		return rpcResponse{JSONRPC: "2.0", ID: req.ID}

	case "tools/list":
		return rpcResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Result:  map[string]interface{}{"tools": mcpTools},
		}

	case "tools/call":
		var p struct {
			Name      string                 `json:"name"`
			Arguments map[string]interface{} `json:"arguments"`
		}
		if err := json.Unmarshal(req.Params, &p); err != nil {
			return errResp(req.ID, -32602, err.Error())
		}
		result, err := callTool(p.Name, p.Arguments)
		if err != nil {
			return rpcResponse{
				JSONRPC: "2.0",
				ID:      req.ID,
				Result: map[string]interface{}{
					"content": []map[string]string{{"type": "text", "text": "Error: " + err.Error()}},
					"isError": true,
				},
			}
		}
		return rpcResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Result: map[string]interface{}{
				"content": []map[string]string{{"type": "text", "text": result}},
			},
		}

	default:
		return errResp(req.ID, -32601, "method not found: "+req.Method)
	}
}

func callTool(name string, args map[string]interface{}) (string, error) {
	switch name {
	case "mm_list":
		return FormatStatus(ReadAll()), nil

	case "mm_set":
		cli, _ := args["cli"].(string)
		prov, _ := args["provider"].(string)
		model, _ := args["model"].(string)
		if err := Apply(cli, prov, model); err != nil {
			return "", err
		}
		return fmt.Sprintf("✓ %s → %s/%s", cli, prov, model), nil

	case "mm_priority":
		cli, _ := args["cli"].(string)
		nRaw := args["n"]
		n := 1
		switch v := nRaw.(type) {
		case float64:
			n = int(v)
		case string:
			n, _ = strconv.Atoi(v)
		}
		if err := ApplyPriority(cli, n); err != nil {
			return "", err
		}
		return fmt.Sprintf("✓ %s → P%d", cli, n), nil

	case "mm_next":
		cli, _ := args["cli"].(string)
		states := ReadAll()
		var cur CLIState
		for _, s := range states {
			if s.CLI == cli {
				cur = s
				break
			}
		}
		if err := NextPriority(cli, cur); err != nil {
			return "", err
		}
		return fmt.Sprintf("✓ %s advanced to next priority", cli), nil

	case "mm_rotate":
		if err := RotateQwen(); err != nil {
			return "", err
		}
		s := readQwen()
		return fmt.Sprintf("✓ qwen rotated → %s", s.Model), nil

	case "mm_exhausted":
		model, _ := args["model"].(string)
		if err := ExhaustQwen(model); err != nil {
			return "", err
		}
		s := readQwen()
		return fmt.Sprintf("✓ %s marked exhausted, qwen → %s", model, s.Model), nil

	case "mm_csv":
		return FormatCSV(ReadAll()), nil

	case "mm_recommend":
		task, _ := args["task"].(string)
		if task == "" {
			task = "mid"
		}
		recs := Recommend(task)
		return FormatRecommend(task, recs), nil

	case "mm_quota":
		info, err := CheckAnthropicQuota()
		if err != nil {
			return "", err
		}
		return FormatQuota(info), nil

	case "mm_log_usage":
		cli, _ := args["cli"].(string)
		provider, _ := args["provider"].(string)
		model, _ := args["model"].(string)
		task, _ := args["task"].(string)
		if task == "" {
			task = "mid"
		}
		var tokIn, tokOut int
		switch v := args["tokens_in"].(type) {
		case float64:
			tokIn = int(v)
		}
		switch v := args["tokens_out"].(type) {
		case float64:
			tokOut = int(v)
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
			return "", err
		}
		return fmt.Sprintf("✓ logged %s  in:%s  out:%s", cli, fmtSI2(tokIn), fmtSI2(tokOut)), nil

	case "mm_usage":
		days := 14
		if v, ok := args["days"].(float64); ok {
			days = int(v)
		}
		return FormatUsageGraph(days), nil

	default:
		return "", fmt.Errorf("unknown tool: %s", name)
	}
}

func errResp(id interface{}, code int, msg string) rpcResponse {
	return rpcResponse{
		JSONRPC: "2.0",
		ID:      id,
		Error:   map[string]interface{}{"code": code, "message": msg},
	}
}
