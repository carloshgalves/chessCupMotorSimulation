# Issue #70 — Histórico completo de partidas no simulador

## Telas e arquitetura

Esta PR implementa o segundo conjunto de mockups aprovado:
1. **História / Visão geral** do enxadrista: catálogo pesquisável (sem novas fotografias), identidade histórica existente, resultados verificados do simulador, últimas partidas, competições, Elo experimental e documentação histórica preservada.
2. **Partidas no ChessSimulation**: tabela de 8 colunas com filtros por competição, adversário, cor, modalidade, resultado, origem e data, paginação de 20 registros por página e exportações JSON, CSV e PGN.
3. Replay local ao selecionar **Abrir replay**: somente quando o registro foi gerado de **UCI completo e PGN verificado**. Projeções têm **placar, não lances**.

A seção pré-existente **Partidas para explorar (até 5)** permanece distinta: são jogos históricos documentados e não devem ser confundidos com partidas da simulação. A biografia e os direitos dos retratos existentes não foram alterados.

## Fontes e proveniência

O índice deriva dos artefatos locais no mesmo \`--out\` da Arena:
- Sessões da Arena e torneios legados conferidos pelo agregador de estatísticas existente.
- Liga: \`league.json\`, calendário, recibos e PGNs conferidos pelo \`LeagueManager._ledger\`.
- Copa: \`cup.json\`, recibos e PGNs conferidos pelo \`CupManager._state\`.

\`engine_full\`: PGN com hash; apenas jogos concluídos comprovados entram no Elo experimental do ritmo pertinente. \`fast_statistical\`: placar determinístico, sem PGN, sem replay nem ingresso na classificação experimental do Elo. Jogos incompletos da Arena nunca recebem resultado presumido; relatórios de cobertura revelam rejeições. Datas reais são timestamps ISO quando presentes. **Não** se atribui hora/duração a calendário fictício de liga nem se usa data do jogo como data histórica real. Títulos reais continuam separados de títulos simulados.

A classificação de Elo é **interna ao simulador**, iniciada em 2500 e nunca confundida com força configurada do Rodent ou rating FIDE. As curvas por modalidade são extraídas do ledger auditado do Elo, sem valores inventados. O pipeline histórico agora inclui jogos **UCI verificados de Ligas**, bem como Arena, Copas e torneios, sem duplicar partidas.

## Segurança e desempenho

- APIs \`GET /api/history/archive/<slug>?\` com filtros allowlist e \`offset=0..20000\`, \`limit=1..100\`.
- \`GET /api/history/archive/<slug>/elo?modality=classical|rapid|blitz|bullet|unclassified\`.
- \`GET /api/history/archive/<slug>/replay/<id>\` valida propriedade do jogo pelo slug, origem, evidência PGN e registro de resultado; não inicia o motor.
- \`GET /api/history/archive/<slug>/export/json|csv|pgn\` exporta somente registros selecionados. PGN exporta somente partidas UCI real e comprovadas. CSV escapa campos iniciados por \`=\`, \`+\`, \`-\`, \`@\`.
- Índice read-only em memória reutilizado enquanto assinatura de mtime/tamanho dos arquivos fontes não se altera. Paginação ocorre sem ler novamente milhares de PGNs; commits nos ledgers invalidam a assinatura. GET não altera arquivos, não faz download, não executa engines.
- Rejeita campos desconhecidos, slugs inválidos, origem não permitida, datas impossíveis, parâmetros duplicados, PGNs com hash inconsistente, IDs de replay arbitrários e diretórios symlink. \`MAX_ROWS=20000\`, no máximo 100 ligas e 100 copas por workspace.

## Gate do mantenedor

Executar em Debian: \`python -m pytest -q -ra\`, \`python -m ruff check src tests\`; iniciar a Arena no **mesmo** \`--out\` da PR #80; abrir História → Perfil → Visão geral e Partidas; verificar a pesquisa/paginação, separação de projeções e PGNs, replay e export. Navegar Arena↔História sem interromper jogos.

Os mockups têm dados ilustrativos. O aplicativo mostrará apenas números deriváveis dos arquivos reais do workspace. **Nenhum merge automático**; cabe ao mantenedor validar o layout no Chrome antes da integração à main.
