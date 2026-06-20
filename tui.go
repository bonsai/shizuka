package main

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

var (
	styleBorder    = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(lipgloss.Color("62"))
	styleSelected  = lipgloss.NewStyle().Background(lipgloss.Color("62")).Foreground(lipgloss.Color("230")).Bold(true)
	styleActive    = lipgloss.NewStyle().Foreground(lipgloss.Color("10")).Bold(true)
	styleDim       = lipgloss.NewStyle().Foreground(lipgloss.Color("240"))
	styleExhausted = lipgloss.NewStyle().Foreground(lipgloss.Color("238"))
	styleHeader    = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("214"))
	styleStatus    = lipgloss.NewStyle().Foreground(lipgloss.Color("10"))
	styleErr       = lipgloss.NewStyle().Foreground(lipgloss.Color("9"))
)

type pane int

const (
	paneCLI      pane = 0
	paneProvider pane = 1
	paneModel    pane = 2
)

type modelEntry struct {
	ID        string
	Exhausted bool
	IsCurrent bool
}

type providerGroup struct {
	Provider string
	Models   []modelEntry
}

type tuiModel struct {
	states     []CLIState
	cliCursor  int
	provCursor int
	modCursor  int
	focus      pane
	status     string
	isErr      bool
	showRef    bool
	providers  []providerGroup

	cliW  int // inner widths for lipgloss boxes
	provW int
	modW  int
}

// computeWidths scans all priority data to find the widest strings in each column.
func computeWidths() (cliW, provW, modW int) {
	cliW, provW, modW = 12, 16, 30
	for _, p := range Priorities {
		if len(p.Name) > cliW {
			cliW = len(p.Name)
		}
		for _, e := range p.Entries {
			if len(e.Provider) > provW {
				provW = len(e.Provider)
			}
			if len(e.Model) > modW {
				modW = len(e.Model)
			}
		}
	}
	for _, r := range QwenRotation {
		if len(r.ModelID) > modW {
			modW = len(r.ModelID)
		}
	}
	cliW += 3
	provW += 3
	modW += 3
	return
}

// buildProviders groups priority entries by provider for the selected CLI.
// For dashscope (UseRotation), expands to all rotation models.
func buildProviders(cli string, currentModel string) []providerGroup {
	pri := findPri(cli)
	if pri == nil {
		return nil
	}

	seen := map[string]bool{}
	var result []providerGroup

	for _, e := range pri.Entries {
		if seen[e.Provider] {
			continue
		}
		seen[e.Provider] = true

		g := providerGroup{Provider: e.Provider}
		if e.UseRotation {
			for _, r := range QwenRotation {
				g.Models = append(g.Models, modelEntry{
					ID:        r.ModelID,
					Exhausted: r.Exhausted,
					IsCurrent: r.ModelID == currentModel,
				})
			}
		} else {
			g.Models = append(g.Models, modelEntry{
				ID:        e.Model,
				IsCurrent: e.Model == currentModel,
			})
		}
		result = append(result, g)
	}
	return result
}

func newTUI() tuiModel {
	states := ReadAll()
	cliW, provW, modW := computeWidths()
	m := tuiModel{
		states: states,
		cliW:   cliW,
		provW:  provW,
		modW:   modW,
	}
	m.rebuildProviders()
	m.syncProvCursor()
	return m
}

func (m *tuiModel) rebuildProviders() {
	if m.cliCursor >= len(m.states) {
		m.providers = nil
		return
	}
	s := m.states[m.cliCursor]
	m.providers = buildProviders(s.CLI, s.Model)
	if m.provCursor >= len(m.providers) {
		m.provCursor = 0
	}
	m.rebuildModCursor()
}

func (m *tuiModel) syncProvCursor() {
	if m.cliCursor >= len(m.states) {
		return
	}
	s := m.states[m.cliCursor]
	for i, g := range m.providers {
		if g.Provider == s.Provider {
			m.provCursor = i
			m.rebuildModCursor()
			return
		}
	}
}

func (m *tuiModel) rebuildModCursor() {
	if m.provCursor >= len(m.providers) {
		m.modCursor = 0
		return
	}
	if m.cliCursor >= len(m.states) {
		m.modCursor = 0
		return
	}
	s := m.states[m.cliCursor]
	for i, me := range m.providers[m.provCursor].Models {
		if me.ID == s.Model {
			m.modCursor = i
			return
		}
	}
	m.modCursor = 0
}

