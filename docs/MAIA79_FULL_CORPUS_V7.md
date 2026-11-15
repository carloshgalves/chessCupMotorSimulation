# V7 — Maia-3 79M, aprendizado individual com TODAS as decisões reais elegíveis

## Decisão de arquitetura

A versão V6 usou Maia3-5M (cinco milhões de parâmetros), a variante
mais barata em CPU. Não era a variante de maior acerto publicada.
No benchmark original de Chessformer (ICLR 2026), as variantes 5M,
23M e 79M alcançaram 55,4%, 56,6% e 57,1%, respectivamente.
A V7 usa **o checkpoint original Maia3-79M**, uma rede aproximadamente
16 vezes maior, mas mantém os seus parâmetros pré-treinados congelados.

A V7 **não afirma fine-tuning da rede inteira**: o aprendizado
específico de cada jogador é um adaptador estatístico de *ranking*
sobre candidatos MultiPV do Maia-3 79M. Essa camada pode ser ajustada
com centenas de milhares de decisões em CPU, preservando a potência
do backbone e evitando um novo treino dispendioso de 79M parâmetros.

Outros modelos, por exemplo o DSC-150M de terceiros, relatam melhor
acurácia em corpus OTB contemporâneo, mas ainda precisam ser
comparados num conjunto histórico equivalente, com fontes e licenças
auditadas. O Maia 79M é o baseline oficial maior dentro da família
cujo protocolo já conseguimos reproduzir.

## Onde estavam os 96 exemplos anteriores

O piloto V6 avaliou **96 DECISÕES** vindas de **95 PARTIDAS**
históricas distintas, e não 96 partidas completas. Foram 24
lances por jogador e quatro jogadores. O resultado de Maia3-5M
cenário 2400 foi 50/96 (52,1%), contra 32/96 do Rodent.
Não generalizamos essas porcentagens para 64 pessoas ou para todo
o histórico de um jogador.

## V7 — treino sem limite artificial por partida

O snapshot público pinado registra aproximadamente 827 jogos de
Fischer, 2.431 de Tal, 3.529 de Karpov e 2.128 de Kasparov.
A reconstrução pode rejeitar jogos inválidos, sem data conhecida
ou com identidade incerta. **Isso não significa 4.000 jogos
comprovadamente utilizáveis para cada jogador.**

A partição anterior permanece: anos completos separados em
TRAIN / VALIDATION / HOLDOUT. A V7 percorre **TODOS os JSONL reais
do braço TRAIN**, verificando manifesto, hashes, lances legais e
suas identidades de partida. Dentro de TRAIN, o SHA-256 da trajetória
classifica cada partida em um dos 10 buckets:
- 0–7: **FIT**, todos os lances do jogador a partir do 21º meio-lance,
  sem subsample nem limite por jogo;
- 8: **TUNE**, jogos diferentes, para escolher intensidade do
  adaptador entre 0; 0,5; 1; 2; 4;
- 9: **AUDIT**, outros jogos, sem coincidências de posição canônica
  com exemplos selecionados para ajuste de intensidade.

O FIT **não acessa os lances históricos dos jogos TUNE, AUDIT,
VALIDATION ou HOLDOUT**. Eles continuam participando da pesquisa
como calibração e verificação, mas não são treinados. Essa é a
maneira correta de usar todo o corpus disponível sem medir fidelidade
numa amostra que o modelo já viu.

O adaptador aprende preferências de jogadas por oportunidade,
comparando a frequência real em PGN de categorias de lances (tipo
da peça, captura, roque, promoção, casa/coluna de destino relativa
ao jogador, avanço de peões e centro) com a frequência disponível
entre **todos os lances legais na mesma posição**. Usa
log-proporções com pseudocontagem de regularização 60 e
coeficientes limitados a ±2. É um **modelo estatístico supervisionado
sobre partidas reais**, não uma rede neural criada do zero.

Na avaliação, o Maia-3 79M fornece até 20 candidatos MultiPV;
o adaptador reordena esses candidatos pela combinação de rank
original e preferências treinadas. Não adicionamos o lance real
se ele estiver fora do top-20. O código documenta que a UCI do
Maia informa score de WDL separado, portanto **não inventamos
probabilidades de política a partir desses scores**: usamos
explicitamente uma penalidade ordinal `-log(rank+1)`.

O seletor só é **ativado** se a intensidade selecionada em TUNE
melhorar também em AUDIT com limites inferiores positivos do IC95
bootstrap por partida e pelo menos três decisões alteradas.
Se isso não ocorrer, o resultado operacional volta a ser a
predição original do Maia 79M. O `style_probe` de intensidade
1,0 é **diagnóstico**, nunca intervenção oficialmente autorizada.

