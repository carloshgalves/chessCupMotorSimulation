# Política contextual v3 — imitação histórica real e inferência pareada

Esta etapa responde à limitação demonstrada na PR #137: um modelo linear
de características quase isoladas conseguiu reduzir a perda de treino,
mas não aprendeu identidades históricas robustas em validação. A v3
implementa um **novo modelo**; a v2 continua funcionando, com schema e
código de inferência preservados.

## Fonte e treinamento

- Fonte: arquivos **reais** Fischer.pgn, Tal.pgn, Karpov.pgn e Kasparov.pgn,
  obtidos de um snapshot PGN Mentor espelhado em commit fixado; sem PGNs
  artificiais no treino ou nas evidências de CI.
- A PR #136 já extraiu *somente* exemplos train-only com
  policy_input (FEN antes do lance, jogador, ano, período observado)
  e policy_target (UCI do lance efetivamente jogado).
- V3 escolhe amostras determinísticas por SHA-256 de jogo e ply,
  com teto de decisões por partida, e aprende primeiro uma política
  **genérica** sobre os dados combinados.
- A seguir aprende a política **individual** regularizada em torno
  do baseline genérico e políticas por **período de carreira observado**
  regularizadas em torno da política individual. Períodos com poucos
  exemplos não recebem pesos fictícios.
- Otimizador AdaGrad com perda de **log-verossimilhança condicional**:
  cada UCI observado concorre com todas as jogadas legais da FEN.
  Somente informações presentes na posição ANTES da escolha e efeitos
  *hipotéticos* de cada lance legal entram nas features.
- Novas interações candidato×estado: fase pelo material, material
  relativo, mobilidade, pressão na zona do próprio rei, peões centrais,
  xeque, captura, peça ameaçada antes/depois e risco no destino.
  São *proxies* explícitos, não análise de motor nem classificação
  clínica de estilo.
- Modelos v2/v3 recebem os MESMOS jogos e o MESMO algoritmo de amostra.
  Cada JSON guarda hashes dos arquivos de treino, manifesto e PGNs
  originais. Novas entradas não sobrescrevem modelos existentes.

## Execução local no Debian

    cd ~/Projetos/GitHub/chessCupMotorSimulation
    source .venv/bin/activate
    python -m pip install -e '.[dev]'
    mkdir -p "$HOME/chesssim-homologacao/contextual"
    python -m chesssim.real_policy_experiment train \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --max-per-player 1200 --max-per-game 3 --seed 2026 \
      --out "$HOME/chesssim-homologacao/contextual/v2.json"
    python -m chesssim.contextual_policy_v3 train \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --max-per-player 1200 --max-per-game 3 --seed 2026 \
      --out "$HOME/chesssim-homologacao/contextual/v3.json"

A v3 pode ser usada com o mesmo adaptador da PR #138, sem mudança dos
parâmetros internos do Rodent:

    python -m chesssim.cli play fischer tal \
      --rodent "$RODENT_IV_HOME" --no-books --depth 4 \
      --max-plies 120 --reverse \
      --policy-model "$HOME/chesssim-homologacao/contextual/v3.json" \
      --policy-white-era overall --policy-black-era overall \
      --policy-override-percent 30 --policy-min-ply 17 \
      --out "$HOME/chesssim-homologacao/contextual/duelos"

Esta substituição externa continua experimental. Se a v3 aprender
preferências piores, poderá produzir partidas piores. Não promove
personalidades oficiais.

## Comparação científica fora do treino

A avaliação lê novamente os PGNs reais pinados e verifica SHA-256,
partições de anos, trajetórias e FENs de treino. Faz **somente
validação**, não expõe a opção holdout, que permanece sem consulta.

    python -m chesssim.contextual_policy_v3 evaluate \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --model "$HOME/chesssim-homologacao/contextual/v3.json" \
      --legacy-model "$HOME/chesssim-homologacao/contextual/v2.json" \
      --fetch --per-player 80 --per-game 2 --seed 2027 \
      --repetitions 1500 \
      --rodent-root "$RODENT_IV_HOME" \
      --rodent-depth 2 --rodent-seconds 0.12 \
      --out "$HOME/chesssim-homologacao/contextual/validacao-v3.json"

**Mesma amostra / mesmos tabuleiros por jogador**, analisando:
v3 perfil correto; v3 genérico; v2 perfil correto; v2 genérico;
v3 perfis de jogadores trocados; Rodent IV correto; Rodent IV Simple;
e Rodent IV perfis trocados.

Acurácia top-1 e média macro por partida são descritivas. A inferência
pareada calcula, para cada partida histórica, a **diferença média de
acertos**, e então:
- Bootstrap de 95% por reamostragem de *partidas inteiras*, não de
  posições individuais.
- Teste bicaudal de inversão de sinais **por partida**, com
  replicações e semente determinísticas.

P-valores são exploratórios, sem correção para múltiplos testes; os
mesmos dados de validação já orientaram versões anteriores. O melhor
perfil trocado é escolhido na própria validação: serve de controle
descritivo adversarial, não de teste nulo calibrado. Histórias podem
compartilhar adversários, época e aberturas. Portanto, **não inferir
fidelidade histórica nem força por pequeno ganho de acerto**.

A CI real-contextual-v3.yml treina v2 e v3 de PGNs públicos reais,
compila o Rodent IV **original** pinado, aplica ambos aos mesmos
jogos reservados e publica modelo/relatório agregados (sem PGNs
históricos brutos). O teste final continua reservado para
pré-registro, validação independente e gate humano.
