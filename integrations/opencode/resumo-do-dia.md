---
description: Gera o resumo do dia a partir de todas as sessões de IA (opencode, [CC], Codex). Uso: /resumo-do-dia [markdown|whatsapp|both|none]
agent: build
---

Você vai gerar o meu resumo de trabalho do dia usando o MCP `daily_digest`.

1. Chame a ferramenta `collect_digest` do servidor MCP `daily_digest` com:
   - `date`: "today"
   - `sources`: "all"
   - `output`: "$ARGUMENTS" (se `$ARGUMENTS` estiver vazio, omita este campo)

2. Com os dados retornados (agrupados por projeto, com prompts, arquivos, comandos,
   todos e subagentes), escreva um resumo em português do Brasil. Regras:
   - Agrupe por projeto. Marque cada item com a origem: `[opencode]`, `[claude]` ou `[codex]`.
   - Não invente nada: use apenas o que veio do `collect_digest`.
   - Seja conciso e objetivo. Use bullets.
   - Seções: 🎯 Objetivo, ✅ Feito, 🧭 Decisões, 🚧 Em andamento, ⛔ Bloqueios, 📌 Próximos passos.
   - Inclua os commits do git do dia, quando houver.

3. Leia o campo `outputs` do retorno para decidir as saídas:
   - Se `outputs.markdown` for `true`: chame `write_digest` com o markdown final
     (`date`: "today") e informe o caminho gravado.
   - Se `outputs.whatsapp` for `true`: verifique se esta sessão tem alguma ferramenta
     de envio de WhatsApp disponível (por exemplo, whatsmiau/Composio). Se tiver,
     monte uma versão curta (formatação do WhatsApp: *negrito*, quebras de linha) e,
     se `outputs.review` for `true`, mostre-a e peça minha confirmação antes de enviar.
     Se não houver ferramenta de WhatsApp, apenas avise que o envio foi pulado e siga.
   - Se ambos forem `false`: apenas mostre o resumo no chat.

4. No final, mostre o resumo no chat.
