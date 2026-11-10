> **Estado da `main` em 09/11/2026:** PR #81 (merge `8d7af85e`) e PR #82 (merge `7830ef48`) já foram integradas. HEAD da #82 teve 696 testes aprovados e 2 ignorados em cada Python 3.11/3.12/3.13 ([CI](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/42712702292)). O arquivo indexa Arena, torneios, Copas e Ligas, mas **ainda não** indexa `human-games/`: #70 segue **PARCIAL**, com slice #84. A prova 4.032 refere-se a registros de fixture, não a 4.032 PGNs físicos. Benchmark e smoke real ficam na #83; status manual `NOT_RUN`.

# Histórico do Enxadrista e Arquivo de Partidas — Issue #70

## Duas telas, dados reais e limites

A interface incorpora as duas imagens aprovadas para a segunda entrega: **História do Enxadrista** (cabeçalho, biografia, lista lateral de 64, visão geral, últimos jogos, estatísticas, linha do tempo, modalidade, torneios, Elo e Registros) e **Partidas do ChessSimulation** (busca com filtros, tabela, paginação, exportação e replay).

- Nunca expor o ranking inicial 2500 como Elo FIDE ou rating de carreira histórica. Não criar gráfico histórico de rating ou precisão/estilo sem fontes medidas; todos os números da tela vêm de partidas UCI verificadas.
- O acervo biográfico original, marcos com referências, licença de retrato, PGNs históricos curados, ratings e o painel anterior de estatísticas permanecem acessíveis em **Registros**, sem alteração dos arquivos originais.
- A lista inclui todas as partidas de Arena, torneios legados, Ligas e Copas que passaram pela verificação existente em \`history_stats.verified_competition_games\`, mais **projeções sintéticas isoladas** em seção da lista identificada por sua origem. A verificação calcula resultado do PGN e checa hashes/proveniência. Não se confunde projeção com PGN, nem soma seus resultados ao Elo.
- Cor, adversário, resultado, modalidade, fonte, condição e timestamp de registro são documentados pelo workspace. A coluna **Registro** não finge ser data do confronto histórico nem afirma o dia exato de execução quando só existe o manifesto de criação.
- Jogos reais completos possuem replay do PGN validado. Tentativas incompletas não recebem pontuação ou replay de resultado final. Projeções ficam **Sem PGN**; lances não são inventados.
- Busca e filtros acontecem **no servidor**, com paginação de até 50 itens; a UI usa 20 por página. O índice em memória é invalidado por alterações em manifestos, recibos, ledgers ou PGNs (incluindo ctime). Primeiro acesso ou alteração de evidências exige auditoria integral; mudar de página não reabre PGNs. Replay e exportação PGN verificam novamente os bytes e hashes. Exportações CSV, JSON e PGN exportam **somente a página selecionada**, preservando o filtro e recusando PGN quando não houver partidas UCI completas. CSV/JSON incluem hashes de PGN, motor, perfis, recibos e metadados temporais quando existentes. A declaração de licença `not_assessed` não afirma autorização de distribuição. O PGN exportado possui tags ChessSimOrigin, ChessSimGameID e ChessSimPGNSHA256 referentes à fonte original.
- Arquivos locais nunca são modificados por GET de História, arquivo, exportação e replay. Leitura de PGN com descritores \`O_NOFOLLOW\`; caminho obtido de ID previamente verificado e conferido com o hash original. Erros de cobertura são expostos sem extrapolar amostra.

## Contratos HTTP

| Método | Rota | Semântica |
| --- | --- | --- |
| GET | \`/api/history/players/<slug>/archive?page=1&page_size=20&origin=all&modality=all&result=all&q=\` | Lista com total, página, estatísticas reais, projeções isoladas e cobertura |
| GET | \`/api/history/archive/replay?slug=<slug>&id=<id>\` | Estados FEN e SAN de um único PGN verificado; recusa projeção, tentativa incompleta e IDs não pertencentes ao jogador |
| GET | \`/api/history/players/<slug>/archive/export/json\` | JSON da página filtrada |
| GET | \`/api/history/players/<slug>/archive/export/csv\` | CSV UTF-8/BOM da página |
| GET | \`/api/history/players/<slug>/archive/export/pgn\` | PGNs UCI comprovados da página |

Filtros:
- \`origin\`: \`all|arena|tournament|league|cup|projection\`.
- \`modality\`: \`all|classical|rapid|blitz|bullet|unclassified\`.
- \`result\`: \`all|win|draw|loss|incomplete\`.
- \`q\`: no máximo 80 caracteres para nome do adversário, competição e ID de registro.
- \`page\`: 1–16384; \`page_size\`: 1–50.

Nenhuma nova consulta externa, download de PGNs ou representação de jogadores foi introduzida.

## Validação no Debian

\`\`\`bash
cd ~/chesssim-pr82
source .venv/bin/activate
python -m pytest -q -ra
python -m ruff check src tests
python -m chesssim arena --rodent "$HOME/Projetos/Engines/rodent-iv" \\
  --out "$HOME/chesssim-pr80-evidence" --port 8766
\`\`\`

Use o **mesmo --out da homologação anterior** para que partidas já executadas apareçam. Ao abrir História e selecionar um jogador com evidência local, verifique as abas **Visão geral, Partidas, Torneios, Elo experimental, Registros**. Teste os filtros, páginas, replay real, exportação da página, e mudança entre jogadores. Copas sintéticas devem ser encontráveis mas oferecer **Sem PGN**, sem aumentar vitórias UCI ou Elo. Os valores finais só podem ser determinados pelo workspace local, não pelos exemplos ilustrados nos prints.

**Estado de integração:** a CI e a revisão automatizada foram concluídas, e as PRs #81 e #82 foram mescladas nessa ordem mediante autorização humana. **A exigência anterior de inspeção visual antes do merge não foi comprovada como executada** e não deve ser convertida retroativamente em PASS: ver homologação real Chrome/Debian em #83 e gate de História/direitos em #53. A issue #70 continua parcial até a #84 e a escala física de 4.032 PGNs segue sem benchmark.

## Filtros complementares e conciliação

São aceitos `color=all|white|black`, `opponent=<slug>`, `engine_sha256=<64 hex>`, `date_from`/`date_to` (AAAA-MM-DD, data UTC do registro), além dos filtros de ano. A interface mostra timestamps no fuso do navegador. O registro do manifesto não é a hora comprovada dos lances.

A leitura das Ligas reutiliza a validação original de manifesto, journal, recibos e PGNs sem inicializar o gerenciador nem executar recuperação. Temporadas corrompidas são rejeitadas integralmente, com cobertura parcial explícita. Partidas UCI completas recebem `origin=league` e atualizam Elo; projeções `fast_statistical` recebem `origin=projection`, sem PGN, duração ou rating. `tournament` identifica torneios mata-mata legados, e não temporadas de Liga.

O teste de escala de 4.032 registros comprova 81 páginas com limite 50, reaproveitamento do índice entre páginas e invalidação por alteração em manifesto. Não representa benchmark de leitura de 4.032 PGNs físicos no Debian, que ainda exige validação operacional.
