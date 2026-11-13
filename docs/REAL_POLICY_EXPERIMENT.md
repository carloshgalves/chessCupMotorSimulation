# Pesquisa de políticas de escolha de lances com dados históricos reais

## Objetivo e método

A PR #136 produziu train/<slug>/<ano>.jsonl de decisões humanas reais,
separadas de anos reservados. Este módulo ajusta uma **política estatística
não neural** por jogador e período observado de carreira, com correção para
as jogadas que eram *legais e disponíveis* na posição. Uma jogada popular
porque era a única legal não é confundida com preferência estilística.

Features são calculadas apenas de FEN pré-lance e jogadas legais:
tipo da peça, origem/destino, captura, xeque, roque, promoção e padrões de
avanço. A frequência observada é comparada com a exposição esperada sob
seleção uniforme das jogadas legais. Pesos logarítmicos recebem suavização
e limites para dados escassos. O baseline genérico é ajustado às MESMAS
partidas reais, porém sem identidade do jogador. Cada perfil acrescenta
componentes por jogador e terço de anos observados.

Este é um modelo leve, interpretável e auditável. NÃO é prova de que o
motor de busca reproduz a força ou o estilo histórico. É a primeira
política treinada efetivamente com policy_input e policy_target reais.

## No Debian, usando a base já preparada

    cd ~/Projetos/GitHub/chessCupMotorSimulation
    source .venv/bin/activate
    mkdir -p "$HOME/chesssim-homologacao/politicas"
    python -m chesssim.real_policy_experiment train \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --max-per-player 1200 --max-per-game 3 --seed 2026 \
      --out "$HOME/chesssim-homologacao/politicas/modelo-01.json"

Por padrão, seleção minhash determinística sobre os exemplos de treino,
com no máximo três decisões de cada jogo. A ferramenta permite valores
maiores; o CI usa amostra real limitada por orçamento de runner. O
modelo registra SHA-256 da base, parâmetros, perfis e contagens.

Avaliar em anos de validação, com os PGNs públicos reais novamente
baixados e conferidos por SHA-256 contra o manifesto de treino:

    python -m chesssim.real_policy_experiment evaluate \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --model "$HOME/chesssim-homologacao/politicas/modelo-01.json" \
      --fetch --arm validation --per-player 40 --per-game 2 \
      --out "$HOME/chesssim-homologacao/politicas/validacao-01.json"

O mesmo conjunto de posições é submetido à política genérica,
ao perfil designado e aos demais perfis aprendidos (controle de rótulo
incorreto). O relatório conserva top-1 e macro por partida, não apenas
por meio-lance; hashes das sequências de decisões permitem auditar
determinismo sem publicar exemplos reservados. O split é reconstruído
dos bytes PGN reais, em vez de aceitar uma tag declarada arbitrariamente.
Trajetórias e FENs presentes nos exemplos de treino não entram na
avaliação.

## Rodent IV REAL, e não substitutos

O comando pode executar o mesmo conjunto com a instalação oficial:

    python -m chesssim.real_policy_experiment evaluate \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --model "$HOME/chesssim-homologacao/politicas/modelo-01.json" \
      --fetch --arm validation --per-player 40 --per-game 2 \
      --rodent-root "$RODENT_IV_HOME" \
      --rodent-depth 2 --rodent-seconds 0.15 \
      --out "$HOME/chesssim-homologacao/politicas/rodent-01.json"

Usa a integração existente com a opção UCI "Personality": Fischer,
Tal, Karpov e Kasparov, e abre outro processo Rodent sem personalidade
histórica explícita como controle genérico UCI. Todos recebem exatamente
as mesmas posições e limites. UseBook=False; grava SHA-256 do motor e
dos arquivos TXT das personalidades. Ausência/erro do executável
interrompe o teste; NUNCA substitui automaticamente por jogadas fictícias.

O workflow real-policy-rodent.yml também baixa o snapshot histórico,
ajusta os modelos usando dados reais, recompila o **Rodent IV original**
do commit e8d84c8c8c189a1cf4eb27c578fc573af3b916d2, confere fontes
e exige respostas UCI efetivas; seus artefatos são agregados, não PGNs.

## Avaliação e limites

- Métrica primária: acerto exato do lance escolhido por humanos,
  por jogador-alvo e modelo, sempre na MESMA posição.
- Métrica secundária: média macro por partida, para evitar que uma
  partida com muitos lances domine o resultado.
- Comparar a diagonal (perfil correto) contra perfis trocados e
  baseline genérico. Vantagem aparente exige replicação por jogo,
  intervalo de incerteza e teste cego (#12) antes de alegar identidade.
- O período "career-stratified" avalia interpolação entre anos de
  carreira. O protocolo "chronological" estima generalização temporal.
  Nenhum dos dois usa o holdout final na execução normal.
- O teste final (arm=holdout) exige --acknowledge-holdout explícito.
  Não realizar ajuste após consulta ao holdout final.
- O manifesto da PR #136 exclui posições reservadas por FEN completa,
  mas não toda similaridade estratégica/abertura. O holdout histórico
  foi lido para indexação e **não está fisicamente lacrado**.
- A estimativa de estilo pode sofrer confusão por abertura,
  adversário, época, amostra e força do motor. Rodent é comparação
  de decisões, não alvo de treinamento neste incremento.
- O artefato é experimental: nenhum resultado promove
  automaticamente personalidade à Copa dos Imortais.

Referências: PGN Mentor, https://www.pgnmentor.com/files.html;
Rodent IV, https://github.com/nescitus/rodent-iv;
UCI python-chess, https://python-chess.readthedocs.io/en/stable/engine.html.


## Auditoria do primeiro piloto UCI real

A execução inicial com 250 exemplos reais de treino por jogador e apenas
10 posições históricas por alvo revelou subajuste e falta de
distinguibilidade: as políticas aprendidas concordaram em 4/40
jogadas humanas; o Rodent de personalidade correspondente, 15/40.
O controle genérico de dados também foi 4/40. Não se conclui
superioridade ou fidelidade por amostra tão pequena.

A segunda rodada aumenta a amostra real para 1.200 exemplos por
jogador, 24 posições de validação por alvo e reforça o condicionamento
por personalidade e período de carreira, mantendo o protocolo de
seleção pré-declarado. Adicionalmente, o baseline UCI agora
ativa explicitamente a personalidade oficial **Simple** do Rodent IV
e registra seu SHA-256; não depende de personalidade padrão incerta.

O modelo é vinculado ao SHA-256 de **cada arquivo JSONL de treino**,
além do manifesto. Alteração posterior dos dados invalida a avaliação.
