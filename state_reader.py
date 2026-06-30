"""state_reader.py — 各 CLI の現在の設定を読み取り"""
import json
import os
import re
from pathlib import Path

HOME = Path.home()
MEGA = Path(os.environ.get("MEGA", HOME / "Documents" / "MEGA"))
LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
APPDATA = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))


class CLIState:
    def __init__(self, cli: str, provider: str = "", model: str = "", priority: int = 0):
        self.cli = cli
        self.provider = provider
        self.model = model
        self.priority = priority

    def __repr__(self):
        return f"CLIState({self.cli}, {self.provider}, {self.model}, P{self.priority})"


def _strip_jsonc_comments(text: str) -> str:
    out = []
    in_str = False
    escaped = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == '/' and i + 1 < len(text) and text[i + 1] == '/':
            while i < len(text) and text[i] != '\n':
                i += 1
            if i < len(text):
                out.append('\n')
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _read_jsonc(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        return json.loads(_strip_jsonc_comments(raw))
    except Exception:
        return {}


def _infer_priority(cli: str, provider: str, model: str) -> int:
    try:
        from db import get_db, get_cli_priorities
        conn = get_db()
        entries = get_cli_priorities(conn, cli)
        conn.close()
        for i, e in enumerate(entries):
            if e["provider"] == provider:
                if e["use_rotation"] or e["model_id"] == model or not model:
                    return i + 1
    except Exception:
        pass
    return 0


# ── Individual CLI readers ────────────────────────────────────────────────

def read_qwen() -> CLIState:
    s = CLIState("qwen")
    cfg = _read_json(HOME / ".qwen" / "settings.json")
    if not cfg:
        return s
    s.model = (cfg.get("model") or {}).get("name") or ""
    providers = (cfg.get("modelProviders") or {}).get("openai") or []
    for p in providers:
        if isinstance(p, dict) and p.get("id") == s.model:
            base = (p.get("baseUrl") or "").lower()
            if "sakura" in base:
                s.provider = "sakura"
            else:
                s.provider = "dashscope"
            break
    if not s.provider and s.model:
        s.provider = "dashscope"
    s.priority = _infer_priority("qwen", s.provider, s.model)
    return s


def read_opencode() -> CLIState:
    s = CLIState("opencode")
    cfg = _read_jsonc(HOME / ".config" / "opencode" / "opencode.jsonc")
    if not cfg:
        return s
    model_raw = cfg.get("model", "")
    if isinstance(model_raw, str) and "/" in model_raw:
        parts = model_raw.split("/", 1)
        prov_map = cfg.get("provider", {})
        if isinstance(prov_map, dict) and parts[0] in prov_map:
            s.provider = parts[0]
            s.model = parts[1]
    if not s.model and isinstance(model_raw, str):
        s.model = model_raw
    if not s.provider:
        from db import get_db, get_cli_priorities
        try:
            conn = get_db()
            entries = get_cli_priorities(conn, "opencode")
            conn.close()
            for e in entries:
                if e["model_id"] == s.model:
                    s.provider = e["provider"]
                    break
        except Exception:
            pass
    if not s.provider:
        prov_map = cfg.get("provider", {})
        if isinstance(prov_map, dict) and prov_map:
            s.provider = sorted(prov_map.keys())[0]
    s.priority = _infer_priority("opencode", s.provider, s.model)
    return s


def read_cline() -> CLIState:
    s = CLIState("cline")
    for name in ["config.json", "auth.json", "settings.json"]:
        cfg = _read_json(HOME / ".cline" / name)
        if cfg:
            s.provider = cfg.get("provider", "")
            s.model = cfg.get("model", "")
            break
    if not s.provider:
        s.provider = "openai-native"
        s.model = "Qwen3-Coder-480B-A35B-Instruct-FP8"
    s.priority = _infer_priority("cline", s.provider, s.model)
    return s


def read_codex() -> CLIState:
    s = CLIState("codex")
    path = MEGA / ".codex-local" / "sakura-ai.env"
    if not path.exists():
        return s
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "OPENAI_MODEL":
            s.model = v
        elif k == "OPENAI_BASE_URL" and "sakura" in v.lower():
            s.provider = "sakura"
    s.priority = _infer_priority("codex", s.provider, s.model)
    return s


def read_gemini() -> CLIState:
    s = CLIState("gemini", provider="google-oauth")
    cfg = _read_json(HOME / ".gemini" / "settings.json")
    if cfg:
        s.model = cfg.get("model", "gemini-2.5-pro-preview-05-06")
    else:
        s.model = "gemini-2.5-pro-preview-05-06"
    s.priority = _infer_priority("gemini", s.provider, s.model)
    return s


def read_kilo() -> CLIState:
    s = CLIState("kilo")
    # settings.json has flat provider string; config.json has nested provider blocks
    for path in [
        HOME / ".kilo" / "settings.json",
        LOCAL_APPDATA / "kilo" / "settings.json",
        HOME / ".kilo" / "config.json",
    ]:
        cfg = _read_json(path)
        if cfg:
            raw = cfg.get("provider", "")
            s.provider = raw if isinstance(raw, str) else ""
            s.model = cfg.get("model", "")
            break
    s.priority = _infer_priority("kilo", s.provider, s.model)
    return s


def read_kiro() -> CLIState:
    s = CLIState("kiro")
    for base in [Path("."), MEGA]:
        cfg = _read_json(base / ".kiro" / "settings.json")
        if cfg:
            s.provider = cfg.get("provider", "amazon-bedrock")
            s.model = cfg.get("model", "")
            break
    if not s.provider:
        s.provider = "amazon-bedrock"
    s.priority = _infer_priority("kiro", s.provider, s.model)
    return s


def read_claude() -> CLIState:
    s = CLIState("claude", provider="anthropic", model="(default)")
    cfg = _read_json(HOME / ".claude" / "settings.json")
    if cfg:
        env = cfg.get("env", {})
        if isinstance(env, dict) and "ANTHROPIC_BASE_URL" in env:
            base = env["ANTHROPIC_BASE_URL"].lower()
            if "sakura" in base:
                s.provider = "sakura"
                s.model = env.get("ANTHROPIC_MODEL", "")
                s.priority = _infer_priority("claude", s.provider, s.model)
                return s
        m = cfg.get("model", "")
        if m:
            s.model = m
    s.priority = _infer_priority("claude", s.provider, s.model)
    if s.priority == 0 and s.model == "(default)":
        s.priority = 1
    return s


def read_qwencode() -> CLIState:
    s = CLIState("qwencode")
    cfg = _read_json(HOME / ".qwencode" / "settings.json")
    if cfg:
        s.provider = cfg.get("provider", "")
        s.model = cfg.get("model", "")
    else:
        from db import get_db, get_cli_priorities
        try:
            conn = get_db()
            entries = get_cli_priorities(conn, "qwencode")
            conn.close()
            if entries:
                s.provider = entries[0]["provider"]
                s.model = entries[0]["model_id"]
        except Exception:
            pass
    s.priority = _infer_priority("qwencode", s.provider, s.model)
    return s


def read_goose() -> CLIState:
    s = CLIState("goose")
    path = APPDATA / "Block" / "goose" / "config" / "config.yaml"
    if not path.exists():
        from db import get_db, get_cli_priorities
        try:
            conn = get_db()
            entries = get_cli_priorities(conn, "goose")
            conn.close()
            if entries:
                s.provider = entries[0]["provider"]
                s.model = entries[0]["model_id"]
        except Exception:
            pass
        return s
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("active_provider:"):
            s.provider = line_stripped.split(":", 1)[1].strip()
            break
    # Find model in provider block
    in_provider = False
    target_prov = s.provider
    for line in text.splitlines():
        raw = line
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if stripped.startswith("providers:"):
            continue
        if indent == 2 and stripped.startswith(target_prov + ":"):
            in_provider = True
            continue
        if indent == 2 and not stripped.startswith(target_prov + ":") and in_provider:
            in_provider = False
        if in_provider and indent == 4 and stripped.startswith("model:"):
            s.model = stripped.split(":", 1)[1].strip()
    s.priority = _infer_priority("goose", s.provider, s.model)
    return s


def read_crush(which: str) -> CLIState:
    s = CLIState(f"crush-{which}")
    cfg = _read_json(LOCAL_APPDATA / "crush" / "crush.json")
    entry = cfg.get(which, {}) if cfg else {}
    if entry:
        s.provider = entry.get("provider", "")
        s.model = entry.get("model", "")
    else:
        from db import get_db, get_cli_priorities
        try:
            conn = get_db()
            entries = get_cli_priorities(conn, f"crush-{which}")
            conn.close()
            if entries:
                s.provider = entries[0]["provider"]
                s.model = entries[0]["model_id"]
        except Exception:
            pass
    s.priority = _infer_priority(f"crush-{which}", s.provider, s.model)
    return s


# ── all readers ────────────────────────────────────────────────────────────

ALL_READERS = [
    read_qwen, read_opencode, read_cline, read_codex,
    read_gemini, read_kilo, read_kiro, read_claude,
    read_qwencode, read_goose,
    lambda: read_crush("large"),
    lambda: read_crush("small"),
]


def read_all() -> list[CLIState]:
    return [r() for r in ALL_READERS]
