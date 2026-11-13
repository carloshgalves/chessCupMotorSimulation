# Base científica real por jogador e ano (#10, #11, #13)

A rotina **real_training_cohorts** analisa PGNs reais e exporta exemplos de
decisões históricas por jogador e ano. Nenhuma jogada sintética é usada como
evidência científica. Não representa treinamento de rede neural nem calibração
concluída de Rodent IV.

## Uso imediato: quatro jogadores reais

    cd ~/Projetos/GitHub/chessCupMotorSimulation
    source .venv/bin/activate
    python -m pip install -e '.[dev]'
    mkdir -p "$HOME/chesssim-homologacao"
    python -m chesssim.real_training_cohorts --fetch \
      --players fischer tal karpov kasparov --min-ply 17 \
      --out "$HOME/chesssim-homologacao/coortes-01"

A saída deve ser um diretório **novo**: manifest.json e arquivos
train/{slug}/{year}.jsonl. Cada linha registra FEN pré-lance, lance UCI
observado, SHA da partida e da fonte, ano, jogador, cor, adversário,
resultado relativo, ECO, segmento do intervalo observado de carreira e
idade exata somente se declarada/comprovável por data completa.

## Ampliar para até 64 enxadristas do elenco

    python -m chesssim.real_training_cohorts --fetch --all-roster \
      --allow-missing --out "$HOME/chesssim-homologacao/coortes-64"

O elenco versionado fornece 64 slugs. Os nomes de arquivo são **candidatos**
do PGN Mentor, com mapeamentos explícitos para exceções (Ding,
VachierLagrave, Hou, Polgar, Mecking). A existência dos arquivos não é
presumida. Com --allow-missing apenas fontes HTTP 404 são ignoradas e
registradas; fontes inválidas continuam erro. É possível fornecer PGNs
locais usando --archive-dir ou mapear nomes via --source-files-json
(ex.: {"mequinho":"Mecking"}). Não gerar jogos artificiais para ausências.

Datas de nascimento opcionais podem vir de --birthdates-json contendo
JSON de slug para data ISO completa (ex.: {"fischer":"1943-03-09"}).
Sem nascimento ou sem data completa da partida, age_at_game fica null.

## Regras de separação e qualidade

- Identidade: foco por sobrenome do elenco, descartando registros cujo
  cabeçalho identifica os dois lados ou nenhum lado como jogador focal.
- Ano: ano civil observado nos PGNs, admitindo dia/mês desconhecidos; ano
  desconhecido é mantido fora de todos os exemplos de treino.
- Partições: por jogador, anos inteiros em ordem cronológica; alvo 60/20/20
  para treino/validação/holdout. As proporções reais dependem dos anos.
- Duplicação: SHA-256 de FEN inicial + sequência integral de lances UCI.
  Trajetórias repetidas internamente são descartadas.
- Contaminação intercoleções: mesmo hash em braços diferentes é bloqueado
  em todos os exemplos de treino, sem contar duplicados como independentes.
- Embargo posicional: nenhuma FEN completa idêntica às observadas em
  validação/holdout/quarentena pode entrar nos exemplos de treino.
- Carreira: terços do intervalo de anos observados (early/middle/late
  observed), **não** diagnóstico de pico de desempenho.
- Cor: brancas/pretas; resultado vitória/empate/derrota a partir da
  perspectiva focal; oponente segundo o header.
- Abertura: ECO A00-E99 do header quando presente; sem valor inferido.
- Fase: proxy explícito por ply 1-16, 17-60, 61+, não classificação
  posicional validada por Stockfish.
- Nascimento: idade só com data completa ISO declarada e PGN dia/mês/ano.
- O holdout não é exportado como PGN ou JSONL e seus lances não aparecem
  nos artefatos. O embargo por FEN exata não cobre analogias estratégicas.

O corpus foi lido para indexação e não está fisicamente lacrado; o pipeline
não atribui validação histórica a nenhuma personalidade. A próxima etapa
será usar decisões reais em FENs comuns para aferir Rodent/perfis, baseline
genérico e controles negativos de #11, com avaliação cega em #12.

Fonte: https://www.pgnmentor.com/files.html.
Snapshot: rozim/ChessData no commit
aaadf90e11862bc6f207fed407a70e7017df26bd.

## Duas políticas distintas de separação temporal

O padrão --split-policy chronological reserva os anos mais recentes para
validação/holdout: testa generalização para o futuro do histórico observado,
mas o treino fica concentrado nos primeiros períodos de carreira.

Para **aprender preferências ao longo de toda a carreira observada**, usar
--split-policy career-stratified, por exemplo:

    python -m chesssim.real_training_cohorts --fetch \
      --players fischer tal karpov kasparov \
      --split-policy career-stratified \
      --out "$HOME/chesssim-homologacao/coortes-carreira-01"

Esse modo divide os **anos observados** em três faixas consecutivas e
mantém anos distintos de treino, validação e holdout **dentro de cada faixa**.
Nunca mistura posições da mesma trajetória entre braços e continua
embargando todas as FENs completas reservadas. Uma faixa com menos de
três anos distintos é colocada integralmente em quarentena, e o manifesto
registra a insuficiência. A avaliação desse modo mede interpolação dentro
da trajetória observada, e **não** desempenho em anos futuros.

## Datas históricas e faixas etárias verificáveis

O workflow real carrega docs/REAL_BIRTHDATES_4.json, cuja origem é o
catálogo biográfico Chess.com dos quatro grandes mestres:

- Bobby Fischer (1943-03-09): https://www.chess.com/players/bobby-fischer
- Mikhail Tal (1936-11-09): https://www.chess.com/players/mikhail-tal
- Anatoly Karpov (1951-05-23): https://www.chess.com/players/anatoly-karpov
- Garry Kasparov (1963-04-13): https://www.chess.com/players/garry-kasparov

As partidas que possuem só o ano histórico não recebem uma idade exata
inventada: age_at_game permanece null, e age_bounds guarda os anos de
idade mínima e máxima possíveis. Caso o mês conste no PGN, o intervalo
pode se estreitar. Indicadores de Elo, evento e TimeControl são
preservados **somente quando existem no cabeçalho real**.

## Contrato explícito de imitação supervisionada

Cada JSONL possui policy_input (contexto anterior à decisão, como FEN,
jogador e idade disponível) e policy_target (move_uci realmente escolhido).
**Somente essas duas estruturas** devem ser passadas ao ajuste supervisionado
de uma política comportamental. Os campos adicionais no registro, como
result_for_player, ECO, Elo, evento, hashes e adversário, têm papel de
auditoria, estratificação e análise; não são atributos do policy_input.
Em especial, resultado final e ECO anotado após a partida são informação
futura e nunca devem ser utilizados como entradas para predizer aquele lance.
