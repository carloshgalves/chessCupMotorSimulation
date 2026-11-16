# Maia3-79M — treinamento neural individual com quatro históricos

## Entrega técnica

A V7 já ajustou 122.847 lances históricos, mas somente com **adaptadores estatísticos de ranking**: o backbone Maia3-79M original não recebeu gradientes. Este novo experimento executa **treinamento neural real** com retropropagação para quatro LoRAs Q/K distintas, uma para Fischer, Tal, Karpov e Kasparov. O backbone 79M e a política de origem são congelados, e nenhum perfil oficial Rodent IV é alterado.

## Modelo pinado e corpus

- Código Maia-3 original: `CSSLab/maia3@1e13597c42d4858b7cfd7cfdae01e297263364b2`.
- Checkpoint `UofTCSSLab/Maia3-79M`: revisão `a107d6ceb7b298cb04ae1da4edffe2939858b894`, SHA256 `3fc6181d5db789b45a15305732148757ae74efa3e0028e81ba335b462dac45c2`.
- PGNs reais pinados e coortes de **anos TRAIN** `career-stratified`, sem uso de exemplos `VALIDATION` nem `HOLDOUT`.
- Dentro de TRAIN, sub-braços FIT / TUNE / AUDIT por hash determinístico da trajetória completa, com embargo de posições canônicas entre braços e de partidas compartilhadas Karpov-Kasparov.

O executor reconstrói o histórico PGN de cada posição selecionada; não alimenta o lance alvo no histórico. A política original é executada em todas as ações legais (vocabulário Maia de 4.352 ações, incluindo promoção e roque). As representações finais do Transformer e suas projeções originais Q/K são extraídas **sem gradiente no backbone**. LoRA rank 4 adapta Q/K e o viés de promoção que depende de K; a loss é entropia cruzada da decisão histórica, KL de preservação do prior e regularização em relação à LoRA genérica. Primeiro aprende uma LoRA genérica com exemplos dos quatro; depois inicializa e treina individualmente quatro LoRAs nomeadas.

## Piloto CPU executável

A CI permite um teste funcional real com, por jogador, 12 partidas FIT × 2 decisões, 4 partidas TUNE × 2 e 4 partidas AUDIT × 2. São **80 partidas distintas e 160 decisões** no total, após seleção determinística. O orçamento é um piloto para verificar que treinamento completo e comparações funcionam, não a alegação de treinamento exaustivo dos 122.847 lances da V7. O tamanho pode ser ampliado até 128 partidas por braço e quatro decisões por partida, explicitamente como **nova experiência exploratória**.

Exemplo, depois de instalar o código original Maia do commit pinado e PyTorch para CPU:

```bash
python -m chesssim.real_training_cohorts --fetch \
  --players fischer tal karpov kasparov \
  --birthdates-json docs/REAL_BIRTHDATES_4.json \
  --split-policy career-stratified --min-ply 17 \
  --out /tmp/maia-neural-cohorts

python -m chesssim.contextual_policy_v3 train \
  --cohorts /tmp/maia-neural-cohorts --max-per-player 1600 \
  --max-per-game 3 --seed 2026 --out /tmp/maia-neural-v3.json

python -m chesssim.maia79_neural_four \
  --cohorts /tmp/maia-neural-cohorts --v3-model /tmp/maia-neural-v3.json \
  --fit-games 12 --tune-games 4 --audit-games 4 --moves-per-game 2 \
  --out /tmp/maia-four-personal-qk.json \
  --report /tmp/maia-four-neural-report.json
```

O relatório compara Maia original, LoRA genérica, LoRA pessoal correta e LoRA pessoal com identidade trocada nas **mesmas** posições internas TUNE/AUDIT. Arquivos exportados contêm somente deltas treináveis e evidências agregadas: nunca PGNs brutos nem pesos originais do Maia.

## Limites e próximos incrementos

- Sem autenticidade do inventário completo do pré-treino Maia, a ressalva `UNVERIFIABLE_PRETRAIN_OVERLAP` permanece — não bloqueia a execução supervisionada.
- Os resultados de TUNE e AUDIT dentro de TRAIN são diagnósticos de desenvolvimento, não validação independente; aumentar amostra depois de ler AUDIT não transforma a mesma amostra em novo teste confirmatório.
- A reconciliação do histórico reabre o arquivo PGN público para **localizar apenas jogos TRAIN selecionados**. Não executa inferência, cálculo de loss ou atualização de parâmetros para quaisquer partidas VALIDATION/HOLDOUT.
- O pipeline não homologa nenhuma personalidade, não altera Rodent IV e não abre HOLDOUT final; qualquer avanço para competição demanda teste tático e gate humano posterior.
- A verdadeira ampliação para milhares de jogos por jogador exige avaliação de custo CPU/tempo, cache seguro de latentes, checkpoints retomáveis e avaliação jogo-pareada. Não alegar que já ocorreu.