func (m tuiModel) Init() tea.Cmd { return nil }

func (m tuiModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "q", "ctrl+c":
			return m, tea.Quit

		case "?":
			m.showRef = !m.showRef

		case "tab", "right", "l":
			if m.focus < paneModel {
				m.focus++
			}
		case "shift+tab", "left", "h":
			if m.focus > paneCLI {
				m.focus--
			}

		case "up", "k":
			switch m.focus {
			case paneCLI:
				if m.cliCursor > 0 {
					m.cliCursor--
					m.rebuildProviders()
					m.syncProvCursor()
				}
			case paneProvider:
				if m.provCursor > 0 {
					m.provCursor--
					m.rebuildModCursor()
				}
			case paneModel:
				if m.modCursor > 0 {
					m.modCursor--
				}
			}

		case "down", "j":
			switch m.focus {
			case paneCLI:
				if m.cliCursor < len(m.states)-1 {
					m.cliCursor++
					m.rebuildProviders()
					m.syncProvCursor()
				}
			case paneProvider:
				if m.provCursor < len(m.providers)-1 {
					m.provCursor++
					m.rebuildModCursor()
				}
			case paneModel:
				if m.provCursor < len(m.providers) {
					mods := m.providers[m.provCursor].Models
					if m.modCursor < len(mods)-1 {
						m.modCursor++
					}
				}
			}

		case "enter", " ":
			if err := m.applySelected(); err != nil {
				m.status = err.Error()
				m.isErr = true
			} else {
				m.states = ReadAll()
				m.rebuildProviders()
				m.syncProvCursor()
				if m.cliCursor < len(m.states) && m.provCursor < len(m.providers) {
					g := m.providers[m.provCursor]
					mod := ""
					if m.modCursor < len(g.Models) {
						mod = g.Models[m.modCursor].ID
					}
					m.status = fmt.Sprintf("✓ %s → %s / %s", m.states[m.cliCursor].CLI, g.Provider, mod)
				}
				m.isErr = false
			}

		case "n":
			if m.cliCursor < len(m.states) {
				s := m.states[m.cliCursor]
				if err := NextPriority(s.CLI, s); err != nil {
					m.status = err.Error()
					m.isErr = true
				} else {
					m.states = ReadAll()
					m.rebuildProviders()
					m.syncProvCursor()
					m.status = fmt.Sprintf("✓ %s → next priority", s.CLI)
					m.isErr = false
				}
			}

		case "r":
			if m.cliCursor < len(m.states) && m.states[m.cliCursor].CLI == "qwen" {
				if err := RotateQwen(); err != nil {
					m.status = err.Error()
					m.isErr = true
				} else {
					m.states = ReadAll()
					m.rebuildProviders()
					m.syncProvCursor()
					m.status = "✓ qwen rotated → " + m.states[m.cliCursor].Model
					m.isErr = false
				}
			}
		}
	}
	return m, nil
}

func (m *tuiModel) applySelected() error {
	if m.cliCursor >= len(m.states) || m.provCursor >= len(m.providers) {
		return nil
	}
	g := m.providers[m.provCursor]
	if m.modCursor >= len(g.Models) {
		return nil
	}
	cli := m.states[m.cliCursor].CLI
	model := g.Models[m.modCursor].ID
	return Apply(cli, g.Provider, model)
}

