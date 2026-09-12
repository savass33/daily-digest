#!/usr/bin/env bash
# daily-digest installer
#
# Preflight (hard-fail): Python >=3.11, venv, an agent present, opencode DB sane,
# output dir writable, and a working `pip install`.
# Everything else is a warning and installation continues.
#
# The package is installed into a dedicated venv, so the launcher does not
# depend on this repository's path. Idempotent: re-running updates the launcher
# and config without duplicating agent entries. Existing command/skill files
# are backed up before being overwritten.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${DAILY_DIGEST_VENV:-$HOME/.local/share/daily-digest/venv}"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/daily-digest-mcp"
DOCTOR="$BIN_DIR/daily-digest-doctor"
CONFIG_DIR="$HOME/.config/daily-digest"
CONFIG_FILE="$CONFIG_DIR/config.toml"
PROFILE_ARGS=()

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
FAILURES=0
WARNINGS=0

ok()   { printf "  %s✓%s %s\n" "$GREEN" "$NC" "$1"; }
warn() { printf "  %s!%s %s\n" "$YELLOW" "$NC" "$1"; WARNINGS=$((WARNINGS+1)); }
bad()  { printf "  %s✗%s %s\n" "$RED" "$NC" "$1"; FAILURES=$((FAILURES+1)); }
info() { printf "  %s•%s %s\n" "$BLUE" "$NC" "$1"; }
title(){ printf "\n%s%s%s\n" "$BOLD" "$1" "$NC"; }

backup() {
  if [ -f "$1" ]; then cp "$1" "$1.bak.$(date +%s)"; fi
}

title "daily-digest — preflight"

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

OPENCODE_DIR="$HOME/.config/opencode"
CLAUDE_DIR="$HOME/.claude"
CODEX_DIR="$HOME/.codex"
VERBOO_DIR="$HOME/.verboo"

# Claude Code is detected by its CLI or its main config file. The ~/.claude
# directory alone is NOT enough: Verboo Code creates/uses it as a legacy store.
HAVE_OPENCODE=0; HAVE_CLAUDE=0; HAVE_CODEX=0; HAVE_VERBOO=0
[ -d "$OPENCODE_DIR" ] && HAVE_OPENCODE=1
if command -v claude >/dev/null 2>&1 || [ -f "$HOME/.claude.json" ]; then
  HAVE_CLAUDE=1
fi
[ -d "$CODEX_DIR" ] && HAVE_CODEX=1
[ -d "$VERBOO_DIR" ] && HAVE_VERBOO=1
if [ "$HAVE_OPENCODE" = 0 ] && [ "$HAVE_CLAUDE" = 0 ] && [ "$HAVE_CODEX" = 0 ] && [ "$HAVE_VERBOO" = 0 ]; then
  bad "nenhum agente suportado encontrado (opencode / [CC] / Codex / Verboo Code)"
else
  [ "$HAVE_OPENCODE" = 1 ] && ok "opencode detectado"
  [ "$HAVE_CLAUDE" = 1 ] && ok "[CC] detectado" || info "Claude Code não detectado (sem CLI/~/.claude.json)"
  [ "$HAVE_CODEX" = 1 ] && ok "Codex detectado"
  [ "$HAVE_VERBOO" = 1 ] && ok "Verboo Code detectado"
fi

OPENCODE_DB="$HOME/.local/share/opencode/opencode.db"
if [ -f "$OPENCODE_DB" ] && [ -n "$PYTHON" ]; then
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
    MISSING:*) warn "opencode.db sem tabelas: ${DB_CHECK#MISSING:}" ;;
    *) warn "opencode.db não pôde ser validado: ${DB_CHECK#ERR:}" ;;
  esac
else
  warn "opencode.db não encontrado (adapter opencode inativo)"
fi

if [ -n "$PYTHON" ]; then
  if "$PYTHON" -c "import urllib.request; urllib.request.urlopen('https://pypi.org/simple/', timeout=8)" >/dev/null 2>&1; then
    ok "PyPI acessível (para instalar o pacote)"
  else
    bad "PyPI inacessível — não será possível instalar o pacote"
  fi
fi

if [ -w "$HOME" ]; then ok "home gravável"; else bad "home não gravável"; fi

command -v git >/dev/null 2>&1 && ok "git disponível" || warn "git ausente — coleta de commits desativada"
command -v sqlite3 >/dev/null 2>&1 && info "sqlite3 CLI presente" || info "sqlite3 CLI ausente (opcional)"
command -v jq >/dev/null 2>&1 && info "jq presente" || info "jq ausente (opcional)"
if command -v git >/dev/null 2>&1; then
  AUTHOR="$(git config --global user.email 2>/dev/null || true)"
  [ -n "$AUTHOR" ] && info "git author: $AUTHOR" || warn "git user.email vazio — commits serão coletados sem atribuição"
fi

if [ "$FAILURES" -gt 0 ]; then
  title "Preflight falhou ($FAILURES problema(s) essencial(is)). Abortando."
  exit 1
