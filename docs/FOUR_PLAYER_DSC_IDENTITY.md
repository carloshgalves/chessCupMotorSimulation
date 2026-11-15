# Quatro jogadores — personalização DSC sem vazamento de dados

Perfis do piloto: Fischer, Tal, Karpov e Kasparov. As fontes históricas
permanecem fixadas na revisão de PGNs do repositório. Nenhuma outra pessoa
é treinada ou avaliada nesta experiência.

## Hipótese e desenho

A V8 observou DSC-150M > Maia79 em VALIDATION histórica (157/256 vs
142/256); tal observação foi consultada e **não é evidência independente**.
O objetivo desta PR é testar se há alguma diferença individual
reproduzível *antes* de realizar o teste final.

As preferências vêm do FIT verdadeiro do V7 (122.847 decisões e 4.256
participações de jogos no run original). TUNE e AUDIT são partidas distintas
do mesmo braço de anos TRAIN, por hash global da trajetória, sem lances de
FIT reusados. Um segundo embargo por FEN canônica de quatro campos impede
que AUDIT reuse posição de TUNE.

DSC original é congelado, sem fine-tuning dos 150 milhões de parâmetros:
reordena-se até 20 movimentos propostos pelo UCI original conforme
\`-log(rank+1) + alpha * score(resíduo pessoal - média equitativa)\`.
Isto é uma **aproximação ordinal de ranking**, não probabilidade de
movimento do checkpoint original. \`alpha\` é escolhido EXCLUSIVAMENTE em
48 posições de TUNE por jogador, do conjunto predefinido
\`[0, 0.05, 0.1, 0.2, 0.4]\`. AUDIT usa até 48 posições por jogador
e registra IC95 bootstrap pareado agrupado por partida.

Controle cego: nas mesmas posições, as quatro políticas pessoais pontuam
o lance histórico, com os nomes ocultos ao seletor. O classificador
escolhe o perfil que atribuiu maior probabilidade *normalizada do reranker*
ao lance observado. Quando o lance não aparece no top-20 original,
o caso é registrado como sem cobertura, nunca reclassificado
arbitrariamente. O acaso nominal é 25% dos casos cobertos;
abrir o nome verdadeiro só acontece na etapa de avaliação.

Também são documentados top-1 correto, genérico (= DSC original), perfil
trocado, divergência de recomendações e ganho pareado por jogo. Nenhum
resultado no AUDIT é estimado com HOLDOUT/VALIDATION. O gate exploratório
exige melhora positiva em auditoria independente versus DSC e nome trocado
(alfa fixo), mais pelo menos 30 jogos distintos. Essa regra **não
constitui autorização de produção**.

## Limites

Estes dois braços pertencem aos anos TRAIN, portanto um resultado positivo
ou negativo ainda é *exploratório* e não equivale à confirmação no
holdout histórico final. A sobreposição do corpus de pré-treino externo
do DSC com partidas antigas não pode ser excluída; classificação de
identidade sobre posições antigas pode refletir memorização/contexto.
Sem ensaio de segurança tática Stockfish, não utilizar em partidas oficiais.

Se houver benefício robusto em AUDIT, congelar protocolo e políticas
antes de ler uma única decisão do HOLDOUT. Se não houver, registrar
fracasso e não fabricar diferenciação entre os quatro personagens.