func (m tuiModel) View() string {
	leftBox  := styleBorder.Width(m.cliW).Render(m.renderCLI())
	midBox   := styleBorder.Width(m.provW).Render(m.renderProvider())
	rightBox := styleBorder.Width(m.modW).Render(m.renderModel())

	topRow := lipgloss.JoinHorizontal(lipgloss.Top, leftBox, " ", midBox, " ", rightBox)

	// outer widths: each box = inner + 2 (borders), gaps = 1 each
	totalOuterW := (m.cliW + 2) + 1 + (m.provW + 2) + 1 + (m.modW + 2)

	statusLine := ""
	if m.status != "" {
		if m.isErr {
			statusLine = styleErr.Render("✗ " + m.status)
		} else {
			statusLine = styleStatus.Render(m.status)
		}
	}

	refHint := "?  ref"
	if m.showRef {
		refHint = "?  hide ref"
	}
	help := styleDim.Render(fmt.Sprintf(
		"↑↓ move  Tab/→ right  Shift+Tab/← left  Enter apply  n next  r rotate(qwen)  %s  q quit",
		refHint,
	))

	parts := []string{
		styleHeader.Render("  Model Manager"),
		"",
		topRow,
	}

	if m.showRef {
		refInnerW := totalOuterW - 2
		if refInnerW < 60 {
			refInnerW = 60
		}
		refBox := styleBorder.Width(refInnerW).Render(renderReference())
		parts = append(parts, refBox)
	}

	parts = append(parts, statusLine, help)
	return strings.Join(parts, "\n")
}

func (m tuiModel) renderCLI() string {
	var b strings.Builder
	fmt.Fprintln(&b, styleHeader.Render("CLI"))
	for i, s := range m.states {
		label := s.CLI
		cursor := "  "
		if i == m.cliCursor {
			if m.focus == paneCLI {
				cursor = "▶ "
				label = styleSelected.Render(label)
			} else {
				cursor = "▸ "
				label = styleActive.Render(label)
			}
		}
		fmt.Fprintln(&b, cursor+label)
	}
	return strings.TrimRight(b.String(), "\n")
}

func (m tuiModel) renderProvider() string {
	var b strings.Builder
	fmt.Fprintln(&b, styleHeader.Render("Provider"))
	for i, g := range m.providers {
		label := g.Provider
		cursor := "  "
		if i == m.provCursor {
			if m.focus == paneProvider {
				cursor = "▶ "
				label = styleSelected.Render(label)
			} else {
				cursor = "▸ "
				label = styleActive.Render(label)
			}
		}
		fmt.Fprintln(&b, cursor+label)
	}
	return strings.TrimRight(b.String(), "\n")
}

func (m tuiModel) renderModel() string {
	var b strings.Builder
	fmt.Fprintln(&b, styleHeader.Render("Model"))
	if m.provCursor >= len(m.providers) {
		return strings.TrimRight(b.String(), "\n")
	}
	models := m.providers[m.provCursor].Models
	for i, me := range models {
		cursor := "  "
		label := me.ID
		suffix := ""

		if me.Exhausted {
			label = styleExhausted.Render(label)
			suffix = styleExhausted.Render(" ✗")
		} else if me.IsCurrent {
			suffix = styleActive.Render(" ●")
		}

		if i == m.modCursor {
			if m.focus == paneModel {
				cursor = "▶ "
				label = styleSelected.Render(me.ID)
				suffix = "" // selection style subsumes markers
			} else {
				cursor = "▸ "
				if !me.Exhausted && !me.IsCurrent {
					label = lipgloss.NewStyle().Render(me.ID)
				}
			}
		}

		fmt.Fprintln(&b, cursor+label+suffix)
	}
	return strings.TrimRight(b.String(), "\n")
}

func renderReference() string {
	var b strings.Builder
	hdr := styleHeader
	dim := styleDim
	fmt.Fprintln(&b, hdr.Render("CLI Commands"))
	fmt.Fprintln(&b, dim.Render("  model-manager list | csv | priority | rotate"))
	fmt.Fprintln(&b, dim.Render("  model-manager recommend [low|mid|high|code]"))
	fmt.Fprintln(&b, dim.Render("  model-manager exhausted <cli> <model>"))
	fmt.Fprintln(&b, dim.Render("  model-manager <cli> next | p<N> | <provider> [model]"))
	fmt.Fprintln(&b, hdr.Render("MCP Tools"))
	fmt.Fprintln(&b, dim.Render("  mm_list  mm_set  mm_priority  mm_next  mm_rotate"))
	fmt.Fprintln(&b, dim.Render("  mm_exhausted  mm_csv  mm_recommend"))
	fmt.Fprintln(&b, hdr.Render("Skills  (Claude Code)"))
	fmt.Fprint(&b, dim.Render("  /model-manager  /rotate-qwen"))
	return b.String()
}

func RunTUI() error {
	m := newTUI()
	p := tea.NewProgram(m, tea.WithAltScreen())
	_, err := p.Run()
	return err
}
