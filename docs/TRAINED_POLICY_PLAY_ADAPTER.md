# Adaptador experimental — política histórica aprendida + partidas Rodent IV

Esta funcionalidade conecta **modelos efetivamente treinados com partidas
humanas reais** à execução UCI do Rodent IV. O motor Rodent calcula normalmente
seu bestmove; o adaptador pode substituí-lo em uma fração determinística
dos lances elegíveis. Portanto o **jogo efetivamente muda**, mas os
parâmetros internos de personalidade do Rodent **não são treinados, alterados,
sobrescritos ou persistidos**.

O modo é **experimental**, desativado por padrão e bloqueado para partidas
oficiais, presets com relógios e livros de abertura. Nenhuma alegação
de fidelidade histórica é permitida. Os pesos das políticas atuais não
foram cientificamente aprovados como personalidades fiéis.

## Pré-requisitos no Debian

1. Rodent IV instalado com executável na pasta fornecida em --rodent,
   exatamente rodent-iv-plain.exe, e pastas personalities/ e books/;
   os aliases Fischer e Tal devem estar registrados em
   personalities/basic.ini. No Linux o executável pode ter sufixo .exe,
   desde que seja um ELF nativo (compilado para Linux e executável).
2. Modelo JSON treinado localmente pela PR #137 ou obtido do artefato de
   GitHub Actions real. A assinatura model_sha256 é verificada na carga.
3. Brancas e pretas devem estar entre os jogadores presentes no modelo
   e entre os perfis Rodent IV com alias registrado.
4. É necessário especificar explicitamente o percentual de intervenção
   e os períodos escolhidos; não inferir idade ou período apenas pelo nome.

## Treinar em suas próprias pastas com os mesmos PGNs reais

    cd ~/Projetos/GitHub/chessCupMotorSimulation
    source .venv/bin/activate
    python -m pip install -e '.[dev]'
    mkdir -p "$HOME/chesssim-homologacao/politicas"
    python -m chesssim.real_policy_experiment train \
      --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
      --max-per-player 1200 --max-per-game 3 --seed 2026 \
      --out "$HOME/chesssim-homologacao/politicas/modelo-01.json"

A pasta --cohorts deve conter as coortes **reais** geradas antes, incluindo
manifest.json e train/<slug>/<ano>.jsonl. O comando de treino recusa a
sobrescrita do arquivo existente. O GitHub Actions também gera um modelo
com amostra real menor, usado no smoke de execução UCI.

## 1. Auditoria sem interferência (shadow mode)

    python -m chesssim.cli play fischer tal \
      --rodent "$RODENT_IV_HOME" --no-books \
      --depth 4 --max-plies 100 \
      --policy-model "$HOME/chesssim-homologacao/politicas/modelo-01.json" \
      --policy-white-era overall --policy-black-era overall \
      --policy-override-percent 0 --policy-min-ply 17 \
      --out "$HOME/chesssim-homologacao/duelos-shadow"

O Rodent escolhe e joga todos os lances, normalmente. O adaptador apenas
registra o lance que a política sugeriria no mesmo tabuleiro, sem
alterar a partida. Útil para comparar decisões e confirmar compatibilidade.

## 2. Aplicar o comportamento aprendido (opt-in)

    python -m chesssim.cli play fischer tal \
      --rodent "$RODENT_IV_HOME" --no-books \
      --depth 4 --max-plies 120 --reverse \
      --policy-model "$HOME/chesssim-homologacao/politicas/modelo-01.json" \
      --policy-white-era early_observed \
      --policy-black-era late_observed \
      --policy-override-percent 30 --policy-min-ply 17 \
      --policy-seed 2026 \
      --out "$HOME/chesssim-homologacao/duelos-politica"

Nesse exemplo a política de Fischer, no terço inicial observado da
carreira, tem probabilidade determinística de ser usada em 30% dos
seus lances elegíveis; Tal usa o terço tardio. Quando as cores
se invertem por --reverse, o vínculo continua por **slug do jogador**,
não por branca/preta. O período deve ter sido efetivamente treinado
e constar no JSON; caso contrário, a execução é recusada.

Percentuais: 0 = apenas auditoria, 100 = toda decisão elegível vira
proposta da política (mesmo que isso enfraqueça muito o jogo); valores
intermediários substituem uma fração dos lances calculados pelo motor.
Se a política e o Rodent escolherem a mesma jogada, não há mudança.

O controle é baseado em SHA-256 de (seed, rodada, jogador, ply, FEN).
A mesma configuração reproduz as **decisões de seleção** para a mesma
posição, não necessariamente a partida inteira: o próprio UCI pode
diferir com hardware, tempo, threads e busca. O adaptador não constrói
books nem altera a base de dados.

## Evidência e limitações

Cada tentativa de duelo contém summary.json, jogo_N.pgn e duelo.pgn.
A seção experimental_learned_policy guarda hashes do modelo e da fonte,
o mecanismo, períodos e o percentual; os PGNs guardam cabeçalhos
PolicyModelSHA256, PolicyMechanism, PolicyWhiteEra/PolicyBlackEra,
PolicyOverridePercent, PolicyOfficial=false. Toda escolha aceita fica
registrada em games[].policy_trace: FEN SHA, sugestão do Rodent, sugestão
da política, escolha realmente jogada, decisão de amostragem e motivo.
O PGN recebe comentário nos lances efetivamente substituídos.
games[].policy_counts informa totais por jogador.

Os parâmetros Rodent IV originais e o arquivo JSON treinado não são
modificados. Se não houver modelo, --no-books e --policy-* deixam de
afetar o fluxo; o modo padrão continua idêntico ao anterior.

O workflow real-policy-play-adapter.yml recompila o motor oficial
pinado e demonstra três condições com **motor UCI real**:
rodar sem modelo (controle), shadow 0% e intervenção 100%, incluindo
inversão de cores. Compara a sequência UCI do PGN aos eventos do
summary, exige intervenções efetivas e publica modelo/partidas
**geradas**; não publica os PGNs históricos de terceiros.

## Ciência

A intervenção é um mecanismo de controle comportamental **externo**,
não uma alteração dos valores de avaliação do motor Rodent. Os modelos
da PR #137 não tiveram vantagem de identidade historicamente
demonstrada sobre baselines em validação. Para comparar estilos
cientificamente, controle abertura, cor, profundidade e hardware,
use partidas pareadas, mais de uma seed, medidas de diversidade e
métricas por partida; não alegue validade a partir de um único duelo.
