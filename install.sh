#!/usr/bin/env bash
# daily-digest installer
#
# Preflight (hard-fail): Python >=3.11, venv, agent present, opencode DB sane,
# output dir writable, and a working `pip install mcp`.
# Everything else is a warning and installation continues.
#
# Idempotent: re-running updates the launcher and config without duplicating
# agent entries. Existing agent configs are backed up before edits.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${DAILY_DIGEST_VENV:-$HOME/.local/share/daily-digest/venv}"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/daily-digest-mcp"
CONFIG_DIR="$HOME/.config/daily-digest"
CONFIG_FILE="$CONFIG_DIR/config.toml"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
FAILURES=0
WARNINGS=0

ok()   { printf "  %s✓%s %s\n" "$GREEN" "$NC" "$1"; }
warn() { printf "  %s!%s %s\n" "$YELLOW" "$NC" "$1"; WARNINGS=$((WARNINGS+1)); }
bad()  { printf "  %s✗%s %s\n" "$RED" "$NC" "$1"; FAILURES=$((FAILURES+1)); }
info() { printf "  %s•%s %s\n" "$BLUE" "$NC" "$1"; }
title(){ printf "\n%s%s%s\n" "$BOLD" "$1" "$NC"; }

title "daily-digest — preflight"

# --- Python ---------------------------------------------------------------
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
done
if [ -z "$PYTHON" ]; then
  bad "Python 3.11+ não encontrado (python3/python)"
else
  PY_OK=$("$PYTHON" - <<'EOF'
import sys
print("1" if sys.version_info >= (3, 11) else "0")
EOF
)
  if [ "$PY_OK" = "1" ]; then
    ok "Python $("$PYTHON" --version 2>&1 | awk '{print $2}')"
  else
    bad "Python < 3.11 ($("$PYTHON" --version 2>&1)) — necessário tomllib"
  fi
fi

if [ -n "$PYTHON" ]; then
  if "$PYTHON" -c "import sqlite3, urllib.request, json, tomllib, argparse, pathlib, datetime, re, subprocess" 2>/dev/null; then
    ok "stdlib obrigatória disponível"
  else
    bad "stdlib incompleta (sqlite3/urllib/tomllib/...)"
  fi
  if "$PYTHON" -m venv --help >/dev/null 2>&1; then
    ok "módulo venv disponível"
  else
    bad "módulo venv ausente — instale python3-venv"
  fi
fi

# --- Agentes presentes ----------------------------------------------------
OPENCODE_CFG="$HOME/.config/opencode/opencode.json"
CLAUDE_DIR="$HOME/.claude"
CODEX_DIR="$HOME/.codex"
HAVE_OPENCODE=0; HAVE_CLAUDE=0; HAVE_CODEX=0
[ -e "$HOME/.config/opencode" ] && HAVE_OPENCODE=1
[ -d "$CLAUDE_DIR" ] && HAVE_CLAUDE=1
[ -d "$CODEX_DIR" ] && HAVE_CODEX=1

if [ "$HAVE_OPENCODE" = 0 ] && [ "$HAVE_CLAUDE" = 0 ] && [ "$HAVE_CODEX" = 0 ]; then
  bad "nenhum agente suportado encontrado (opencode / [CC] / Codex)"
else
  [ "$HAVE_OPENCODE" = 1 ] && ok "opencode detectado"
  [ "$HAVE_CLAUDE" = 1 ] && ok "[CC] detectado"
  [ "$HAVE_CODEX" = 1 ] && ok "Codex detectado"
fi

# --- Store do opencode (integridade) -------------------------------------
OPENCODE_DB="$HOME/.local/share/opencode/opencode.db"
if [ -f "$OPENCODE_DB" ]; then
  if [ -n "$PYTHON" ]; then
    DB_CHECK=$("$PYTHON" - "$OPENCODE_DB" <<'EOF'
import sqlite3, sys
path = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in ("session", "message", "part") if t not in tables]
    if missing:
        print("MISSING:" + ",".join(missing)); sys.exit(0)
    conn.execute("SELECT count(*) FROM session").fetchone()
    print("OK")
except Exception as exc:
    print("ERR:" + str(exc))
EOF
)
    case "$DB_CHECK" in
      OK) ok "opencode.db legível (WAL, read-only) e com schema esperado" ;;
      MISSING:*) bad "opencode.db sem tabelas: ${DB_CHECK#MISSING:}" ;;
      *) warn "opencode.db não pôde ser validado: ${DB_CHECK#ERR:}" ;;
    esac
  fi
else
  warn "opencode.db não encontrado (adapter opencode inativo)"
fi

# --- Rede / pip -----------------------------------------------------------
if [ -n "$PYTHON" ]; then
  if "$PYTHON" -c "import urllib.request; urllib.request.urlopen('https://pypi.org/simple/mcp/', timeout=8)" >/dev/null 2>&1; then
    ok "PyPI acessível (para instalar o SDK mcp)"
  else
    bad "PyPI inacessível — não será possível instalar o SDK mcp"
  fi
fi

# --- Saída ---------------------------------------------------------------
if [ -w "$HOME" ]; then
  ok "home gravável (mkdir ~/daily quando necessário)"
else
  bad "home não gravável"
fi

# --- Avisos não bloqueantes ---------------------------------------------
command -v git >/dev/null 2>&1 && ok "git disponível" || warn "git ausente — coleta de commits desativada"
command -v sqlite3 >/dev/null 2>&1 && info "sqlite3 CLI presente" || warn "sqlite3 CLI ausente (não é necessário; Python basta)"
command -v jq >/dev/null 2>&1 && info "jq presente" || warn "jq ausente (opcional)"
if command -v git >/dev/null 2>&1; then
  AUTHOR="$(git config --global user.email 2>/dev/null || true)"
  [ -n "$AUTHOR" ] && info "git author: $AUTHOR" || warn "git user.email vazio — commits serão coletados por janela de tempo"
