# Estudo V5 — política histórica aprendida de avaliações e variantes reais do Rodent IV

## Hipótese

A V3 (atributos manuais do tabuleiro) melhorou a previsão geral, sem identificar de modo robusto o jogador correto. A V4 demonstrou que acrescentar exemplos em 400→800→1600 melhorou o ponto estimado, mas sem ganho incremental estabelecido por intervalos de confiança. O objetivo da V5 é **mudar a representação**, obtendo do próprio Rodent IV avaliações, profundidades e variantes `MultiPV`.

O motor UCI de verdade é compilado do commit público `nescitus/rodent-iv@e8d84c8c8c189a1cf4eb27c578fc573af3b916d2`. Sua opção `MultiPV` anuncia múltiplas linhas reais e scores. Não existe substituição silenciosa por atributos fictícios ou motores de teste no experimento científico.

## Experimento pré-especificado

- PGNs reais Fischer, Karpov, Kasparov e Tal, reconstruídos a partir da fonte pinada, com SHA-256 e divisão por ANOS inteiros. Apenas TRAIN fornece os lances humanos usados para ajuste.
- O modelo V3 de controle é treinado novamente em 1600 decisões por jogador, unicamente de TRAIN.
- Treino V5: 128 decisões históricas por jogador (no máximo duas de uma partida); configuração Rodent UCI nomeado, sem livros, MultiPV=4, depth=3.
- Validação: 48 posições por jogador de anos VALIDATION. Remove posições que sejam iguais aos estados canônicos vistos em QUALQUER decisão do braço TRAIN — inclusive transposições com contadores FEN diferentes. O teste final HOLDOUT não é acessível na V5.
- O motor oferece as quatro melhores variantes, antes de consultar o lance humano. O target histórico **nunca é injetado** na lista de candidatos. Quando ausente do MultiPV, o exemplo não participa da perda de treinamento condicional e conta como **erro incondicional** de qualquer ranker restrito às variantes na validação. A taxa de cobertura é sempre publicada.
- Recursos numéricos: score em centipeões, distância do primeiro score, rank MultiPV, comprimento da variante, resposta de captura/xeque, contagem de capturas/xeques ao longo da PV e mate anunciado pelo motor. Nada depende do resultado futuro da partida ou da identidade da resposta histórica.
- Treinamento estatístico: regressão logística condicional, primeiro pesos genéricos e depois pesos individuais regularizados em torno dos genéricos, otimizados por AdaGrad.
- Controles nas **mesmas FENs**: melhor linha do Rodent em modo MultiPV; V5 genérico; V5 jogador correto; V5 perfil de jogador trocado por rotação fixa; V3 nominal e V3 genérico. Intervalos bootstrap 95% e sinais pareados POR PARTIDA, não por ply tratado como independente.

Os mesmos anos de validação já foram consultados em PRs anteriores, portanto os intervalos e p-values são **exploratórios** e não constituem comprovação de identidade ou ganho de Elo.

## Reprodução no Debian

A pasta de coortes e o modelo V3 devem provir do mesmo manifesto e snapshot histórico. `RODENT_IV_HOME` deve apontar para o binário e a pasta de personalidades do Rodent IV nativo.

```bash
cd ~/Projetos/GitHub/chessCupMotorSimulation
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m chesssim.engine_pv_policy_v5 train \
  --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
  --v3-model "$HOME/chesssim-homologacao/v4/v3-1600.json" \
  --rodent-root "$RODENT_IV_HOME" \
  --per-player 128 --per-game 2 --seed 2031 \
  --multipv 4 --depth 3 \
  --out "$HOME/chesssim-homologacao/v5-model.json"

python -m chesssim.engine_pv_policy_v5 evaluate \
  --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
  --v3-model "$HOME/chesssim-homologacao/v4/v3-1600.json" \
  --model "$HOME/chesssim-homologacao/v5-model.json" \
  --rodent-root "$RODENT_IV_HOME" --fetch \
  --per-player 48 --per-game 2 --seed 2032 \
  --out "$HOME/chesssim-homologacao/v5-validation.json"
```

A V5 exige hashes idênticos do modelo V3, manifesto de partição, arquivos de treino, binário Rodent e personalidades na avaliação. Os arquivos de resultado não são sobrescritos. É necessário recalibrar ao mudar o executável nativo do Rodent.

## Escopo da política

O artefato V5 é uma política supervisionada **aprendida** de variantes reais; não muda pesos internos do Rodent IV nem arquivos TXT. Enquanto os controles de cobertura, individualidade e segurança tática não aprovarem intervenções, não existe promoção automática para partidas oficiais. O fato de estar limitado às 4 linhas MultiPV é registrado como restrição científica, e um ganho apenas condicional não pode ser apresentado como melhoria incondicional.

O workflow de GitHub Actions recompila o motor real, lê novamente os PGNs públicos, ajusta a V3, treina a V5, calcula indicadores e publica apenas modelos/relatórios agregados. Fixtures sintéticas são usadas somente em testes de implementação.


## Primeira execução real e revisão adversarial — 14/11/2026

A primeira CI real completou treinamento, análise UCI MultiPV, validação
histórica e inferência estatística (run 43612021556): 512 posições de
TRAIN de 4 jogadores; lance humano presente no top-4 em 376/512.
Na validação de 192 decisões históricas, o lance humano esteve no top-4
em 128/192 (**66,7%**). O melhor lance do Rodent em modo MultiPV acertou
76/192 (**39,6%**).

**Finding científico, não ocultado:** os pesos gerais e individuais
aprendidos inicialmente não mudaram nenhum dos 192 movimentos escolhidos
pelo Rodent (76/192 em todos os perfis), mesmo com perda de treino em
queda. Portanto, a V5 vinda apenas de scores/variantes **ainda não
demonstrou estilo individual**, e não deve ser apresentada como uma
melhoria do motor.

O resultado motivou uma **ablação diagnóstica pós-hoc** que pontua cada
linha pela diferença entre os pesos pessoais e genéricos, retirando a
preferência direta pelo score do Rodent. A ablação é publicada como
`trained_residual_named` e `trained_residual_fixed_swapped`, com
diferenças de decisões frente ao engine e estatísticas por jogo. Isso
NÃO equivale a treinar ou validar mais uma política autoritativa:
não ajusta hiperparâmetros em função dos anos de validação e não
libera nenhum override. Se as preferências residuais perderem acurácia,
isso é igualmente evidência para a seleção da próxima estratégia.

A primeira revisão estática encontrou um import de SHA-256 não usado,
que foi retirado. O teste científico real foi aprovado na primeira
execução; os novos testes e o workflow v5 são reexecutados no
HEAD final após a ablação.
