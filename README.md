# daily-digest

A **local MCP server** that reads your AI coding sessions (opencode, [CC] and
Codex) on your machine, normalizes them, **removes secrets** and hands compact
data to the agent itself. At the end of the day, inside any session, you type:

```
/resumo-do-dia
```

Your agent's own model writes the summary, saves a Markdown file and — if you
configure it — sends it on WhatsApp. **No CLI, no LLM API key, no background
process.**

> Works with opencode, Verboo Code, [CC] and Codex CLI — each stores sessions in
> its own place, and the digest tags every item with its origin.

## How it works

```
opencode.db   ─┐
claude *.jsonl ─┼─▶ adapters ─▶ normalize ─▶ redact ─▶ MCP collect_digest ─▶ your agent summarizes
codex *.jsonl  ─┘                                          │
                                                           └─▶ write_digest ─▶ ~/daily/...
```

- Summarization is done by the **model of the agent where you typed the prompt**.
- The MCP only collects, redacts and writes. Secrets are removed **before** any
  content reaches the agent.
- Multiple agents/sessions running at the same time are all captured; sub-agents
  are folded into their parent so the same work is not counted twice.

### Where each agent stores sessions

| Agent | Location | Notes |
|---|---|---|
| opencode | `~/.local/share/opencode/opencode.db` | single SQLite DB (WAL) |
| [CC] | `~/.claude/projects/<enc-cwd>/<id>.jsonl` | detected by its semver `version` field |
| Verboo Code | `~/.claude/projects/...` (or `VERBOO_PROJECTS_DIR`; also `~/.openclaude/projects`) | shares the [CC] layout; detected by `version: "unknown"` / the "Verboo Code" banner, so it is tagged `[verboo]`, not `[claude]` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` (+ `archived_sessions`) | |

Verboo Code also reads `~/.claude/skills` as a *legacy* store, which is why the
installer avoids installing two copies of `/resumo-do-dia` (see below).

## Requirements

- Python **3.11+** (uses `venv` and `tomllib`)
- At least one of: opencode / [CC] / Codex / Verboo Code
- Internet **only during installation** (to install the package)
- **Linux or macOS**. Windows is not supported in this version.
- Optional: `git` (commit collection), `claude`/`verboo` CLI (MCP auto-registration)

## Installation

```bash
git clone <repo-url> daily-digest
cd daily-digest
./install.sh
```

The installer runs a preflight (Python, venv, `opencode.db` integrity, PyPI,
permissions), creates a venv, installs the package **into the venv** (so the
launcher does not depend on the repo path), registers the MCP **only on the
agents it finds**, and installs `/resumo-do-dia`. It is idempotent and backs up
(`.bak.<timestamp>`) any command/skill file before overwriting.

Run a health check any time:

```bash
daily-digest-doctor
```

## Usage

Inside any agent session:

| Command | Effect |
|---|---|
| `/resumo-do-dia` | infers the scope from the current project |
| `/resumo-do-dia me resume as coisas da verboo hoje` | natural-language scope |

Outputs (Markdown and/or WhatsApp) come from the config, not from arguments.

## Workspaces: keep work and personal apart

A digest must never mix personal projects into professional ones. Configure
workspaces in `~/.config/daily-digest/config.toml`:

```toml
[workspaces.work]
aliases = ["verboo", "verbeux"]
match_paths = ["~/Documentos/Work/**"]
match_remotes = ["github.com/your-org/*"]
output_dir = "~/daily/work"
whatsapp_target = "5511999999999"

[workspaces.personal]
aliases = ["pessoal"]
match_paths = ["~/projetos/**"]
match_remotes = ["github.com/your-user/*"]
output_dir = "~/daily/personal"
```

Classification precedence for a project:

1. a `.daily-digest.toml` marker in the repo (`context = "work"`)
2. `match_paths` globs
3. `match_remotes` globs (normalized `host/org/repo`)
4. `other` — visible only when the scope is `all`

A path that matches **more than one** workspace is treated as a conflict and is
**not** assigned to any named workspace.

### Hard isolation with profiles

By default the installer registers **only the permissive server**
(`daily_digest`): the workspace is chosen per call (scope inference or the
`workspace` argument). To lock a session to a single workspace, set the active
profile:

```toml
[general]
profile = "work"      # or "all" (default)
```

or per process with the `DAILY_DIGEST_PROFILE` environment variable (takes
precedence). To run an agent that is *physically unable* to read another
workspace, register an extra server with the env var baked in and point your
command at it:

```bash
verboo mcp add --scope user --env DAILY_DIGEST_PROFILE=work --transport stdio \
  daily_digest_work -- ~/.local/bin/daily-digest-mcp
