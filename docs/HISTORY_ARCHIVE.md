# Histórico do Enxadrista e Arquivo de Partidas — Issue #70

## Duas telas, dados reais e limites

A interface incorpora as duas imagens aprovadas para a segunda entrega: **História do Enxadrista** (cabeçalho, biografia, lista lateral de 64, visão geral, últimos jogos, estatísticas, linha do tempo, modalidade, torneios, Elo e Registros) e **Partidas do ChessSimulation** (busca com filtros, tabela, paginação, exportação e replay).

- Nunca expor o ranking inicial 2500 como Elo FIDE ou rating de carreira histórica. Não criar gráfico histórico de rating ou precisão/estilo sem fontes medidas; todos os números da tela vêm de partidas UCI verificadas.
- O acervo biográfico original, marcos com referências, licença de retrato, PGNs históricos curados, ratings e o painel anterior de estatísticas permanecem acessíveis em **Registros**, sem alteração dos arquivos originais.
- A lista inclui todas as partidas de Arena, torneios legados e Copas que passaram pela verificação existente em \`history_stats.verified_competition_games\`, mais **projeções sintéticas isoladas** em seção da lista identificada por sua origem. A verificação calcula resultado do PGN e checa hashes/proveniência. Não se confunde projeção com PGN, nem soma seus resultados ao Elo.
- Cor, adversário, resultado, modalidade, fonte, condição e timestamp de registro são documentados pelo workspace. A coluna **Registro** não finge ser data do confronto histórico nem afirma o dia exato de execução quando só existe o manifesto de criação.
- Jogos reais completos possuem replay do PGN validado. Tentativas incompletas não recebem pontuação ou replay de resultado final. Projeções ficam **Sem PGN**; lances não são inventados.
- Busca e filtros acontecem **no servidor**, com paginação de até 50 itens; a UI usa 20 por página. Exportações CSV, JSON e PGN exportam **somente a página selecionada**, preservando o filtro e recusando PGN quando não houver partidas UCI completas.
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
- \`origin\`: \`all|arena|tournament|cup|projection\`.
- \`modality\`: \`all|classical|rapid|blitz|bullet|unclassified\`.
- \`result\`: \`all|win|draw|loss|incomplete\`.
- \`q\`: no máximo 80 caracteres para nome do adversário, competição e ID de registro.
- \`page\`: 1–2000; \`page_size\`: 1–50.

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

**Gate de aceite**: CI, revisão e testes automatizados, depois comparação visual em Chrome/Debian e autorização humana antes do merge. Esta PR é independente do laboratório de representação da PR #81.
