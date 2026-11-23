# Tal × Fischer: comparação comum da qualidade histórica dos lances

## Critério correto

O objetivo do ChessCupMotorSimulation é disponibilizar um Tal com qualidade
de decisão compatível com seu jogador histórico, na **mesma tolerância de
representação** aplicada ao Fischer já congelado, preservando preferências
ofensivas reconhecíveis. Não é reproduzir todos os lances ou reconstruir
a mente de Mikhail Tal.

Os benchmarks anteriores de Tal Balanced **não** demonstraram isso.
O 10+2 com 288 partidas teve 47,92% contra Rodent Simple d16 e 76,04%
contra Tal original, o que comprova vantagem *relativa* entre motores
Rodent. O benchmark original de Fischer era igualmente uma homologação
de produto, não um teste de equivalência histórica de Elo humano.

## Quatro comparações fundamentais

Na **mesma posição pré-lance da partida histórica**, todos os candidatos
são convidados a escolher a jogada, independentemente do humano:

1. Lance efetivamente jogado pelo humano na partida;
2. Rodent Fischer congelado d16 (mesmo arquivo e hash do gate anterior);
3. Tal Balanced d16, com SHA-256 do candidato previamente selecionado;
4. Controles adicionais: Rodent Tal original d16 e Rodent Simple d16.

O Stockfish independente, com a mesma instalação e pesquisa **d20**,
avalia cada jogada candidata *a partir daquela exata posição*,
usando pesquisas de raiz forçada. Isso produz um desvio em centipeões
**em relação à melhor continuação encontrada pelo avaliador**, sem
rotular arbitrariamente o lance diferente do humano como incorreto.
O resultado não constitui verdade absoluta: profundidade finita,
horizonte de busca, sobreposição de linhas e partidas de diferentes
épocas limitam o método.

## Fonte, épocas e amostragem pré-fixadas

Usar o arquivo PGN Mentor histórico, commit do mirror já fixado no
repositório, SHA do Fischer
`dcb6428779f2506c302c18148b36e83b56123397e2e348d325e292ae01557d19`
e SHA de Tal
`586aac4613e170474f12893679ab992d3999ca5109df7df77c634a76846d2687`.

Selecionar, em cada jogador:

- **96 partidas FIT** do período de maior destaque definido
  *antes de verificar a qualidade*: Fischer **1970–1972**;
  Tal **1957–1961**.
- **96 partidas FIT** das demais épocas como análise complementar,
  para impedir que uma pontuação de toda a carreira esconda uma
  queda de qualidade no auge.
- Uma posição **não forçada por fase** (abertura, meio-jogo, final),
  em cada partida, escolhida por menor SHA com salt fixo. Uma
  partida que não alcançou finais não recebe um final fictício.
- Sempre comparar humano, Fischer, Tal Balanced e controles na
  *mesma* posição, mantendo histórico de lances para roque,
  en passant, repetição e regra dos cinquenta lances.

Total: **384 partidas históricas selecionadas**, com o número real
de posições dependente das fases observadas. Todos os 827 jogos
aceitos no corpus Fischer e os 2.431 de Tal continuam inventariados.
A seleção é derivada exclusivamente do FIT já definido globalmente.
Nenhum lance TUNE ou TEST é selecionado para calibrar os critérios;
o TEST já utilizado em outros contextos do projeto não deve ser
retrospectivamente chamado de blind.

Registros por partida, decisão e motor contêm hash do PGN, FEN,
candidatos, Stockfish, Rodent, perfis, profundidade atingida e
resultados de busca. O agregador **reabre as partidas originais** e
confere cada posição e lance; 32 shards de CI precisam ser íntegros.
Falha ou falta de uma posição não pode ser substituída por um número
inventado ou removida silenciosamente dos denominadores.

## Métrica primária: diferença de fidelidade de qualidade

Para cada decisão de qualidade **numericamente comparável**,
`L(x)` é o desvio não negativo, em centipeões, do movimento `x`
em relação ao movimento de referência no mesmo tabuleiro.

Em Fischer histórico:
`G_F = média_fases[L(motor Fischer) − L(humano Fischer)]`.

