# Estatísticas locais por jogador — ChessSimulation (#52)

Este painel é **somente leitura** e apresenta o desempenho dos perfis **na instalação local do simulador**. Não representa a carreira documentada do enxadrista, rating FIDE, desempenho histórico, similaridade estilística ou análise Stockfish. Nenhuma pontuação, taxa ou duração de partidas é inferida sem fonte.

## Onde buscar os arquivos

O serviço `python -m chesssim arena --out <workspace>` consulta somente:

- `<workspace>/session-*/session.json` e seus duelos em `duels/<a>-vs-<b>/<tentativa>/summary.json`;
- `<workspace>/*/tournament.json` para torneios criados como subpastas diretas;
- `<workspace>/tournaments/*/tournament.json` para torneios organizados nessa subpasta;
- `<workspace>/tournament.json` se o próprio diretório configurado for um torneio.

Torneios armazenados em outros lugares **não são importados nem varridos implicitamente**. Para incluir um torneio, organize sua pasta sob uma das raízes descritas antes de iniciar o serviço. Não é necessária rede ou conexão com o motor. A consulta não modifica a pasta e não requer credenciais.

Endpoint: `GET /api/history/players/{slug}/stats`, com `slug` pertencente aos 64 jogadores canônicos; JSON em contrato `chesssim-history-stats-v1`. Slugs desconhecidos recebem 404. Erros operacionais não vazam caminhos internos.

## Denominadores e conciliação

| Indicador | Contagem |
|---|---|
| `run_count` | Sessões Arena ou torneios distintos em que o jogador participa, mesmo sem partidas |
| `tournaments_participated` | Manifestos válidos de torneios em que figura como participante; bye não conta como jogo |
| `duels_count` | Tentativas distintas de confronto com ao menos um PGN individual válido; dois jogos com cores invertidas = **um** duelo |
| `completed_games` | Jogos com resultado terminal comprovado por PGN legal ou queda de tempo comprovada pelo relógio |
| `wins/draws/losses` | Partição exclusiva dos jogos concluídos, do ponto de vista do jogador |
| `incomplete_games` | PGNs parciais válidos e documentados, nunca tratados como vitórias, derrotas ou empates |
| `head_to_head` | Mesmos indicadores por oponente, com um duelo contado uma única vez, independentemente das cores |

Invariantes obrigatórias:
`completed_games = wins + draws + losses`, e os totais por adversário conciliam com os totais globais. Um confronto pode ter duas partidas, mas não duas tentativas; um rematch ou nova tentativa é outro duelo apenas quando é uma tentativa canônica diferente.

**Torneios:** contam como partidas competitivas somente os duelos completos registrados em `match.attempts`, com dois PGNs legais e hashes de manifesto, resumo, motor, perfil e protocolo coerentes. Arquivos de tentativas interrompidas, excluídas e não reconciliadas são sinalizados na cobertura, sem receber resultado por presunção. Isso impede que um jogo repetido por retomada de torneio seja pontuado duas vezes.

**Arena:** considera os resumos individuais da sessão e seus `jogo_N.pgn`, ou `jogo_N.partial.pgn` quando não existir arquivo final. Uma sessão sem jogo ou ainda em execução pode aparecer como participação sem números concluídos. Se um jogo do resumo está adulterado, o confronto não contribui com números parciais: o registro é rejeitado e a cobertura informa a rejeição.

**Resultados:** conclusão por mate, empate terminal ou reivindicável são verificados contra o tabuleiro; queda de tempo exige `TimeForfeit`, relógio do lado correto esgotado e resultado compatível. Um resultado arbitrário em cabeçalho PGN não basta. Os arquivos não são reproduzidos em motor durante a consulta.

## Proveniência, cobertura e erros

`coverage` informa execuções/tentativas examinadas, jogos do jogador aceitos, registros excluídos, fontes rejeitadas e motivos agregados (sem nomes de arquivos privados). `complete=false` indica cobertura **parcial**; números exibidos são subconjuntos verificados e não devem ser anunciados como desempenho global do acervo. Em caso de ausência de dados, a interface exibe “Sem partidas verificadas”.

O campo `updated_at`, quando presente, é a data de criação UTC registrada nos manifestos participantes, não um tempo de conclusão de partida nem um timestamp criado a partir de mtime do sistema. Pode ser `null`.

As leituras usam limites de volume e acesso por descritores com `O_NOFOLLOW`, recusando symlinks, traversal, arquivos grandes, PGNs inválidos, resultados contraditórios e JSON ambíguo. Não há threads de motor, gravações, subprocessos, downloads, manipulação de rating nem chamadas POST adicionais. Para preservar desempenho, coleções excessivamente grandes excedendo os limites de segurança recebem 503, não um total silenciosamente truncado.

## Validação local

Após instalar a PR e iniciar a Arena com um workspace existente, abra um perfil na aba História e confira **No simulador**. Para inspecionar o contrato sem interface:

```text
http://127.0.0.1:8766/api/history/players/fischer/stats
```

O resultado fica vazio sem partidas no workspace configurado. Testes sintéticos cobrem cores invertidas, revanche, empate, torneios/bye, interrupção, manifesto repetido, arquivo corrompido, queda de tempo, symlink, controle de escopo, requisição HTTP e estados de navegador. A homologação visual no Chrome/Debian continua parte do gate #53.
