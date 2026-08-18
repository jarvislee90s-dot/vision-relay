#!/usr/bin/env bash
# Standalone proxy harness wiring extracted from Qwen-MM-Plugins install.sh (proxy block).
# source this file (e.g. . ./install-proxy.sh) to get proxy functions.
# No dependency on the host repo installer.

PROXY_BIND_PORT="${QWEN_MM_PROXY_BIND_PORT:-8787}"

proxy_check_conflicts() {
    # $1 = codex config path；base_url 指向非本代理 → 告警并返回 1
    local codex_cfg="$1" base_url
    if [[ -f "$codex_cfg" ]]; then
        base_url="$(sed -n 's/.*base_url[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$codex_cfg" 2>/dev/null | head -1)"
        if [[ -n "$base_url" && "$base_url" != "http://127.0.0.1:${PROXY_BIND_PORT}"* ]]; then
            echo "⚠ base_url 指向 ${base_url}（非本代理），需保证本代理为第一跳（见 spec §4.4）" >&2
            return 1
        fi
    fi
    return 0
}

proxy_rewrite_codex() {
    local codex_cfg="${CODEX_HOME:-$HOME/.codex}/config.toml"
    [[ -f "$codex_cfg" ]] || return 0
    proxy_check_conflicts "$codex_cfg" || return 1
    cp "$codex_cfg" "${codex_cfg}.qwen-mm-proxy.bak" 2>/dev/null || true
    # 覆盖 model_providers 下所有 base_url 为本地代理；sed + tmp+mv 避免 GNU -i 依赖
    sed 's|base_url[[:space:]]*=[[:space:]]*"[^"]*"|base_url = "http://127.0.0.1:'"${PROXY_BIND_PORT}"'"|g' \
        "$codex_cfg" > "${codex_cfg}.tmp" && mv "${codex_cfg}.tmp" "$codex_cfg"
    echo "codex base_url -> http://127.0.0.1:${PROXY_BIND_PORT}（备份 ${codex_cfg}.qwen-mm-proxy.bak）"
}

proxy_rewrite_cc() {
    # Claude Code：ANTHROPIC_BASE_URL 写入 settings.json（备份后合并）
    local settings="$HOME/.claude/settings.json"
    [[ -f "$settings" ]] || return 0
    cp "$settings" "${settings}.qwen-mm-proxy.bak" 2>/dev/null || true
    # JSON 合并 env.ANTHROPIC_BASE_URL —— 保留已有 key，用 Python 做可靠 merge
    local _py; for _py in python3 python; do command -v "$_py" >/dev/null 2>&1 && break; done
    "$_py" -c "
import json, sys
p = sys.argv[1]
with open(p) as f: d = json.load(f)
d.setdefault('env', {})['ANTHROPIC_BASE_URL'] = sys.argv[2]
with open(p, 'w') as f: json.dump(d, f, indent=2); f.write('\n')
" "$settings" "http://127.0.0.1:${PROXY_BIND_PORT}"
    echo "claude code ANTHROPIC_BASE_URL -> http://127.0.0.1:${PROXY_BIND_PORT}"
}

proxy_rewrite_qwen_code() {
    # Qwen Code / DashScope 兼容：DASHSCOPE_BASE_URL 写入 env 文件
    local proxy_url="http://127.0.0.1:${PROXY_BIND_PORT}"
    local env_file="$HOME/.qwen-code/.env"
    if [[ -f "$env_file" ]]; then
        cp "$env_file" "${env_file}.qwen-mm-proxy.bak" 2>/dev/null || true
        # 替换已有值或追加
        if grep -q "^DASHSCOPE_BASE_URL=" "$env_file" 2>/dev/null; then
            sed "s|^DASHSCOPE_BASE_URL=.*|DASHSCOPE_BASE_URL=${proxy_url}|"                 "$env_file" > "${env_file}.tmp" && mv "${env_file}.tmp" "$env_file"
        else
            printf 'DASHSCOPE_BASE_URL=%s\n' "$proxy_url" >> "$env_file"
        fi
    else
        mkdir -p "$(dirname "$env_file")"
        printf 'DASHSCOPE_BASE_URL=%s\n' "$proxy_url" > "$env_file"
    fi
    echo "qwen code DASHSCOPE_BASE_URL -> ${proxy_url}"
}

proxy_backup_base_urls() {
    # 备份三处 harness 配置（供 proxy_restore_base_urls 回滚）
    local f
    for f in "$HOME/.codex/config.toml" "$HOME/.claude/settings.json"              "$HOME/.qwen-code/.env"; do
        [[ -f "$f" ]] && cp "$f" "${f}.qwen-mm-proxy.bak" 2>/dev/null || true
    done
    echo "proxy base_url 已备份"
}

proxy_restore_base_urls() {
    # 恢复三处备份（*.qwen-mm-proxy.bak）
    local f
    for f in "$HOME/.codex/config.toml.qwen-mm-proxy.bak"              "$HOME/.claude/settings.json.qwen-mm-proxy.bak"              "$HOME/.qwen-code/.env.qwen-mm-proxy.bak"; do
        if [[ -f "$f" ]]; then cp "$f" "${f%.qwen-mm-proxy.bak}" && rm "$f"; fi
    done
    echo "proxy base_url 已回滚"
}