```

> This is a **guardrail against accidental mixing**, not a security boundary
> against someone editing the config/env. Real isolation would need separate
> OS accounts.

## Outputs

```toml
[output]
markdown = true             # write ~/daily[/<workspace>]/YYYY-MM-DD.md
whatsapp = false            # delegate sending to the agent
review_before_send = true   # confirm before sending
```

WhatsApp is **delegated to the agent**: if the session has a WhatsApp tool
(e.g. whatsmiau via Composio), the agent builds the short version and sends it
to the workspace's `whatsapp_target` after confirmation. If there is no such
tool, the step is skipped — nothing breaks.

Running the digest more than once a day **versions** the file: the previous
version is moved to `archive/`, and archives older than
`archive_retention_days` are pruned.

### Manual MCP registration

The installer registers the MCP on every agent it finds. To do it by hand with
`~/.local/bin/daily-digest-mcp`:

```bash
# Verboo Code
verboo mcp add --scope user daily_digest -- ~/.local/bin/daily-digest-mcp

# [CC]
claude mcp add --scope user --transport stdio daily_digest -- ~/.local/bin/daily-digest-mcp

# Codex (~/.codex/config.toml)
# [mcp_servers.daily_digest]
# command = "/home/USER/.local/bin/daily-digest-mcp"
```

> **Verboo Code and [CC] share `~/.claude`.** Verboo reads `~/.claude/skills` as
> a legacy store, so the installer writes the skill to exactly one place to keep
> the `/` menu clean: `~/.claude/skills` when real [CC] is installed, otherwise
> `~/.verboo/skills`. If `/resumo-do-dia` appears more than once, run
> `./install.sh` again — it removes the duplicate skill and the stale
> `~/.verboo/commands` copy. The installer registers only the permissive
> `daily_digest` server; see
> [Hard isolation with profiles](#hard-isolation-with-profiles) to add a
> workspace-locked server.

## MCP tools

| Tool | Description |
|---|---|
| `resolve_scope(text, project)` | Maps free text / a path to a workspace scope. Returns `needs_clarification` + `candidates` when ambiguous. |
| `collect_digest(date, sources, output, workspace, project, query)` | Sessions of the day, grouped by project, redacted and compact, plus git commits. |
| `list_sessions(period, source, workspace, project)` | List sessions. |
| `search_sessions(query, source, since, until, workspace)` | Keyword search across agents and dates. |
| `write_digest(markdown, date, workspace)` | Write the workspace's daily file (validated against the profile). |
| `whoami()` | Active profile, workspaces and config path. |

`date`: `today`, `yesterday` or `YYYY-MM-DD`.

All returned content is framed as **untrusted data**; the templates instruct
the agent to never follow instructions found inside session content.

## Privacy

- Everything is local and offline, except installing the package.
- Redaction runs **in the collector**: keys (`sk-`, `vbk_`, `ghp_`, …),
  `TOKEN=…`, `PASSWORD=…`, `Bearer …`, credentials in URLs and private keys
  become `***REDACTED***`; `$HOME` paths become `~`.
- The **cache is redacted before writing** (`~/.cache/daily-digest/cache.db`,
  mode `0600`), so secrets never sit on disk in plain text.
- Output files are written `0600`; directories `0700`.
- The digest contains only prompts, files touched, commands and todos — **no**
  tool output and **no** reasoning blocks.
- **Known limitations:** redaction targets secret patterns, not arbitrary PII
  (names, emails, customer data). Add your own regexes via
  `[redact] extra_patterns`. Session content is summarized by the model of the
  agent you invoked, so it reaches that provider — that is your responsibility.

## Configuration reference

`~/.config/daily-digest/config.toml` (created from `config.example.toml`). A
malformed TOML never crashes the server: defaults are used and the error is
reported by `daily-digest-doctor`.

Key sections: `[general]` (timezone, profile, output_dir, cache_path),
`[sources.*]` (enabled/db/dir), `[git]` (enabled, roots, author),
`[redact]`, `[output]`, `[digest]` (limits, `max_output_bytes`,
`archive_retention_days`) and `[workspaces.*]`.

Set `[git] author` (or `git config --global user.email`) so commits are
attributed to you. Without an identity, commits are collected but flagged
`git_attributed = false` — they may be other people's.

## Doctor and uninstall

```bash
daily-digest-doctor          # config, sources, scope and a dry-run of today
./uninstall.sh               # remove integrations (asks confirmation)
./uninstall.sh --purge       # also remove venv + cache
./uninstall.sh --dry-run     # show what would be removed
```

`~/daily` files are never touched by the uninstaller unless you ask.

## Migration notes

- The command argument is now a **free-text scope** (e.g. "coisas da verboo"),
  not `markdown|whatsapp|both|none`. Choose outputs in the config.
- Existing `/resumo-do-dia` files are backed up before being replaced.
- Without any `[workspaces]` section the behavior is the old one (a single
  digest), so existing installs keep working.

## Development

```bash
python3 -m unittest discover -s tests    # 28 tests
python3 -m daily_digest.doctor
```

## License

MIT. The [CC]/Codex parsing rules were inspired by
[ai-sessions-mcp](https://github.com/yoavf/ai-sessions-mcp) (MIT).