fi

if [ "$FAILURES" -gt 0 ]; then
  title "Preflight falhou ($FAILURES problema(s) essencial(is)). Abortando."
  exit 1
fi

# =========================================================================
title "Instalando runtime (venv + SDK mcp)"
"$PYTHON" -m venv "$VENV_DIR" || { bad "falha ao criar venv"; exit 1; }
ok "venv em $VENV_DIR"

"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if "$VENV_DIR/bin/python" -m pip install --quiet --upgrade mcp >/dev/null 2>&1; then
  ok "SDK mcp instalado ($("$VENV_DIR/bin/python" -c 'import mcp; print(getattr(mcp,"__version__","?"))' 2>/dev/null))"
else
  bad "falha ao instalar o SDK mcp"; exit 1
fi

title "Criando launcher"
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$REPO_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV_DIR/bin/python" -m daily_digest.mcp_server "\$@"
EOF
chmod +x "$LAUNCHER"
ok "launcher em $LAUNCHER"

title "Configuração"
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_FILE" ]; then
  ok "config existente mantida em $CONFIG_FILE"
else
  cp "$REPO_DIR/config.example.toml" "$CONFIG_FILE"
  ok "config criada em $CONFIG_FILE"
fi

title "Registrando o MCP nos agentes"

# opencode
if [ "$HAVE_OPENCODE" = 1 ] && [ -f "$OPENCODE_CFG" ]; then
  cp "$OPENCODE_CFG" "$OPENCODE_CFG.bak"
  OPENCODE_RESULT=$("$PYTHON" - "$OPENCODE_CFG" "$LAUNCHER" <<'EOF'
import json, sys
path, launcher = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        data = json.load(fh)
except Exception as exc:
    print("ERR:" + str(exc)); sys.exit(0)
data.setdefault("mcp", {})
data["mcp"]["daily_digest"] = {
    "type": "local",
    "command": [launcher],
    "enabled": True,
}
with open(path, "w") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print("OK")
EOF
)
  case "$OPENCODE_RESULT" in
    OK) ok "opencode: mcp.daily_digest registrado (backup .bak)" ;;
    *) warn "opencode: não foi possível editar opencode.json: $OPENCODE_RESULT" ;;
  esac
  mkdir -p "$HOME/.config/opencode/commands"
  cp "$REPO_DIR/integrations/opencode/resumo-do-dia.md" "$HOME/.config/opencode/commands/resumo-do-dia.md"
  ok "opencode: comando /resumo-do-dia instalado"
fi

# [CC]
if [ "$HAVE_CLAUDE" = 1 ]; then
  mkdir -p "$CLAUDE_DIR/skills/resumo-do-dia"
  cp "$REPO_DIR/integrations/claude/resumo-do-dia/SKILL.md" "$CLAUDE_DIR/skills/resumo-do-dia/SKILL.md"
  ok "[CC]: skill /resumo-do-dia instalada"
  if command -v claude >/dev/null 2>&1; then
    claude mcp remove daily_digest --scope user >/dev/null 2>&1 || true
    if claude mcp add --scope user --transport stdio daily_digest -- "$LAUNCHER" >/dev/null 2>&1; then
      ok "[CC]: MCP daily_digest registrado"
    else
      warn "[CC]: falha ao registrar via CLI; adicione manualmente: claude mcp add --scope user --transport stdio daily_digest -- $LAUNCHER"
    fi
  else
    warn "[CC]: CLI 'claude' não encontrada; registre o MCP manualmente (ver README)"
  fi
fi

# Codex
if [ "$HAVE_CODEX" = 1 ]; then
  CODEX_CFG="$CODEX_DIR/config.toml"
  mkdir -p "$CODEX_DIR/prompts"
  cp "$REPO_DIR/integrations/codex/resumo-do-dia.md" "$CODEX_DIR/prompts/resumo-do-dia.md"
  ok "Codex: prompt /resumo-do-dia instalado"
  if [ ! -f "$CODEX_CFG" ] || ! grep -q "mcp_servers.daily_digest" "$CODEX_CFG" 2>/dev/null; then
    [ -f "$CODEX_CFG" ] && cp "$CODEX_CFG" "$CODEX_CFG.bak"
    {
      printf "\n[mcp_servers.daily_digest]\n"
      printf 'command = "%s"\n' "$LAUNCHER"
    } >> "$CODEX_CFG"
    ok "Codex: MCP daily_digest registrado (backup .bak)"
  else
    ok "Codex: MCP daily_digest já registrado"
  fi
fi

title "Verificação do servidor MCP"
HANDSHAKE="$( { printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"install","version":"0"}}}'; sleep 3; } \
  | "$LAUNCHER" 2>/dev/null )"
if printf '%s' "$HANDSHAKE" | grep -q '"serverInfo"'; then
  ok "servidor MCP iniciou e respondeu ao handshake"
else
  warn "não foi possível confirmar o handshake do MCP (rode: $LAUNCHER < handshake.json)"
fi

title "Concluído"
printf "  Agentes instalados:"
[ "$HAVE_OPENCODE" = 1 ] && printf " opencode"
[ "$HAVE_CLAUDE" = 1 ] && printf " [CC]"
[ "$HAVE_CODEX" = 1 ] && printf " codex"
printf "\n"
printf "  Avisos: %s\n" "$WARNINGS"
printf "\n  Abra seu agente e digite: %s/resumo-do-dia%s\n\n" "$BOLD" "$NC"
printf "  Config: %s\n" "$CONFIG_FILE"
printf "  Launcher: %s\n\n" "$LAUNCHER"