Em Tal histórico:
`G_T = média_fases[L(motor Tal Balanced) − L(humano Tal)]`.

Diferença de qualidade histórica **relativa**:
`D = G_T − G_F`.

A média é calculada separadamente por abertura, meio-jogo e final,
depois agregada com pesos iguais, evitando que o grande número de
lances de meio-jogo ou a duração de partidas determine sozinho o
resultado. O intervalo de confiança de 95% reamostra **partidas
inteiras**, jamais trata lances da mesma partida como independentes.

O critério de não inferioridade de produto, registrado antes da
execução, exige o **limite superior do IC95 de D ≤ +15 cp**.
Isso não garante que `G_F` seja zero ou Fischer historicamente
perfeito; compara a tolerância relativa entre as duas personalidades.

## Garantias contra pontuação média enganosa

Além do critério principal, para declarar o proxy adequado:

- **Erros graves:** diferença Tal–Fischer da taxa *excedente ao
  humano* de escolhas que perdem mais de 100 cp; limite superior
  do IC95 ≤ **5 pontos percentuais**.
- **Decisões táticas pré-lance:** subset identificado pelo mesmo
  classificador de Fischer a partir da posição e do segundo
  melhor lance do Stockfish, sem olhar a jogada que o humano ou o
  motor escolheria. Diferença Tal–Fischer de perda excedente;
  limite superior do IC95 ≤ **+20 cp**.
- **Mates:** nunca transformar posições com mate detectado em
  centipeões inventados. Publicar separadamente quantos mates
  vencedores foram perdidos e em quantos lances o motor escolheu
  uma linha de mate desfavorável.
- **Cobertura:** em cada conjunto principal, pelo menos 20
  observações numéricas por fase, 20 observações de contexto
  tático e 85% de cobertura numérica conjunta; pelo menos 90%
  das pesquisas de cada personalidade principal devem alcançar
  d16 solicitado. Inconsistência do avaliador entre busca raiz
  global e busca raiz forçada >35 cp em mais de 2% dos casos
  bloqueia a conclusão.

Se faltar suporte ou integridade, o estado é
`INCONCLUSIVE_NEEDS_HUMAN_QUALITY_SUPPORT`. Se os critérios
de não inferioridade não passarem, o estado será
`HISTORICAL_MOVE_QUALITY_PARITY_NOT_DEMONSTRATED`.
Se todos passarem, será apenas
`HISTORICAL_MOVE_QUALITY_PARITY_PROXY_PASSED_REVIEW_REQUIRED`.
**Não será inventada uma nota FIDE, Elo ou avaliação universal.**

## Sobre sacrifícios e combinações

A qualidade geral **não demonstra por derivação** que cada sacrifício
e combinação é correto: isso seria inferir um comportamento de cauda
a partir de uma média. O gate já inclui decisões táticas e erros
graves, para reduzir esse risco sem exigir coincidência histórica
dos lances.

Se o subconjunto tático tiver tamanho e qualidade adequados,
podemos dispensar uma bateria separada para um *critério de produto*
sobre tática geral. Mas não devemos denominar "sacrifícios
historicamente certificados" sem um subconjunto validamente
anotado de sacrifícios e uma avaliação específica.

## Estado operacional

O workflow de pull request executa **somente inventário integral,
testes unitários e validações do desenho**. A análise d20 com
múltiplas avaliações contrafactuais de raiz, que consome muitas
horas de CPU, é disparada explicitamente em
`historical-move-quality-parity.yml` via GitHub Actions
`workflow_dispatch` após revisão e merge:

```bash
gh workflow run historical-move-quality-parity.yml \
  --repo carloshgalves/chessCupMotorSimulation --ref main
```

Todos os perfis Rodent originais são lidos sem alterações.
Tal Balanced é construído num sandbox com os oito parâmetros
previamente selecionados. A execução usa Fischer congelado
**exatamente em depth16**, sem livro adicional ou aumento
oportunista de profundidade para favorecer um dos modelos.

A PR #211 de proposta de release Tal permanece separada;
**não congelar Tal** por causa de vitória contra Simple,
coincidência de lances ou um CI verde. A qualidade histórica
deve ser confirmada neste novo protocolo antes da homologação.
