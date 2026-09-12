# daily-digest

Um servidor **MCP local** que lê as suas sessões de agentes de IA (opencode, [CC] e Codex)
na sua máquina, normaliza, **remove segredos** e entrega os dados para o próprio agente
resumir. No fim do dia, dentro de qualquer sessão, você digita:

```
/resumo-do-dia
```

O modelo da sua sessão monta o resumo, grava um Markdown e — se você configurar — envia
no WhatsApp. **Sem CLI, sem chave de API de LLM, sem processo em background.**

## Como funciona

```
opencode.db  ─┐
claude *.jsonl ─┼─▶ adapters ─▶ normalização ─▶ redação ─▶ MCP collect_digest ─▶ seu agente resume
codex *.jsonl  ─┘                                              │
                                                               └─▶ write_digest ─▶ ~/daily/YYYY-MM-DD.md
```

- A **sumarização é feita pelo modelo do agente** onde você digitou o prompt.
- O MCP só **coleta, redige e grava**. Nada de segredo sai da máquina sem passar pela redação.
- Sessões de agentes diferentes rodando ao mesmo tempo são todas capturadas; sub-agentes
  são agregados ao pai (sem contar trabalho duas vezes).

## Requisitos

- Python **3.11+** (usa `venv` e `tomllib`)
- Pelo menos um dos agentes: opencode, [CC] ou Codex
- Acesso à internet **apenas na instalação** (para baixar o SDK `mcp`)

## Instalação

```bash
git clone <url-do-repo> daily-digest
cd daily-digest
./install.sh
```

O `install.sh` faz um preflight (Python, venv, integridade do `opencode.db`, PyPI, permissões),
cria um venv em `~/.local/share/daily-digest/venv`, instala o SDK `mcp`, registra o MCP
**apenas nos agentes detectados** e instala o comando `/resumo-do-dia`. É idempotente e faz
backup (`.bak`) antes de editar qualquer configuração.

### Configuração

Edite `~/.config/daily-digest/config.toml` (criado a partir de `config.example.toml`):

```toml
[output]
markdown = true     # grava ~/daily/YYYY-MM-DD.md
whatsapp = false    # envia no WhatsApp (se a sessão tiver ferramenta de envio)
review_before_send = true
```

## Uso

Dentro de qualquer sessão do seu agente:

| Comando | Efeito |
|---|---|
| `/resumo-do-dia` | usa o config |
| `/resumo-do-dia markdown` | só grava o Markdown |
| `/resumo-do-dia whatsapp` | só envia no WhatsApp |
| `/resumo-do-dia both` | grava e envia |
| `/resumo-do-dia none` | apenas mostra no chat |

### WhatsApp

O envio é **delegado ao agente**. Se a sua sessão tiver uma ferramenta de WhatsApp
(por exemplo, `whatsmiau` via Composio), o agente monta a versão curta e envia após a sua
confirmação. Se não tiver, o envio é simplesmente pulado — nada quebra.

## Ferramentas MCP

| Tool | Descrição |
|---|---|
| `collect_digest(date, sources, output)` | Sessões do dia agrupadas por projeto, redigidas e compactas + commits git |
| `list_sessions(period, source)` | Lista as sessões do período |
| `search_sessions(query, source)` | Busca por palavra-chave entre agentes |
| `write_digest(markdown, date)` | Grava `~/daily/<date>.md` (versão anterior vai para `archive/`) |

`date`: `today`, `yesterday` ou `YYYY-MM-DD`. `sources`: `all` ou `opencode,claude,codex`.

### Registro manual do MCP

O `install.sh` registra automaticamente quando encontra a CLI/config do agente. Se precisar
fazer na mão, use o launcher `~/.local/bin/daily-digest-mcp`:

**[CC]**
```bash
claude mcp add --scope user --transport stdio daily_digest -- ~/.local/bin/daily-digest-mcp
```

**Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.daily_digest]
command = "/home/SEU_USUARIO/.local/bin/daily-digest-mcp"
```

**opencode** (`~/.config/opencode/opencode.json`):
```json
{
  "mcp": {
    "daily_digest": { "type": "local", "command": ["/home/SEU_USUARIO/.local/bin/daily-digest-mcp"], "enabled": true }
  }
}
```

> Após registrar, **reinicie o agente** para ele carregar o novo MCP.

## Privacidade

- Tudo local e offline, exceto a instalação do SDK.
- A redação roda **no coletor**: chaves (`sk-`, `vbk_`, `ghp_`, ...), `TOKEN=...`,
  `PASSWORD=...`, `Bearer`, credenciais em URL e chaves privadas são substituídas por
  `***REDACTED***`; caminhos `$HOME` viram `~`.
- O digest **não** inclui saída de ferramentas nem blocos de raciocínio, apenas prompts,
  arquivos tocados, comandos e todos — mantendo o contexto do agente enxuto.

## Estrutura

```
daily_digest/
├─ mcp_server.py          # servidor MCP (SDK mcp)
├─ digest.py              # janela, compressão, redação, agrupamento
├─ normalize.py           # modelo Session/Event + rollup de sub-agentes
├─ redact.py              # remoção de segredos
├─ cache.py               # cache incremental (SQLite WAL)
├─ git_source.py          # commits dos repos que tiveram sessão
└─ adapters/              # opencode, claude, codex, registry
integrations/             # /resumo-do-dia para cada agente
install.sh                # preflight + instalação
```

## Licença

MIT. As regras de parsing dos formatos [CC]/Codex foram inspiradas no
[ai-sessions-mcp](https://github.com/yoavf/ai-sessions-mcp) (MIT).
