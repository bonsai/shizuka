package main

import (
	"fmt"
	"sort"
	"strings"
)

// ModelSpec holds cost and quality data for scoring/recommend
type ModelSpec struct {
	Provider    string
	Model       string  // empty = wildcard (any model from this provider)
	CostIn      float64 // USD per 1M input tokens; 0 = free
	QualityLow  int     // 0-10 fitness for low-effort tasks
	QualityMid  int
	QualityHigh int
	QualityCode int
}

// ModelSpecs — cost + quality table, loaded from models.json
var ModelSpecs []ModelSpec

// Recommendation is one scored result from Recommend()
type Recommendation struct {
	CLI      string
	Provider string
	Model    string
	Score    float64
	Reason   string
}

// Recommend scores every priority-table entry by task difficulty and returns top 5.
// task: "low" | "mid" | "high" | "code"
func Recommend(task string) []Recommendation {
	task = strings.ToLower(strings.TrimSpace(task))
	switch task {
	case "medium", "":
		task = "mid"
	case "l":
		task = "low"
	case "h":
		task = "high"
	case "c":
		task = "code"
	}

	var results []Recommendation
	seen := map[string]bool{}

	for _, pri := range Priorities {
		for _, e := range pri.Entries {
			model := e.resolvedModel()
			key := pri.Name + "|" + e.Provider + "|" + model
			if seen[key] {
				continue
			}
			seen[key] = true

			spec := lookupSpec(e.Provider, model)
			if spec == nil {
				continue
			}

			quality := specQuality(spec, task)
			// normalize cost: $15/1M → 10.0 penalty; $0 → 0 penalty
			costPenalty := spec.CostIn / 1.5
			if costPenalty > 10 {
				costPenalty = 10
			}

			score := float64(quality) - costPenalty

			var reason string
			if spec.CostIn == 0 {
				reason = fmt.Sprintf("quality=%d/10  cost=free", quality)
			} else {
				reason = fmt.Sprintf("quality=%d/10  cost=$%.1f/1M", quality, spec.CostIn)
			}

			results = append(results, Recommendation{
				CLI:      pri.Name,
				Provider: e.Provider,
				Model:    model,
				Score:    score,
				Reason:   reason,
			})
		}
	}

	sort.Slice(results, func(i, j int) bool {
		return results[i].Score > results[j].Score
	})

	if len(results) > 5 {
		results = results[:5]
	}
	return results
}

func lookupSpec(provider, model string) *ModelSpec {
	// exact match
	for i := range ModelSpecs {
		if ModelSpecs[i].Provider == provider && ModelSpecs[i].Model == model {
			return &ModelSpecs[i]
		}
	}
	// wildcard (empty Model matches any)
	for i := range ModelSpecs {
		if ModelSpecs[i].Provider == provider && ModelSpecs[i].Model == "" {
			return &ModelSpecs[i]
		}
	}
	return nil
}

func specQuality(s *ModelSpec, task string) int {
	switch task {
	case "low":
		return s.QualityLow
	case "mid":
		return s.QualityMid
	case "high":
		return s.QualityHigh
	case "code":
		return s.QualityCode
	default:
		return s.QualityMid
	}
}

// FormatMeta shows ModelSpec metadata (pricing + quality) for currently active models.
func FormatMeta(states []CLIState) string {
	var b strings.Builder
	fmt.Fprintln(&b, "\nModel metadata (cost & quality scores):")
	fmt.Fprintf(&b, "%-13s  %-22s  %-40s  %8s  %3s  %3s  %4s  %4s\n",
		"CLI", "Provider", "Model", "$/1M in", "Low", "Mid", "High", "Code")
	fmt.Fprintln(&b, strings.Repeat("─", 110))
	for _, s := range states {
		spec := lookupSpec(s.Provider, s.Model)
		if spec == nil {
			fmt.Fprintf(&b, "%-13s  %-22s  %-40s  %8s\n", s.CLI, s.Provider, s.Model, "—")
			continue
		}
		cost := "free"
		if spec.CostIn > 0 {
			cost = fmt.Sprintf("$%.3f", spec.CostIn)
		}
		model := s.Model
		if len(model) > 38 {
			model = model[:35] + "..."
		}
		fmt.Fprintf(&b, "%-13s  %-22s  %-40s  %8s  %3d  %3d  %4d  %4d\n",
			s.CLI, s.Provider, model, cost,
			spec.QualityLow, spec.QualityMid, spec.QualityHigh, spec.QualityCode)
	}
	return b.String()
}

func FormatRecommend(task string, recs []Recommendation) string {
	var b strings.Builder
	label := map[string]string{
		"low":  "低負荷 (low)",
		"mid":  "中負荷 (mid)",
		"high": "高負荷 (high)",
		"code": "コーディング (code)",
	}[task]
	if label == "" {
		label = task
	}
	fmt.Fprintf(&b, "Best options for %s:\n\n", label)
	fmt.Fprintf(&b, "%-3s  %-13s  %-22s  %-36s  %s\n", "#", "CLI", "Provider", "Model", "Score")
	fmt.Fprintln(&b, strings.Repeat("─", 100))
	for i, r := range recs {
		model := r.Model
		if len(model) > 34 {
			model = model[:31] + "..."
		}
		fmt.Fprintf(&b, "%-3d  %-13s  %-22s  %-36s  %.1f  [%s]\n",
			i+1, r.CLI, r.Provider, model, r.Score, r.Reason)
	}
	if len(recs) > 0 {
		top := recs[0]
		fmt.Fprintf(&b, "\n→ Recommended: model-manager %s %s %s\n", top.CLI, top.Provider, top.Model)
	}
	return b.String()
}
