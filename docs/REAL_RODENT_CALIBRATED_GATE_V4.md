# Frente científica V4: curva de aprendizagem real e gate calibrado no Rodent IV

## Objetivo e limites

Não se exige reprodução perfeita de decisões históricas: as fontes são
desiguais e os estilos mudam com a carreira. Já constatamos que V3 (104/480)
supera V2 (77/480), mas não demonstra individualidade (controle genérico:
109/480) e fica atrás do Rodent IV real (197/480). A V4 mede se acrescentar
dados ajuda e testa uma estratégia diferente de substituir lances indiscriminadamente.

**Dados científicos exclusivamente reais:** PGNs de Fischer, Tal, Karpov e
Kasparov, extraídos do snapshot pinado e reconstruídos pelo módulo
real_training_cohorts. Testes com PGNs artificiais são SOMENTE fixtures
de engenharia; o workflow real baixa, processa, treina e avalia fontes
públicas genuínas.

## Métodos

1. Curva de aprendizagem com 400, 800 e 1.600 decisões reais por jogador,
   seleção determinística por hash e limite por partida. As três versões
   contextual-v3 são confrontadas nos MESMOS jogos de validação; as fontes
   completas e arquivos de treino são comprometidos com SHA-256.
2. Rodent IV torna-se o motor de decisão primário. A V3 propõe outro lance.
   O escore de divergência é: diferença da preferência V3 pelo lance
   alternativo sobre o bestmove Rodent, menos a mesma preferência do perfil
   genérico. Esse estilo residual é só uma hipótese, não garantia tática.
3. O módulo reconstrói exatamente todos os jogos usados no treino da V3.
   **Proíbe globalmente** que esses jogos ou posições FEN participem da
   calibração do novo gate. Amostra 128 novas decisões por jogador de
   partidas reais do braço TRAIN que não participaram da V3.
4. Divide por JOGO as decisões disponíveis em duas amostras não sobrepostas:
   ajuste do limiar e auditoria. Limiares predefinidos: 0, 0.25, 0.5,
   1.0, 2.0, 4.0, 8.0. Treina maximizando média de ganho top-1 por
   jogo menos penalidade 0.01 pela fração de intervenções. A seguir,
   aceita a intervenção somente se, em jogos diferentes, a banda
   bootstrap de 95% para vantagem sobre o Rodent for inteiramente positiva.
   **Caso contrário, threshold=null: nunca substituir o Rodent**.
5. Valida o gate na partição de ANOS de validação (não holdout), nas
   mesmas 120 posições por jogador, com testes pareados por jogo.
   Também registra a curva de aprendizagem sobre essas mesmas FENs.
   O treino do gate não consulta a validação. Teste final permanece lacrado.

Os intervalos por jogo e p-valores continuam EXPLORATÓRIOS:
o corpus foi consultado em várias iterações anteriores; a auditoria do
gate ocorre ainda no braço TRAIN, não em teste final independente.

O experimento treina um seletor EXTERNO de lances. Não modifica os
parâmetros internos, livros ou personalidades TXT do Rodent IV; não
constitui prova de Elo, força tática, ou identidade psicológica.

## Execução no Debian

Requisitos: Python e Rodent IV Linux instalados, além da pasta de coortes
reais da PR #136. A variável RODENT_IV_HOME deve apontar para a pasta
do executável nativo rodent-iv-plain.exe, personalidades e livros.

    cd ~/Projetos/GitHub/chessCupMotorSimulation
    source .venv/bin/activate
    python -m pip install -e '.[dev]'
    mkdir -p "$HOME/chesssim-homologacao/v4"

Gerar modelo V3 na sua própria máquina (reutilize o seu JSON íntegro
se os parâmetros e fontes coincidirem):

    python -m chesssim.contextual_policy_v3 train \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --max-per-player 1600 --max-per-game 3 --seed 2026 \
      --out "$HOME/chesssim-homologacao/v4/v3-1600.json"

Calibrar o gate utilizando o Rodent IV REAL nessa mesma máquina:

    python -m chesssim.real_gate_v4 fit \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --model "$HOME/chesssim-homologacao/v4/v3-1600.json" \
      --rodent-root "$RODENT_IV_HOME" \
      --per-player 128 --per-game 2 --seed 2028 \
      --rodent-depth 2 --rodent-seconds 0.12 \
      --out "$HOME/chesssim-homologacao/v4/gate.json"

Para avaliar a curva, treine V3 extras de 400 e 800 decisões/jogador
com os mesmos parâmetros (exceto max-per-player) e execute:

    python -m chesssim.real_gate_v4 evaluate \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --model "$HOME/chesssim-homologacao/v4/v3-1600.json" \
      --gate "$HOME/chesssim-homologacao/v4/gate.json" \
      --rodent-root "$RODENT_IV_HOME" --fetch \
      --curve "400:$HOME/chesssim-homologacao/v4/v3-400.json" \
      --curve "800:$HOME/chesssim-homologacao/v4/v3-800.json" \
      --per-player 120 --per-game 2 --seed 2027 \
      --rodent-depth 2 --rodent-seconds 0.12 \
      --out "$HOME/chesssim-homologacao/v4/validation.json"

Jogar com a regra adicional, SOMENTE no modo experimental:

    python -m chesssim.cli play fischer tal \
      --rodent "$RODENT_IV_HOME" --no-books --depth 2 \
      --policy-model "$HOME/chesssim-homologacao/v4/v3-1600.json" \
      --policy-gate "$HOME/chesssim-homologacao/v4/gate.json" \
      --policy-white-era overall --policy-black-era overall \
      --policy-override-percent 100 --policy-min-ply 17 \
      --max-plies 120 --out "$HOME/chesssim-homologacao/v4/duelos"

Importante: 100% autoriza o gate a examinar todas as oportunidades; NÃO
significa forçar substituições. Quando o gate rejeita um jogador, o
Rodent mantém 100% dos bestmoves. Não há aprendizado online.

Os hashes do binário e dos arquivos de personalidade são vinculados
ao gate. Um gate calibrado no GitHub Actions pode ser incompatível com
o binário no Debian, mesmo compilado da mesma fonte: nesse caso,
calibre um novo gate local. O programa recusa divergência ANTES de
criar uma tentativa de duelo.

## Registro e critérios de progressão

Arquivos: modelos reais JSON 400/800/1600, gate JSON e relatório de
validação com hashes, n por jogo, ano/período, ganho e intervalos
pareados, mais PGN gerado pelo Rodent IV na integração final.
O relatório marca explicitamente final_holdout_opened=false e
proíbe promoção da personalidade como historicamente validada.

A curva é um diagnóstico para decidir se mais PGNs estão agregando
informação. Se o aprendizado estabilizar ou o gate não for aceito,
a próxima hipótese científica será uma representação mais adequada
à avaliação enxadrística, e NÃO maior assertividade retórica.
