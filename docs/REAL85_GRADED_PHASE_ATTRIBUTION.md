# Quatro históricos: adversários graduados, aberturas reais e atribuição de erros

## Objetivo prático

Medir jogos **UCI de verdade** dos perfis Fischer, Tal, Karpov e Kasparov,
sem recalibrar estilos para acertar posições previamente observadas.
O corpus de 64 jogadores já foi auditado no merge #184: 100.844 trajetórias
únicas FIT/TUNE; 4.919.270 decisões dos respectivos jogadores. Esta fatia
utiliza os arquivos reais completos dos quatro pilotos dentro da mesma
partição histórica, não jogos artificiais para treino.

### Partidas

Para cada jogador, selecionar **antes dos jogos** três aberturas distintas
de partidas reais FIT, após os 12 primeiros meios-lances, com origem e
SHA registrados. Nunca selecionar uma linha de TUNE ou TEST. O mesmo
ponto de partida é usado pelos dois braços, e os jogos UCI prosseguem
até um resultado terminal, com atribuição de cor invertida.

Há duas forças de oposição, concretas mas NÃO ELO:
Rodent IV Simple com busca depth **2** e **6**. O motor histórico
Rodent usa depth **4** nas duas. A IA neural, se ativa, só propõe
mudanças dentro do controle de segurança Stockfish depth8 e confirmação
forçada depth12; o SHADOW só observa. São 4 históricos × 3 inícios
× 2 profundidades adversárias × 2 cores × 2 braços = **96 jogos**.

A abertura histórica compartilhada pelos dois braços começa em um FEN
autêntico depois de 12 meios-lances, de modo que as partidas são
**continuações completas** até o fim — não se afirma terem começado
da posição inicial numa partida contra Simple. O livro histórico
aprendido das partidas reais continua podendo jogar até ply40 no
turno do próprio jogador. O adversário nunca recebe o livro do jogador.

### Atribuição por fase

Independentemente da contagem de vitórias, reabrir os PGNs jogados e
amostrar, de modo determinístico, o **primeiro lance de cada
fonte × fase por partida**, com até uma ocorrência em cada célula.
Os lances realmente jogados são classificados como:

- **Rodent original:** bestmove UCI da personalidade sem intervenção.
- **Repertório:** lance escolhido pelo livro histórico; não é um
  bestmove original do Rodent. Para comparação, executar de verdade
  Rodent IV depth4 no mesmo FEN sem livros.
- **Neural:** lance de substituição realmente escolhido após o
  Rodent responder, com confirmação Stockfish depth12.

O Stockfish independente depth14 avalia a **mesma posição de origem**,
o lance real, o lance original do Rodent e o melhor lance observado
a essa profundidade. Separar posições de abertura (ply abaixo de 40),
meio-jogo e finais com pouco material; finais não são identificados
apenas por ultrapassar um número de lances. Limitar e publicar
quantas posições foram efetivamente avaliadas.

O resultado é diagnóstico **descritivo**:
não é experiência de rating humano, prova de identidade individual,
nem causa demonstrada de derrota. A profundidade do Stockfish também
pode produzir estimativas instáveis. Quando não há finais ou lances
de determinado tipo, deixar a categoria sem observações em vez
de afirmar zero erros.

### Limitações e resultado esperado

A comparação de profundidades reais identifica se a força do
adversário muda a performance dos perfis; a análise de lances
distingue se o erro se origina do Rodent, do repertório ou de
substituição neural em determinada posição. Não confundir o
Stockfish que **avalia/guarda** os lances com o motor Rodent que
**escolhe** os lances da partida.

A fatia não altera originais Rodent IV, não aumenta parâmetros
de Maia/DSC e não promove estilo histórico sem validação
independente. As 96 partidas e as avaliações por fase somente
podem ser declaradas concluídas após a CI produzir PGNs
completos, métricas e auditoria adversarial sem findings.
