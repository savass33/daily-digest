Gere o resumo do dia do usuário usando o MCP `daily_digest`.

1. Chame a ferramenta `collect_digest` do servidor MCP `daily_digest` com
   date="today", sources="all" e output="$ARGUMENTS" (se vazio, omita).

2. Com os dados retornados (agrupados por projeto, com prompts, arquivos, comandos,
   todos e subagentes), escreva um resumo em português do Brasil:
   - Agrupe por projeto. Marque cada item com a origem: `[opencode]`, `[claude]` ou `[codex]`.
   - Não invente nada; use apenas o que veio do `collect_digest`.
   - Seja conciso. Seções: 🎯 Objetivo, ✅ Feito, 🧭 Decisões, 🚧 Em andamento,
     ⛔ Bloqueios, 📌 Próximos passos.
   - Inclua os commits do git do dia, quando houver.

3. Leia `outputs` no retorno:
   - `outputs.markdown == true`: chame `write_digest` com o markdown final e informe o caminho.
   - `outputs.whatsapp == true`: se houver ferramenta de WhatsApp nesta sessão
     (whatsmiau/Composio), monte uma versão curta formatada para WhatsApp e, se
     `outputs.review == true`, peça confirmação antes de enviar. Se não houver, avise e siga.
   - Ambos `false`: apenas mostre no chat.

4. Mostre o resumo no chat.