## Experimento pareado e critérios

Comparações exatamente nas MESMAS FENs históricas e com histórico
PGN reconstruído (não FEN sem história):
- Maia3 5M original, cenário 2400;
- Maia3 79M original, cenário 2400;
- Maia3 79M + preferências pessoais treinadas (ablação pós-treino);
- Maia3 79M + preferências pessoais somente se TUNE e AUDIT autorizaram.

O workflow inicial testa 64 decisões de validação por jogador
(256 no total), quatro vezes o piloto V6, sem alterar a
seleção da partição temporal. Registra acertos top-1 por
jogador, partidas distintas, macro por partida e bootstrap
pareado de 95%. Como as amostras V6/V7 diferem, **NÃO se
subtraem percentuais entre elas para alegar ganho**; a V7
executa 5M e 79M na mesma amostra atual.

O código completo de treino aceita **todas as decisões do braço
FIT sem limite**; a amostragem de validação é separada e
configurável. A avaliação completa de TODOS os lances de
VALIDATION continua sendo mais cara e, para reproduzi-la,
precisa de execução em lotes/reiniciável ou de tempo de CPU
adequado; não se pode chamá-la de realizada se apenas 256
posições foram inferidas. A próxima etapa de cobertura dos
64 jogadores permanece registrada na issue #143.

## CPU e execução Debian

Não é necessário CUDA nem comprar GPU. O benchmark baixa os
checkpoints oficiais Maia3 5M e 79M uma vez, instala o código
original Maia3 pinado no commit da V6 e roda em CPU. **O tempo
de inferência do 79M é maior**: treinamento do adaptador percorre
os JSONL em streaming; a predição Maia só ocorre na calibração
amostrada e na validação, sem refazer a rede para todo lance
de treino.

Depois de configurar os requisitos da V6, gerar coortes reais
e referência V3 com os MESMOS hashes:

```bash
python -m chesssim.maia_personalization_v7 fit \
  --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
  --v3-model "$HOME/chesssim-homologacao/v4/v3-1600.json" \
  --out "$HOME/chesssim-homologacao/v7-adapters.json"

python -m chesssim.maia_personalization_v7 benchmark \
  --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
  --v3-model "$HOME/chesssim-homologacao/v4/v3-1600.json" \
  --adapter "$HOME/chesssim-homologacao/v7-adapters.json" \
  --maia79-revision "<SHA40_PINADO_E_VERIFICADO>" --fetch \
  --validation-per-player 64 --tune-per-player 32 \
  --out "$HOME/chesssim-homologacao/v7-results.json"
```

**Importante:** o SHA do checkpoint Maia79 é pinado pela CI
antes de usar resultados como base científica, como foi feito na
V6. O arquivo do adaptador também é assinado com SHA-256 e
amarrado às fontes, manifesto, arquivos de treino e modelo V3
de referência. Nada entra nos torneios oficiais até obter
validação independente, segurança tática e aprovações explícitas.

## Fontes

- Maia3 / Chessformer, ICLR 2026: https://openreview.net/forum?id=2ltBRzEHyd
- Maia3 código original: https://github.com/CSSLab/maia3
- Checkpoint Maia79: https://huggingface.co/UofTCSSLab/Maia3-79M
- Estudo original personalização Maia: https://www.maiachess.com/blog/maia-v1
- Corpus histórico do projeto: `src/chesssim/historical_real_corpus.py`

**Limites:** o nosso split é histórico OTB, não idêntico ao
benchmark Lichess Maia; ratings Maia fixos são cenários, não
Elo FIDE real; treinamento do backbone Maia é externo e não
auditamos sobreposição com seus dados prévios; a validação
do projeto já foi consultada várias vezes, de modo que os
p-values são exploratórios. Nenhum resultado negativo
representa teto matemático absoluto.


## V7 — controle de identidade individual (incremento)

Para cada uma das mesmas FENs históricas da validação, a inferência calcula
o rank adaptado com pesos do jogador correto, média **não ponderada**
dos perfis como controle genérico e pesos de outro jogador selecionado
pela rotação circular dos slugs em ordem alfabética (controle de nome trocado).
Uma matriz de todos os perfis contra cada alvo permite encontrar
confusão cruzada, sem escolher o melhor nome com base na validação.
O relatório registra métricas top-1, macro por jogo e IC95 pareado
**por partida histórica**, inclusive correto menos genérico/trocado.
Todos os braços usam exatamente os mesmos candidatos Maia79 e o mesmo
histórico original. A força da adaptação no diagnóstico é fixa em 1,0.
Nenhum resultado desse diagnóstico autoriza promoção sem protocolo
independente e pré-registrado; se o ajuste não melhorar, permanece desligado.
