# Pesquisa V6 — Maia Chess original, transferência histórica e limite de progresso

## Revisão dos estudos publicados

**Maia é pesquisa acadêmica**, não uma empresa de motores que buscam os melhores
lances. Maia original (KDD 2020) adaptou AlphaZero/Leela e treinou **nove
modelos por faixa de 100 pontos entre 1100 e 1900**, usando cerca de
**12 milhões de jogos por faixa**. Em avaliações on-line, o principal
critério era acerto top-1: qual lance a pessoa realmente jogou. A
metodologia descartava os dez primeiros meios-lances e situações em que
o tempo restante era inferior a 30 segundos. A acurácia relatada foi
aproximadamente 46–52%, dependendo da faixa.

Maia-2 (NeurIPS 2024) unificou a modelagem condicionada à habilidade
e obteve aproximadamente **53,87%** em seu subconjunto de mestres.
Maia-3 (Chessformer, ICLR 2026) usa Transformer de atenção geométrica e
histórico de posições. A variante de 5 milhões de parâmetros relatou
**55,4%** e a de 79 milhões **57,1%** no protocolo do artigo.

**Personalização importa:** outro estudo Maia relatou até **65% de acerto
em alguns indivíduos** e alertou que jogadores com menos jogos podem
não melhorar após fine-tuning. Nenhum desses resultados constitui teto
universal, pois as amostras incluem populações e regimes de tempo
específicos. Não é científico comparar diretamente com nossa amostra
de grandes mestres clássicos do século XX.

Referências oficiais:
- https://www.maiachess.com/blog/maia-v1
- https://www.microsoft.com/en-us/research/blog/the-human-side-of-ai-for-chess/
- https://proceedings.neurips.cc/paper_files/paper/2024/hash/250190819ff1dda47cd23cecc0c5a69b-Abstract-Conference.html
- https://openreview.net/forum?id=2ltBRzEHyd
- https://github.com/CSSLab/maia3 (AGPL-3.0, UCI Python)
- https://huggingface.co/UofTCSSLab/Maia3-5M

## O que realmente replicamos

O benchmark v6 é um estudo **de transferência de domínio**, não
uma réplica exata da tabela do artigo do Maia. Usamos o modelo
**Maia-3 5M original pré-treinado**, não uma rede criada artificialmente
ou treinada por nós. Seu código é pinado no commit GitHub
`1e13597c42d4858b7cfd7cfdae01e297263364b2`. O checkpoint
Hugging Face é fixado pela revisão git completa resolvida ANTES da
execução, e seu SHA-256 real é salvo no relatório.

Em vez de apenas FEN, reconstruímos todos os lances anteriores a
cada decisão por reinterpretação do PGN original, verificando que o
tabuleiro e o lance humano correspondem ao hash e à posição esperados.
O Maia-3 executa em CPU com `--use-uci-history`, temperatura zero,
sem amostragem estocástica, no contexto verdadeiro das partidas.
São descartados os primeiros 20 meios-lances (uma regra até mais
restritiva que Maia original), usando apenas FENs com `ply >= 21`.
O resultado é comparado com Rodent IV UCI real MultiPV profundidade 3
e a política V3, nas mesmas posições.

**Não inventamos ratings:** 1800 e 2400 são cenários fixos de rating
**na escala esperada pelo Maia**, não estimativas Elo FIDE de Fischer,
Kasparov, Karpov ou Tal. São uma análise de sensibilidade; nenhuma
conversão de FIDE histórico para Lichess é feita. Os PGNs OTB
históricos geralmente não trazem relógios por lance; não conseguimos
reproduzir o filtro Maia de 30 segundos. Portanto as porcentagens
aqui NÃO podem ser equiparadas às publicadas pelo Maia.

## Experiência adicional: veto tático calibrado

Além de reproduzir uma linha de base Maia original, testamos uma
hipótese incremental barata:

- Maia-3 propõe um lance, Rodent IV gera suas quatro variantes reais
  e avaliações em centipeões, sem olhar o lance humano.
- Uma seleção Maia/Rodent só aceita a proposta Maia quando ela
  aparece entre as quatro variantes Rodent e a diferença de score
  não ultrapassa **0, 35, 80 ou 150 centipeões**, conforme limiar
  calibrado. São pré-especificadas as combinações desses quatro
  valores com dois cenários de Elo Maia (1800, 2400).
- O limiar por jogador é ajustado com **48 decisões de anos TRAIN**;
  selecionamos pelo ganho médio top-1 por **partida histórica**, com
  pequena penalidade por intervenção, nunca por pontuações de validação.
- A seguir exigimos replicação em outros jogos de TRAIN, sem
  sobreposição de partida: no mínimo 3 alterações e limite inferior
  do bootstrap por partida de 95% **positivo**. Sem essa evidência,
  não existe gate: o Rodent continua integralmente soberano.
