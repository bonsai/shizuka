"""state_writer.py — 各 CLI の設定を書き換え (Apply)"""
import json
import os
import re
import subprocess
from pathlib import Path

HOME = Path.home()
MEGA = Path(os.environ.get("MEGA", HOME / "Documents" / "MEGA"))
LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
GROK_CONFIG = HOME / ".grok" / "config.toml"


# ── API key resolution ─────────────────────────────────────────────────────

def _sakura_api_key() -> str:
    """Read Sakura API key from opencode.jsonc (source of truth)."""
    path = HOME / ".config" / "opencode" / "opencode.jsonc"
    try:
        raw = path.read_text(encoding="utf-8")
        import re as _re
        m = _re.search(r'"apiKey"\s*:\s*"([^"]+)"', raw)
        if m:
            return m.group(1)
    except Exception:
        pass
    return os.environ.get("SAKURA_API_KEY", "")


def _toml_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _replace_toml_section(text: str, header: str, body_lines: list[str]) -> str:
    block = header + "\n" + "\n".join(body_lines).rstrip() + "\n"
    pattern = re.compile(rf"(?ms)^{re.escape(header)}\n.*?(?=^\[|\Z)")
    if pattern.search(text):
        return pattern.sub(block, text, count=1).rstrip() + "\n"
    if not text.strip():
        return block
    return text.rstrip() + "\n\n" + block


def _replace_models_default(text: str, default_name: str) -> str:
    pattern = re.compile(r"(?ms)^\[models\]\n.*?(?=^\[|\Z)")
    match = pattern.search(text)
    if match:
        lines = match.group(0).rstrip("\n").splitlines()
        updated = False
        for i, line in enumerate(lines[1:], start=1):
            if re.match(r"^\s*default\s*=", line):
                lines[i] = f'default = "{default_name}"'
                updated = True
                break
        if not updated:
            lines.insert(1, f'default = "{default_name}"')
        block = "\n".join(lines) + "\n"
        return pattern.sub(block, text, count=1).rstrip() + "\n"
    block = "[models]\n" + f'default = "{default_name}"\n'
    if not text.strip():
        return block
    return text.rstrip() + "\n\n" + block


# ── Qwen ───────────────────────────────────────────────────────────────────