fi

# =========================================================================
title "Instalando runtime (venv + pacote)"
"$PYTHON" -m venv "$VENV_DIR" || { bad "falha ao criar venv"; exit 1; }
ok "venv em $VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if "$VENV_DIR/bin/python" -m pip install --quiet --upgrade "$REPO_DIR" >/dev/null 2>&1; then
  ok "pacote daily-digest instalado no venv"
else
  bad "falha ao instalar o pacote no venv"; exit 1
fi

title "Criando launchers"
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" -m daily_digest.mcp_server "\$@"
EOF
cat > "$DOCTOR" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" -m daily_digest.doctor "\$@"
EOF
chmod +x "$LAUNCHER" "$DOCTOR"
ok "launcher em $LAUNCHER"
ok "doctor em $DOCTOR"

title "Configuração"
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_FILE" ]; then
  ok "config existente mantida em $CONFIG_FILE"
else
  cp "$REPO_DIR/config.example.toml" "$CONFIG_FILE"
  ok "config criada em $CONFIG_FILE"
fi

# Discover configured workspaces (for per-profile registration).
WORKSPACES=""
if [ -n "$PYTHON" ]; then
  WORKSPACES=$("$PYTHON" - "$CONFIG_FILE" <<'EOF'
import sys, tomllib
try:
    with open(sys.argv[1], "rb") as fh:
        data = tomllib.load(fh)
    print(" ".join((data.get("workspaces") or {}).keys()))
except Exception:
    print("")
EOF
)
fi
if [ -n "$WORKSPACES" ]; then
  info "workspaces configurados: $WORKSPACES"
fi

title "Registrando o MCP nos agentes"

# --- opencode --------------------------------------------------------------
if [ "$HAVE_OPENCODE" = 1 ]; then
  OPENCODE_CFG="$OPENCODE_DIR/opencode.json"
  if [ ! -f "$OPENCODE_CFG" ] && [ -f "$OPENCODE_DIR/opencode.jsonc" ]; then
    warn "opencode usa opencode.jsonc; não edito JSONC automaticamente. Adicione o MCP manualmente (ver README)"
  elif [ -f "$OPENCODE_CFG" ]; then
    backup "$OPENCODE_CFG"
    RESULT=$("$PYTHON" - "$OPENCODE_CFG" "$LAUNCHER" <<'EOF'
import json, sys
path, launcher = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        data = json.load(fh)
except Exception as exc:
    print("ERR:" + str(exc)); sys.exit(0)
mcp = data.setdefault("mcp", {})
# Only the permissive server is registered. Workspaces are selected per call
# (resolve_scope / workspace argument), so hard-locked *_work/_personal servers
# are intentionally not created here (see README to add them manually).
for stale in [k for k in list(mcp) if k.startswith("daily_digest_")]:
    del mcp[stale]
mcp["daily_digest"] = {"type": "local", "command": [launcher], "enabled": True}
with open(path, "w") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print("OK")
EOF
)
    case "$RESULT" in
      OK) ok "opencode: MCP daily_digest registrado" ;;
      *) warn "opencode: não foi possível editar opencode.json: $RESULT" ;;
    esac
  fi

  CMD_DIR="$OPENCODE_DIR/commands"
  mkdir -p "$CMD_DIR"
  backup "$CMD_DIR/resumo-do-dia.md"
  cp "$REPO_DIR/integrations/opencode/resumo-do-dia.md" "$CMD_DIR/resumo-do-dia.md"
  ok "opencode: comando /resumo-do-dia instalado"
fi

# --- [CC] ------------------------------------------------------------------
if [ "$HAVE_CLAUDE" = 1 ]; then
  mkdir -p "$CLAUDE_DIR/skills/resumo-do-dia"
  backup "$CLAUDE_DIR/skills/resumo-do-dia/SKILL.md"
  cp "$REPO_DIR/integrations/claude/resumo-do-dia/SKILL.md" "$CLAUDE_DIR/skills/resumo-do-dia/SKILL.md"
  ok "[CC]: skill /resumo-do-dia instalada"
  if command -v claude >/dev/null 2>&1; then
    claude mcp remove daily_digest --scope user >/dev/null 2>&1 || true
    if claude mcp add --scope user --transport stdio daily_digest -- "$LAUNCHER" >/dev/null 2>&1; then
      ok "[CC]: MCP daily_digest registrado"
    else
      warn "[CC]: falha ao registrar; manual: claude mcp add --scope user --transport stdio daily_digest -- $LAUNCHER"
    fi
  else
    warn "[CC]: CLI 'claude' não encontrada; registre o MCP manualmente (ver README)"
  fi
else
  # No real [CC]: remove a stale skill so Verboo Code (which reads ~/.claude as
  # a legacy store) does not show a duplicate /resumo-do-dia.
  if [ -d "$CLAUDE_DIR/skills/resumo-do-dia" ]; then
    rm -rf "$CLAUDE_DIR/skills/resumo-do-dia"
    info "[CC] ausente: removida skill legada duplicada em ~/.claude/skills"
  fi