- A avaliação de transferência utiliza **24 novas decisões por
  jogador**, de anos VALIDATION, e embarga posições canônicas que
  apareceram em QUALQUER decisão de TRAIN. O holdout final
  permanece lacrado.
- Os percentuais top-1 incluem TODOS os exemplos. ICs e testes
  de sinais são calculados por partida, nunca atribuindo
  independência indevida aos meios-lances da mesma partida.

Profundidade 3 e diferença de score não certificam segurança
tática. O gate é apenas um experimento offline, **não está conectado
às partidas oficiais ou ao Rodent de produção**.

## Custo computacional e replicação local

O código do Maia-3 oferece versão 5M explicitamente indicada
para CPU; **não treinamos do zero 5–79 milhões de parâmetros**.
A biblioteca Maia-3 é uma dependência opcional executada como
processo UCI separado, para evitar forçar PyTorch no ambiente
padrão de desenvolvimento. Não é necessário contratar nuvem ou GPU.

Em um virtualenv separado de Python com CPU-only PyTorch:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
git clone https://github.com/CSSLab/maia3.git maia3-source
git -C maia3-source checkout 1e13597c42d4858b7cfd7cfdae01e297263364b2
python -m pip install -e ./maia3-source
```

Gerar a revisão correta do checkpoint:

```bash
# Revisão verificada no experimento V6; NÃO substituir por 'main' dinâmica.
MAIA_HF_SHA="b6559de2398d7140b985f28fd2c19fb5e47ddabe"
```

No repositório ChessCup, após construir as coortes reais e a V3 de
controle nos mesmos parâmetros da CI:

```bash
python -m chesssim.maia_transfer_v6 \
  --cohorts "$HOME/chesssim-homologacao/coortes-carreira-01" \
  --v3-model "$HOME/chesssim-homologacao/v4/v3-1600.json" \
  --rodent-root "$RODENT_IV_HOME" \
  --maia-revision "$MAIA_HF_SHA" --fetch \
  --train-per-player 48 --val-per-player 24 --rodent-depth 3 \
  --out "$HOME/chesssim-homologacao/v6-maia-transfer.json"
