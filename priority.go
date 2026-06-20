package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type PEntry struct {
	Provider    string `json:"provider"`
	Model       string `json:"model,omitempty"`
	Note        string `json:"note,omitempty"`
	UseRotation bool   `json:"useRotation,omitempty"`
}

type CLIPri struct {
	Name    string   `json:"cli"`
	Entries []PEntry `json:"entries"`
}

type RotEntry struct {
	ModelID   string `json:"modelID"`
	Expires   string `json:"expires"`
	Exhausted bool   `json:"exhausted"`
}

type modelsConfig struct {
	Version      int                `json:"version"`
	Priorities   []CLIPri           `json:"priorities"`
	QwenRotation []RotEntry         `json:"qwenRotation"`
	ModelSpecs   []modelSpecJSON    `json:"modelSpecs"`
}

type modelSpecJSON struct {
	Provider string        `json:"provider"`
	Model    string        `json:"model"`
	CostIn   float64       `json:"costIn"`
	Quality  qualityScores `json:"quality"`
}

type qualityScores struct {
	Low  int `json:"low"`
	Mid  int `json:"mid"`
	High int `json:"high"`
	Code int `json:"code"`
}

var Priorities []CLIPri
var QwenRotation []RotEntry

func modelsJSONPath() string {
	return filepath.Join(homeDir(), "Documents", "MEGA", "model-manager", "models.json")
}

func LoadModelsFromJSON() error {
	p := modelsJSONPath()
	data, err := os.ReadFile(p)
	if err != nil {
		return fmt.Errorf("load models.json: %w", err)
	}

	var cfg modelsConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return fmt.Errorf("parse models.json: %w", err)
	}

	Priorities = cfg.Priorities
	QwenRotation = cfg.QwenRotation

	ModelSpecs = make([]ModelSpec, len(cfg.ModelSpecs))
	for i, s := range cfg.ModelSpecs {
		ModelSpecs[i] = ModelSpec{
			Provider:    s.Provider,
			Model:       s.Model,
			CostIn:      s.CostIn,
			QualityLow:  s.Quality.Low,
			QualityMid:  s.Quality.Mid,
			QualityHigh: s.Quality.High,
			QualityCode: s.Quality.Code,
		}
	}
	return nil
}

func SaveModelsToJSON() error {
	cfg := modelsConfig{
		Version:      1,
		Priorities:   Priorities,
		QwenRotation: QwenRotation,
	}
	cfg.ModelSpecs = make([]modelSpecJSON, len(ModelSpecs))
	for i, s := range ModelSpecs {
		cfg.ModelSpecs[i] = modelSpecJSON{
			Provider: s.Provider,
			Model:    s.Model,
			CostIn:   s.CostIn,
			Quality: qualityScores{
				Low:  s.QualityLow,
				Mid:  s.QualityMid,
				High: s.QualityHigh,
				Code: s.QualityCode,
			},
		}
	}

	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(modelsJSONPath(), data, 0644)
}

func currentRotation() string {
	for _, r := range QwenRotation {
		if !r.Exhausted {
			return r.ModelID
		}
	}
	return "qwen3.5-27b"
}

func nextRotation(current string) string {
	found := false
	for _, r := range QwenRotation {
		if r.Exhausted {
			continue
		}
		if found {
			return r.ModelID
		}
		if r.ModelID == current {
			found = true
		}
	}
	return currentRotation()
}

func markRotationExhausted(model string) {
	for i, r := range QwenRotation {
		if r.ModelID == model {
			QwenRotation[i].Exhausted = true
		}
	}
	if db != nil {
		_ = dbMarkRotationExhausted(model)
	}
}

func (e PEntry) resolvedModel() string {
	if e.UseRotation {
		return currentRotation()
	}
	return e.Model
}

func findPri(cliName string) *CLIPri {
	for i := range Priorities {
		if Priorities[i].Name == cliName {
			return &Priorities[i]
		}
	}
	return nil
}


