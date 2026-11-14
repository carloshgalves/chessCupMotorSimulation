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
MAIA_HF_SHA="$(python -c 'from huggingface_hub import HfApi; print(HfApi().model_info("UofTCSSLab/Maia3-5M").sha)')"
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