```

Para reprodução estrita futura, use o SHA **já gravado no relatório**,
em vez de atualizar a revisão. Cada modelo e cada perfil de Rodent
é identificado por hash. Não distribuímos os dados pessoais de
treinamento Maia, nem arquivos PGN brutos de terceiros no artefato.

## Critério explícito de parada científica

O objetivo não é 100%. É quantificar ganhos factíveis dentro de dados
e hardware disponíveis, com critérios para interromper estratégias
que deixaram de agregar informação. Por jogador, propomos:

1. Registrar cobertura: partidas distintas por época, distribuição
   de lances, abertura/meio-jogo/final e intervalos de confiança.
   **Falta de dados** é classificada como insuficiência estatística,
   não como falha da pessoa ou do método.
2. Cada nova estratégia deve ser testada contra o melhor controle,
   no **mesmo** conjunto pré-especificado de jogos nunca usados na
   sua escolha de hiperparâmetros. Reportar ganho absoluto de top-1,
   erro de previsão de distribuição, estabilidade e segurança tática.
3. Considerar uma família de estratégias em platô operacional somente
   depois de **duas experiências pré-registradas** sem ganho e com
   limite SUPERIOR de IC95 para ganho incremental inferior a **2
   pontos percentuais**, com pelo menos **100 jogos distintos**
   por perfil e cobertura temporal suficiente. O teste não precisa
   provar um teto teórico: decide o que vale continuar investindo.
4. Caso não haja 100 jogos para um jogador, manter resultado como
   **UNDERPOWERED** e priorizar seu melhor controle já validado,
   sem extrapolação do rating ou da personalidade.
5. Os ganhos em amostras de desenvolvimento/validação previamente
   inspecionadas são exploratórios. O holdout final só deverá
   ser aberto num protocolo posterior congelado para os 64 perfis.

Assim, o projeto pode incorporar o máximo viável em cada jogador,
com diferenças reais de qualidade documental, sem fabricar
consistência matemática ou prometer ultrapassar o estado da arte.


## Primeira execução reproduzida em CPU — 14/11/2026

O [workflow real #43647773211](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43647773211)
passou, executando realmente o **Maia-3 5M** e **Rodent IV**
sobre 192 decisões de calibração (48 por jogador) e 96 decisões
históricas de validação (24 por jogador). A origem do checkpoint
é o repositório oficial Maia3-5M, revisão HF
`b6559de2398d7140b985f28fd2c19fb5e47ddabe`; SHA-256 dos
pesos `ba14208b2992d85502f5fb501934abf6aaaeb355e9f3fdf90e326911f562524f`.

| Método, sobre as MESMAS 96 posições | Lances corretos |
| --- | ---: |
| Maia-3 cenário 1800 | 47/96 (49,0%) |
| Maia-3 cenário 2400 | **50/96 (52,1%)** |
| Rodent IV MultiPV top-1 | 32/96 (33,3%) |
| Seletor Maia–Rodent calibrado | 32/96 (33,3%) |
| V3 contextual histórico | 20/96 (20,8%) |

**Separação por jogador no cenário Maia-3 2400 / Rodent IV:**
Fischer 15/24 vs 7/24; Karpov 11/24 vs 8/24; Kasparov
12/24 vs 7/24; Tal 12/24 vs 10/24.

Nenhum dos quatro gates recebeu autorização de substituir o
Rodent: quando o limiar escolhido não demonstrou vantagem
suficiente no audit de partidas independentes, o resultado foi
o bestmove normal do Rodent. Isso não significa que Maia
tenha menos concordância global (teve MAIS), mas sim que a
restrição de segurança por MultiPV e os critérios de admissão
não encontraram benefício suficientemente robusto para liberar
uma alteração. A precisão de shallow MultiPV não demonstra
que um lance é seguro em profundidade tática real.

A observação de 52,1% numa amostra de 96 lances NÃO prova
convergência ao resultado publicado de 55,4%; populações,
relógios e tamanhos são incomparáveis. O número final deve
vir acompanhado de IC por partida e variações entre épocas.
Trata-se de uma **validação preliminar de transferência**,
não de um patamar definitivo para os 64 perfis.

### Achados da revisão adversarial

1. O teste estático revelou um identificador local criado e
   não utilizado na reconstrução do histórico; removido.
2. O gate separava jogos, mas partidas diferentes poderiam
   reproduzir a mesma posição canônica. A segunda revisão
   acrescentou **embargo de posição canônica entre ajuste
   e auditoria**; se a cobertura independente restante for
   menor que 8 jogos, ele **nega a intervenção**, sem falsear
   evidência nem apagar o jogador pouco documentado.
3. O relatório inicialmente trazia IC95 por partida só
   para cada jogador; agora inclui também **contrastes
   agregados pareados por jogo** sobre as mesmas 96 FENs.
4. Verificação de hash do checkpoint, pin do código do Maia,
   histórico completo verdadeiro, ausência de rótulo futuro,
   rejeição do holdout e da equivalência artificial de
   Elo histórico versus Lichess são critérios de CI.

**A segurança científica vem antes da promoção do motor**:
o modelo original Maia continua apenas como baseline de
transferência; não altera a execução oficial, os livros,
os pesos do Rodent ou a classificação dos jogadores.


### Contrastes agregados, embargo de posições e proveniência final

Na segunda execução real (workflow 43648458017), os resultados
absolutos de 50/96 vs 32/96 foram reproduzidos. O efeito Maia-3 2400
menos Rodent, calculado como ganho MACRO por **95 partidas históricas
distintas**, foi de +0,1895, IC95 bootstrap [0,0737; 0,3158],
teste exploratório de sinais p=0,0050; cenário Maia-3 1800,
+0,1579, IC95 [0,0211; 0,2737], p=0,0245.

Estes p-values são exploratórios e NÃO ajustados para multiplicidade
ou escolhas reiteradas na validação; não autorizam afirmar 95% de
probabilidade de que o Maia seja superior universalmente.

Os 4 seletores continuaram negados. A auditoria após o ajuste
trata separadamente jogos e posições canônicas; na segunda execução,
17 partidas de auditoria por jogador sobreviveram, sem colisões
canônicas nesse recorte. A retenção do Rodent não é uma vitória
do gate: decorre da ausência de vantagem replicável para a política
com veto.

A revisão final congelou o checkpoint V6:
- Maia3 código pinado ao commit GitHub
  `1e13597c42d4858b7cfd7cfdae01e297263364b2`;
  código carregado via pacote instalado é verificado por `git rev-parse HEAD`.
- Checkpoint Hugging Face pinado ao commit
  `b6559de2398d7140b985f28fd2c19fb5e47ddabe`, sem consulta
  dinâmica à versão mais recente a cada runner.
- Pesos originais conferidos byte a byte por SHA-256
  `ba14208b2992d85502f5fb501934abf6aaaeb355e9f3fdf90e326911f562524f`.
- Pesos não são redistribuídos na PR, que distribui somente
  código de integração, modelo V3 treinado e dados agregados
  obtidos dos modelos originais.

Quando disponibilizarmos uma nova versão do Maia, ela deverá
ser outro experimento versionado, com fonte, revisão e pesos
explicitamente identificados, não um silencioso upgrade da V6.
