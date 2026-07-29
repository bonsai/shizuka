#!/usr/bin/env python3
"""
collect_daily_tokens.py

Claude Code CLI のセッションログ (~/.claude/projects/**/*.jsonl) をスキャンし、
日次・モデル別のToken消費をCSVに集計する。

「shizuka」構想のMVP第一歩：DB/BigQueryなし、まずローカルでCSVを吐くだけ。

使い方:
    python3 collect_daily_tokens.py
    python3 collect_daily_tokens.py --projects-dir /path/to/.claude/projects
    python3 collect_daily_tokens.py --out daily_tokens.csv

注意事項（重要）:
    - message.usage.output_tokens はJSONLログ上ではプレースホルダ値(1〜2程度)しか
      入っておらず、実際の出力トークン数を反映していないケースが報告されている。
      正確な output_tokens が必要な場合は `claude -p ... --output-format json` の
      結果を別途ロギングする必要がある。このスクリプトは input / cache 系は
      正確な値として扱い、output_tokens は「参考値（過小評価の可能性あり）」として
      別カラムに出す。
    - cache_creation_input_tokens / cache_read_input_tokens は実消費コストに直結する
      重要な指標なので、Token/Commit などの指標を作る際はこちらを主軸にする方が安全。
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


def default_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def iter_assistant_records(jsonl_path: Path):
    """1つのJSONLセッションファイルから assistant type のレコードだけを取り出す"""
    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "assistant":
                    yield record
    except OSError as e:
        print(f"  [WARN] 読み込み失敗: {jsonl_path} ({e})", file=sys.stderr)


def collect(projects_dir: Path):
    """
    集計キー: (date, model)
    """
    agg = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens_reported": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "message_count": 0,
        "session_ids": set(),
    })

    jsonl_files = sorted(projects_dir.glob("**/*.jsonl"))
    if not jsonl_files:
        print(f"[INFO] JSONLファイルが見つかりません: {projects_dir}", file=sys.stderr)
        return agg

    print(f"[INFO] {len(jsonl_files)} 件のセッションログを走査します...", file=sys.stderr)

    for jsonl_path in jsonl_files:
        for record in iter_assistant_records(jsonl_path):
            message = record.get("message", {})
            usage = message.get("usage")
            timestamp = record.get("timestamp")
            if not usage or not timestamp:
                continue

            date = timestamp[:10]  # "2026-07-29T..." -> "2026-07-29"
            model = message.get("model", "unknown")
            key = (date, model)

            bucket = agg[key]
            bucket["input_tokens"] += usage.get("input_tokens", 0) or 0
            bucket["output_tokens_reported"] += usage.get("output_tokens", 0) or 0
            bucket["cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens", 0) or 0
            bucket["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
            bucket["message_count"] += 1
            session_id = record.get("sessionId")
            if session_id:
                bucket["session_ids"].add(session_id)

    return agg


def write_csv(agg, out_path: Path):
    rows = []
    for (date, model), b in agg.items():
        rows.append({
            "date": date,
            "model": model,
            "input_tokens": b["input_tokens"],
            "output_tokens_reported": b["output_tokens_reported"],
            "cache_creation_input_tokens": b["cache_creation_input_tokens"],
            "cache_read_input_tokens": b["cache_read_input_tokens"],
            "message_count": b["message_count"],
            "session_count": len(b["session_ids"]),
        })

    rows.sort(key=lambda r: (r["date"], r["model"]))

    fieldnames = [
        "date", "model", "input_tokens", "output_tokens_reported",
        "cache_creation_input_tokens", "cache_read_input_tokens",
        "message_count", "session_count",
    ]

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[INFO] {len(rows)} 行を書き出しました -> {out_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Claude Code CLIログから日次Token集計を作る")
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=default_projects_dir(),
        help="Claude Codeのprojectsディレクトリ (デフォルト: ~/.claude/projects)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("daily_tokens.csv"),
        help="出力CSVパス (デフォルト: ./daily_tokens.csv)",
    )
    args = parser.parse_args()

    if not args.projects_dir.exists():
        print(f"[ERROR] ディレクトリが存在しません: {args.projects_dir}", file=sys.stderr)
        sys.exit(1)

    agg = collect(args.projects_dir)
    if not agg:
        print("[WARN] 集計結果が0件でした。パスや権限を確認してください。", file=sys.stderr)
        sys.exit(0)

    write_csv(agg, args.out)


if __name__ == "__main__":
    main()
