# モデルコスト最適化レポート

生成: 2026-06-30
ベース: `%APPDATA%/model-manager/data.db` (usage_log)

---

## 現状の使用量サマリ

| CLI | Task | Provider | Model | Tokens In | Tokens Out | 推定コスト |
|-----|------|----------|-------|-----------|------------|-----------|
| claude | high | anthropic | claude-sonnet-4-6 | 95,000 | 5,100 | $0.3615 |
| qwen | mid | dashscope | qwen3.7-max-2026-05-20 | 12,000 | 800 | $0 (free rotation) |
| crush-large | low | anthropic | claude-sonnet-4-6 | 8,500 | 600 | $0.0345 |

**総使用トークン**: 115,500 in / 6,500 out
**総推定コスト**: ~$0.40（データ期間: 2026-05-24 のみ）

---

## コスト最適化レコメンデーション

### 1. low タスクに有料モデルを使わない 🔥

**問題**: `crush-large` が `low` タスクに claude-sonnet-4-6 ($3/1M) を使用。

**提案**: 下記の無料モデルに切り替え:
| 代替モデル | 品質(code) | コスト |
|-----------|-----------|------|
| opencode/big-pickle | ? (stealth) | **無料** ← 最優先 |
| google/gemini-2.5-pro-preview-05-06 | 8 | 無料 (OAuth) |
| openrouter/google/gemini-2.0-flash-exp:free | 7 | 無料 |
| dashscope rotation | 8 | 無料 |

**削減効果**: $0.0345/回 → $0 (100%削減)

### 2. opencode は Big Pickle に設定済み ✅

opencode CLI を `opencode/big-pickle` (無料) に設定完了。
`shizuka/models.json` の優先リストも `opencode-zen → OpenRouter free pool` で整合。

### 3. high タスクの最適化

現状: claude + sonnet-4-6 ($3/$15 per 1M)
| 代替案 | 品質(high) | コスト/1M in | 削減率 |
|-------|-----------|------------|-------|
| opencode/big-pickle | ? | 無料 | 100% |
| gemini-2.5-pro (OAuth) | 9 | 無料 | 100% |
| claude-sonnet-4-6 (現状) | 9 | $3.0 | 基準 |
| aihubmix claude-sonnet-4-6 | 9 | $3.5 | -17% |

gemini-2.5-pro は OAuth 認証済みで品質も 9、無料。high タスクでもまず試す価値あり。

### 4. 全体的な推奨ルーティングポリシー

```
low/mid  → Big Pickle (無料) → Gemini 2.5 Pro (無料) → OpenRouter free pool
high     → Big Pickle → Gemini 2.5 Pro → claude-sonnet-4-6
code     → Big Pickle → Qwen3-Coder-480B (sakura, $0.2) → claude-sonnet-4-6
```

### 5. コスト削減インパクト試算

仮に週 100万トークン処理した場合:
- **現状**: 全量 claude-sonnet-4-6 → **$3.0〜18.0/週**
- **最適化後**: 80% を無料モデル + 20% sonnet-4-6 → **$0.6〜3.6/週**
- **削減率**: **80〜90%**

---

## ログ収集の改善提案

usage_log が 4行のみ。`mm_log_usage` MCP が各種 CLI から呼ばれる仕組みになっているが、
実際の使用量が記録されていない。下記を推奨:

1. 各 CLI の使用後に `shizuka_mm_log_usage` を呼ぶバッチ/フックを設定
2. 最低でも週1回の使用量スナップショット
3. Big Pickle の使用量も記録して品質スコアを測定