fi

# --- Codex -----------------------------------------------------------------
if [ "$HAVE_CODEX" = 1 ]; then
  CODEX_CFG="$CODEX_DIR/config.toml"
  mkdir -p "$CODEX_DIR/prompts"
  backup "$CODEX_DIR/prompts/resumo-do-dia.md"
  cp "$REPO_DIR/integrations/codex/resumo-do-dia.md" "$CODEX_DIR/prompts/resumo-do-dia.md"
  ok "Codex: prompt /resumo-do-dia instalado"
  if [ ! -f "$CODEX_CFG" ] || ! grep -q "mcp_servers.daily_digest" "$CODEX_CFG" 2>/dev/null; then
    [ -f "$CODEX_CFG" ] && backup "$CODEX_CFG"
    {
      printf "\n[mcp_servers.daily_digest]\ncommand = \"%s\"\n" "$LAUNCHER"
    } >> "$CODEX_CFG"
    # Validate the TOML we just wrote; restore the backup on corruption.
    if [ -n "$PYTHON" ] && ! "$PYTHON" -c "import tomllib,sys; tomllib.load(open(sys.argv[1],'rb'))" "$CODEX_CFG" 2>/dev/null; then
      warn "Codex: config.toml inválido após edição; restaurando backup"
      LATEST_BAK="$(ls -t "$CODEX_CFG".bak.* 2>/dev/null | head -1)"
      [ -n "$LATEST_BAK" ] && cp "$LATEST_BAK" "$CODEX_CFG"
    else
      ok "Codex: MCP daily_digest registrado (toml válido)"
    fi
  else
    ok "Codex: MCP daily_digest já registrado"
  fi
fi

# --- Verboo Code -----------------------------------------------------------
# Verboo Code is a [CC]-style agent. Its user-skills dir is ~/.verboo/skills,
# but it ALSO reads ~/.claude/skills as a legacy store. To avoid the slash menu
# showing /resumo-do-dia more than once:
#   - if real [CC] is installed, rely on ~/.claude/skills (shared) and do not
#     create the native copy;
#   - otherwise install the native copy only.
# Commands are not installed (the skill is enough) to prevent a third entry.
if [ "$HAVE_VERBOO" = 1 ]; then
  rm -f "$VERBOO_DIR/commands/resumo-do-dia.md"
  if [ "$HAVE_CLAUDE" = 1 ]; then
    rm -rf "$VERBOO_DIR/skills/resumo-do-dia"
    info "Verboo Code: usando a skill de ~/.claude/skills (evita duplicata)"
  else
    mkdir -p "$VERBOO_DIR/skills/resumo-do-dia"
    backup "$VERBOO_DIR/skills/resumo-do-dia/SKILL.md"
    cp "$REPO_DIR/integrations/claude/resumo-do-dia/SKILL.md" "$VERBOO_DIR/skills/resumo-do-dia/SKILL.md"
    ok "Verboo Code: skill /resumo-do-dia instalada"
  fi
  if command -v verboo >/dev/null 2>&1; then
    verboo mcp remove daily_digest --scope user >/dev/null 2>&1 || true
    if verboo mcp add --scope user daily_digest -- "$LAUNCHER" >/dev/null 2>&1; then
      ok "Verboo Code: MCP daily_digest registrado"
    else
      warn "Verboo Code: falha ao registrar; manual: verboo mcp add --scope user daily_digest -- $LAUNCHER"
    fi
  else
    warn "Verboo Code: CLI 'verboo' não encontrada; registre o MCP manualmente (ver README)"
  fi
fi

title "Verificação do servidor MCP"
HANDSHAKE="$( { printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"install","version":"0"}}}'; sleep 3; } \
  | "$LAUNCHER" 2>/dev/null )"
if printf '%s' "$HANDSHAKE" | grep -q '"serverInfo"'; then
  ok "servidor MCP iniciou e respondeu ao handshake"
else
  warn "não foi possível confirmar o handshake do MCP (rode: daily-digest-doctor)"
fi

title "Concluído"
printf "  Agentes instalados:"
[ "$HAVE_OPENCODE" = 1 ] && printf " opencode"
[ "$HAVE_CLAUDE" = 1 ] && printf " [CC]"
[ "$HAVE_CODEX" = 1 ] && printf " codex"
[ "$HAVE_VERBOO" = 1 ] && printf " verboo"
printf "\n"
[ -n "$WORKSPACES" ] && printf "  Workspaces no config: %s\n" "$WORKSPACES"
printf "  Avisos: %s\n" "$WARNINGS"
printf "\n  Abra seu agente e digite: %s/resumo-do-dia%s\n" "$BOLD" "$NC"
printf "  Diagnóstico: %s\n" "$DOCTOR"
printf "  Config: %s\n\n" "$CONFIG_FILE"
