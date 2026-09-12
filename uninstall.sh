#!/usr/bin/env bash
# daily-digest uninstaller
#
# Removes the MCP registrations, command/skill/prompt files and launchers.
# With --purge it also removes the venv and cache (and config with --config).
#
# Usage:
#   ./uninstall.sh                 # remove integrations (asks confirmation)
#   ./uninstall.sh --yes           # no confirmation
#   ./uninstall.sh --purge         # also remove venv + cache
#   ./uninstall.sh --purge --config
#   ./uninstall.sh --dry-run       # show what would be removed, change nothing

set -uo pipefail

VENV_DIR="${DAILY_DIGEST_VENV:-$HOME/.local/share/daily-digest/venv}"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/daily-digest-mcp"
DOCTOR="$BIN_DIR/daily-digest-doctor"
CONFIG_FILE="$HOME/.config/daily-digest/config.toml"
CACHE_DIR="$HOME/.cache/daily-digest"
OPENCODE_DIR="$HOME/.config/opencode"
CLAUDE_DIR="$HOME/.claude"
CODEX_DIR="$HOME/.codex"
VERBOO_DIR="$HOME/.verboo"

PURGE=0; REMOVE_CONFIG=0; ASSUME_YES=0; DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --config) REMOVE_CONFIG=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "Opção desconhecida: $arg"; exit 2 ;;
  esac
done

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; NC=$'\033[0m'
ok()   { printf "  %s✓%s %s\n" "$GREEN" "$NC" "$1"; }
skip() { printf "  %s-%s %s\n" "$YELLOW" "$NC" "$1"; }
run()  { if [ "$DRY_RUN" = 1 ]; then skip "dry-run: $*"; else "$@"; fi; }

PYTHON="$(command -v python3 || command -v python || true)"

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  read -r -p "$1 [y/N] " reply
  [[ "$reply" =~ ^[yY]$ ]]
}

printf "%sdaily-digest uninstall%s%s\n" "$BOLD" "$NC" "$([ "$DRY_RUN" = 1 ] && echo ' (dry-run)')"

# opencode: remove MCP entries
if [ -f "$OPENCODE_DIR/opencode.json" ] && [ -n "$PYTHON" ]; then
  RESULT=$("$PYTHON" - "$OPENCODE_DIR/opencode.json" "$DRY_RUN" <<'EOF'
import json, sys
path, dry = sys.argv[1], sys.argv[2] == "1"
with open(path) as fh:
    data = json.load(fh)
mcp = data.get("mcp", {})
removed = [k for k in list(mcp) if k == "daily_digest" or k.startswith("daily_digest_")]
if removed and not dry:
    for k in removed:
        del mcp[k]
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
print(",".join(removed))
EOF
)
  [ -n "$RESULT" ] && ok "opencode: MCP $RESULT" || skip "opencode: nenhum MCP encontrado"
fi

# opencode: command files (resumo-*.md covers resumo-do-dia.md too)
for f in "$OPENCODE_DIR"/commands/resumo-*.md; do
  [ -f "$f" ] && run rm -f "$f" && ok "opencode: $(basename "$f")"
done

# [CC]: skills
for d in "$CLAUDE_DIR"/skills/resumo-*; do
  [ -d "$d" ] && run rm -rf "$d" && ok "[CC]: $(basename "$d")"
done
if command -v claude >/dev/null 2>&1; then
  REMOVED=""
  for name in daily_digest daily_digest_work daily_digest_personal; do
    if [ "$DRY_RUN" = 1 ]; then skip "dry-run: claude mcp remove $name"
    else claude mcp remove "$name" --scope user >/dev/null 2>&1 && REMOVED="$REMOVED $name"; fi
  done
  [ -n "$REMOVED" ] && ok "[CC]: MCP$REMOVED" || skip "[CC]: nenhum MCP encontrado"
fi

# Codex: prompts
for f in "$CODEX_DIR"/prompts/resumo-*.md; do
  [ -f "$f" ] && run rm -f "$f" && ok "Codex: $(basename "$f")"
done
# Codex: config sections
if [ -f "$CODEX_DIR/config.toml" ] && [ -n "$PYTHON" ]; then
  RESULT=$("$PYTHON" - "$CODEX_DIR/config.toml" "$DRY_RUN" <<'EOF'
import re, sys
path, dry = sys.argv[1], sys.argv[2] == "1"
with open(path) as fh:
    text = fh.read()
pattern = re.compile(r"\n?\[mcp_servers\.daily_digest[^\]]*\][^\[]*", re.S)
new, n = pattern.subn("\n", text)
if n and not dry:
    with open(path, "w") as fh:
        fh.write(new.rstrip() + "\n")
print(n)
EOF
)
  [ "$RESULT" != "0" ] && ok "Codex: $RESULT seção(ões)" || skip "Codex: nenhuma seção encontrada"
fi

# Verboo Code: skills, commands and MCP
for d in "$VERBOO_DIR"/skills/resumo-*; do
  [ -d "$d" ] && run rm -rf "$d" && ok "Verboo Code: $(basename "$d")"
done
for f in "$VERBOO_DIR"/commands/resumo-*.md; do
  [ -f "$f" ] && run rm -f "$f" && ok "Verboo Code: $(basename "$f")"
done
if command -v verboo >/dev/null 2>&1; then
  REMOVED=""
  for name in daily_digest daily_digest_work daily_digest_personal; do
    if [ "$DRY_RUN" = 1 ]; then skip "dry-run: verboo mcp remove $name"
    else verboo mcp remove "$name" --scope user >/dev/null 2>&1 && REMOVED="$REMOVED $name"; fi
  done
  [ -n "$REMOVED" ] && ok "Verboo Code: MCP$REMOVED" || skip "Verboo Code: nenhum MCP encontrado"
fi

# Launchers
for f in "$LAUNCHER" "$DOCTOR"; do
  [ -f "$f" ] && run rm -f "$f" && ok "removido $f"
done

if [ "$PURGE" = 1 ]; then
  if confirm "Remover venv ($VENV_DIR) e cache ($CACHE_DIR)?"; then
    [ -d "$VENV_DIR" ] && run rm -rf "$VENV_DIR" && ok "venv removido"
    [ -d "$CACHE_DIR" ] && run rm -rf "$CACHE_DIR" && ok "cache removido"
  fi
  if [ "$REMOVE_CONFIG" = 1 ] && [ -f "$CONFIG_FILE" ]; then
    if confirm "Remover config ($CONFIG_FILE)?"; then
      run rm -f "$CONFIG_FILE"; ok "config removido"
    fi
  fi
fi

printf "\n%sConcluído.%s Arquivos de digest em ~/daily não foram tocados.\n" "$BOLD" "$NC"
