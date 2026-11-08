# Ligas e Temporadas — contrato e homologação da Issue #59

## Experiência aprovada

A nova aba **Ligas** reutiliza a linguagem visual da Arena e segue o layout aprovado: barra de navegação; coluna **Minhas Ligas**; centro com nome, temporada, modalidade, participantes, formato, Classificação/Rodadas/Partidas/Detalhes; coluna direita com progresso, próxima rodada, executar, exportar e pausar; formulário inferior **Criar nova liga**. Preferências avançadas ficam recolhidas para manter a distribuição e as dimensões do mockup. Não há botão Limpar.

Na criação, os quatro campos principais são nome, ritmo, formato e rótulo de temporada. O painel de configuração avançada permite escolher 4, 8, 16, 32 ou 64 enxadristas do catálogo, profundidade UCI, pontos por vitória/empate/derrota e tipo de execução. O padrão é **8 participantes, 3/1/0, rápido, turno e returno, motor UCI real**. A seleção inicial privilegia os perfis instalados, mas isso não valida sua autenticidade histórica.

## Calendário e classificação

- Método circular determinístico. Para \`n\` enxadristas pares, cada turno tem \`n-1\` rodadas e \`n(n-1)/2\` partidas. O returno inverte as cores para cada confronto. Com 64 participantes, são 126 rodadas e **4.032 jogos**; não há confrontos dirigidos por um rating artificial.
- Temporadas têm **rótulos fictícios**, não alegações de eventos históricos. Cada rodada tem uma **semana/dia virtual** ordinal (semana r, dia 1 + 7 × (r−1)). Cada execução real registra \`started_utc\` e \`finished_utc\` com UTC explícito; a interface converte horários reais ao fuso do navegador.
- Pontos de liga são configuráveis: 3 por vitória, 1 por empate e 0 por derrota, como padrão. Cada PGN concluído vale uma partida; partidas incompletas, falhas, pendentes e tentativas sem resultado não somam pontos.
- Ordem de desempate: **pontos**, **vitórias**, **confronto direto**, **Sonneborn–Berger**, **slug canônico**. O confronto direto entra no desempate apenas se **exatamente dois jogadores** estiverem empatados em pontos/vitórias **e todos os confrontos programados entre ambos estiverem concluídos**; em empate entre três ou mais, usa-se Sonneborn–Berger diretamente. SB soma os pontos finais dos adversários derrotados e metade dos pontos dos adversários contra os quais se empatou. Nenhum saldo de gols.
- A coluna **Elo exp.** é um **indicador externo por modalidade**, consultado ao módulo já implementado de Elo experimental. Não é a pontuação da Liga; não altera o Elo e não é rating FIDE.

## Modos de execução e proveniência

**engine_full:** usa um **Rodent IV instalado**, os perfis registrados e \`python-chess\`, um jogo por vez. A temporada congela os hashes do executável e de todos os arquivos de perfil no início da execução; se qualquer um mudar, a retomada da temporada é recusada até uma decisão humana. Cada jogo concluído ganha um PGN e recibo com SHA-256, resultado, termo de encerramento, jogadores, relógio, versão efetiva do motor/perfil (hash), modo e timestamps. **Sem perfil UCI real, a execução é recusada; não há lances ou PGNs fictícios**.

**fast_statistical:** gera apenas **projeções determinísticas**, com semente e confrontos explicitamente identificados. Não executa UCI, não atribui força a perfis sem calibração, **não gera PGN, não pontua Elo** e registra \`source=fast_statistical\`. Uma projeção é resultado de modelo, não uma partida observada. A UI o informa em detalhes e no histórico. Não se converte uma temporada estatística em resultados reais.

O catálogo contém 64 enxadristas, mas apenas **21 aliases de Rodent IV registrados** nesta revisão. É possível criar calendários de 64, mas a temporada \`engine_full\` só pode começar quando todos os perfis selecionados puderem ser comprovados localmente.

## Persistência, idempotência e exclusão mútua

Os arquivos ficam sob \`<workspace>/leagues/league-<id>/\`:

- \`league.json\`: configuração congelada, horário de criação, estado da rodada e hashes de recursos.
- \`games/rNNN-gNN.pgn\`: somente partidas UCI concluídas e comprováveis.
- \`receipts/rNNN-gNN.json\`: comprovante por jogo com checksum e fonte.
- \`ledger.jsonl\`: journal append-only de recibos concluídos; cada ID de partida aparece **no máximo uma vez**.

A **gravação de um jogo** segue: PGN atômico (se real) → recibo atômico → append e \`fsync\` do journal. Se o processo cair depois do recibo e antes do append, a próxima operação explícita de execução reconciliará o recibo comprovado sem refazer o jogo. As rotas GET exibem recibos comprovados mesmo antes da reconciliação, **sem escrever arquivos**. A reabertura do servidor converte uma execução deixada em RUNNING em PAUSED: a retomada exige comando explícito.

A Arena mantém exclusão mútua de **Duelo, Jogar e Ligas**. O comando **Executar rodada** processa somente a rodada atual, serialmente, para não esgotar CPU/RAM. **Pausar temporada** solicita parada **após a partida em andamento**. Fechar a aba não interrompe o servidor nem concede tempo extra aos motores. A saída é confirmada por checkpoints; partidas incompletas continuam pendentes.

As operações de API escrevendo estado exigem Host loopback, Origin local exata, token de autorização no fragmento da URL e corpo JSON estrito de até 4 KiB. Identificadores externos têm regex fixa e não viram caminhos arbitrários. Não há persistência do token em arquivos de temporada. O hash SHA-256 verifica consistência local; não é certificado de autenticidade externa.

## API local

| Método | Endpoint | Uso |
| --- | --- | --- |
| GET | \`/api/leagues\` | Catálogo de ligas |
| GET | \`/api/leagues/{id}\` | Detalhe, tabela, progresso e próxima rodada |
| GET | \`/api/leagues/{id}/round/{n}\` | Confrontos de rodada escolhida |
| GET | \`/api/leagues/{id}/games?player=&offset=0&limit=20\` | Histórico filtrado/paginado |
| GET | \`/api/leagues/{id}/export/json\` | JSON completo com fonte e checksums |
| GET | \`/api/leagues/{id}/export/pgn\` | PGNs reais verificados; indisponível sem partidas UCI |
| POST | \`/api/leagues/create\` | Cria temporada com participantes e regras |
| POST | \`/api/leagues/{id}/run\` | Inicia ou retoma a próxima rodada |
| POST | \`/api/leagues/{id}/pause\` | Pausa cooperativa entre partidas |

## Plano de aceite no Debian 13

1. Atualize a branch da PR #79 e execute \`python -m pytest -q -ra\` e \`python -m ruff check src tests\`.
2. Execute \`python -m chesssim arena --rodent "$HOME/Projetos/Engines/rodent-iv" --out "$HOME/chesssim-pr79-evidence" --port 8766\`; abra a URL do terminal e entre em **Ligas**.
3. Crie uma liga pequena com **4 participantes instalados**, modalidade **Blitz** ou **Rápido**, turno/returno e modo \`engine_full\`. Confira nomes, totais de rodadas, placar inicial, calendário, áreas Classificação/Rodadas/Partidas/Detalhes.
4. Execute uma rodada real, verifique partidas concluídas, pontuação e arquivos \`games/\`, \`receipts/\` e \`ledger.jsonl\` dentro do workspace; confira exportação JSON e PGN e hashes. Pausar/retomar não pode alterar placares anteriores.
5. Crie uma temporada **64 participantes**, turno/returno, sem iniciar a execução. Confirme **126 rodadas e 4.032 partidas**. Com perfis não instalados, \`engine_full\` não pode inventar resultados.
6. Se desejar testar desempenho do calendário sem Rodent, crie **outra** liga em \`fast_statistical\`. Valide aviso explícito de simulação, ausência de PGN e que não altera o Elo; a fonte não pode ser confundida com partida real.
7. Navegue entre Arena, Ligas e História, use busca de enxadrista, histórico por jogador, exportação e vista mobile. Recarregue a página e reinicie o servidor para verificar persistência e retomada.
8. Inspecione a legibilidade, largura dos três painéis, tabela e botões contra o **print aprovado**. Este gate visual real não é substituído por testes automatizados.

A PR só deverá ser mesclada após a decisão do mantenedor.