def _apply_qwen(provider: str, model: str):
    path = HOME / ".qwen" / "settings.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))

    base_urls = {
        "sakura": ("https://api.ai.sakura.ad.jp/v1", "SAKURA_API_KEY"),
        "dashscope": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
        "aihubmix": ("https://aihubmix.com/v1", "AIHUBMIX_API_KEY"),
        "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    }
    base_url, env_key = base_urls.get(provider, ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"))

    providers = cfg.get("modelProviders", {}).get("openai", [])
    found = any(p.get("id") == model for p in providers)
    if not found:
        name_prefix = {"sakura": "[Sakura]", "anthropic": "[Anthropic]"}.get(provider, "[Free Quota]")
        providers.append({
            "id": model,
            "name": f"{name_prefix} {model}",
            "baseUrl": base_url,
            "envKey": env_key,
        })
        cfg["modelProviders"]["openai"] = providers

    cfg["model"] = {"name": model}
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Grok ──────────────────────────────────────────────────────────────────

def _apply_grok(provider: str, model: str):
    section_map = {
        "sakura": ("sakura2", "https://api.ai.sakura.ad.jp/v1", "SAKURA_AI_TOKEN"),
        "dashscope": ("dashscope", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
        "openrouter": ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    }
    if provider not in section_map:
        raise ValueError(f"unsupported Grok provider: {provider}")

    section_name, base_url, env_key = section_map[provider]
    path = GROK_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    text = _replace_toml_section(text, f"[model.{section_name}]", [
        f'model = "{_toml_quote(model)}"',
        f'base_url = "{base_url}"',
        f'env_key = "{env_key}"',
        'api_backend = "chat_completions"',
    ])
    text = _replace_models_default(text, section_name)
    path.write_text(text, encoding="utf-8")


# ── OpenCode ───────────────────────────────────────────────────────────────

def _apply_opencode(provider: str, model: str):
    path = HOME / ".config" / "opencode" / "opencode.jsonc"
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider}/{model}",
        "provider": {provider: {}},
    }
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── Cline ──────────────────────────────────────────────────────────────────

def _apply_cline(provider: str, model: str):
    cline_prov = provider
    extra = []
    if provider in ("openai-native", "sakura"):
        cline_prov = "openai-native"
        key = _sakura_api_key()
        extra = ["--baseurl", "https://api.ai.sakura.ad.jp/v1", "--apikey", key]
    elif provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY", "")
        extra = ["--apikey", key]
    subprocess.run(["cline", "auth", "--provider", cline_prov, "--modelid", model] + extra)


# ── Codex ──────────────────────────────────────────────────────────────────

def _apply_codex(model: str):
    path = MEGA / ".codex-local" / "sakura-ai.env"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines(keepends=True)
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("OPENAI_MODEL="):
            lines[i] = f"OPENAI_MODEL={model}\n"
            replaced = True
            break
    if not replaced:
        lines.append(f"OPENAI_MODEL={model}\n")
    path.write_text("".join(lines), encoding="utf-8")


# ── Gemini ─────────────────────────────────────────────────────────────────

def _apply_gemini(model: str):
    path = HOME / ".gemini" / "settings.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["model"] = model
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Kilo ───────────────────────────────────────────────────────────────────

def _apply_kilo(provider: str, model: str):
    path = HOME / ".kilo" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provider": provider, "model": model}, indent=2), encoding="utf-8")


# ── Kiro ───────────────────────────────────────────────────────────────────

def _apply_kiro(provider: str, model: str):
    path = MEGA / ".kiro" / "settings.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["provider"] = provider
    cfg["model"] = model
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Claude ─────────────────────────────────────────────────────────────────

def _apply_claude(provider: str, model: str):
    path = HOME / ".claude" / "settings.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    env = cfg.get("env", {})

    if provider == "sakura":
        key = _sakura_api_key()
        env.update({
            "ANTHROPIC_BASE_URL": "https://api.ai.sakura.ad.jp",
            "ANTHROPIC_AUTH_TOKEN": key,
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": "gpt-oss-120b",
        })
        cfg["env"] = env
        cfg.pop("model", None)
    else:  # anthropic
        for k in ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
                   "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL"]:
            env.pop(k, None)
        if env:
            cfg["env"] = env
        else:
            cfg.pop("env", None)
        cfg["model"] = model

    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ── QwenCode ───────────────────────────────────────────────────────────────

def _apply_qwencode(provider: str, model: str):
    path = HOME / ".qwencode" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provider": provider, "model": model}, indent=2), encoding="utf-8")


# ── Goose ──────────────────────────────────────────────────────────────────

def _apply_goose(provider: str, model: str):
    path = APPDATA / "Block" / "goose" / "config" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    base_url = "https://api.deepseek.com/v1" if model.startswith("deepseek") else ""
    prov_block = f"    enabled: true\n    model: {model}\n    configured: true"
    if base_url:
        prov_block += f"\n    base_url: {base_url}"
    yaml = f"active_provider: {provider}\nproviders:\n  {provider}:\n{prov_block}\n"
    path.write_text(yaml, encoding="utf-8")


# ── Crush ──────────────────────────────────────────────────────────────────

def _apply_crush(which: str, provider: str, model: str):
    path = LOCAL_APPDATA / "crush" / "crush.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {"large": {}, "small": {}}
    if which not in cfg:
        cfg[which] = {}
    cfg[which]["provider"] = provider
    cfg[which]["model"] = model
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ── dispatch ───────────────────────────────────────────────────────────────

def apply(cli: str, provider: str, model: str):
    dispatch = {
        "qwen": _apply_qwen,
        "grok": _apply_grok,
        "opencode": _apply_opencode,
        "cline": _apply_cline,
        "codex": _apply_codex,
        "gemini": _apply_gemini,
        "kilo": _apply_kilo,
        "kiro": _apply_kiro,
        "claude": _apply_claude,
        "qwencode": _apply_qwencode,
        "goose": _apply_goose,
        "crush-large": lambda p, m: _apply_crush("large", p, m),
        "crush-small": lambda p, m: _apply_crush("small", p, m),
    }
    fn = dispatch.get(cli)
    if not fn:
        raise ValueError(f"unknown CLI: {cli}")
    fn(provider, model)
