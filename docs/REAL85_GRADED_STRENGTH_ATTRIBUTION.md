# Real85 — adversários graduados, abertura diversa e origem dos erros

## Questão de investigação

Os resultados negativos de Fischer/Karpov em #183 podem refletir Rodent IV d4, intervenção do residual de estilo, escolha de abertura ou força do adversário. Não é aceitável atribuir todo erro tático à personalidade histórica.

## Desenho prospectivo

Quatro pilotos: Fischer, Tal, Karpov e Kasparov. Cada perfil preserva o treinamento histórico amplo de FIT, sem cortes arbitrários de partidas ou lances. A fonte de 64 perfis (#184) é a base de disponibilidade, não 64 motores treinados.

Para cada jogador e cor, duas posições originais de **12 plies históricos**, selecionadas exclusivamente de partidas FIT, representando famílias de aberturas diferentes nos quatro primeiros lances, antes dos resultados dos motores. Ambas as condições SHADOW (lances do Rodent original) e GUIDED (com intervenção neural guardada) jogam **a partir do mesmo FEN**, com a mesma cor.

O perfil histórico permanece no Rodent IV autêntico, profundidade 4. O adversário Rodent IV Simple sem livro atua com busca 2, 4 ou 6. O desenho tem 4 jogadores × 2 cores × 2 aberturas × 3 forças × 2 condições: **96 partidas UCI jogadas até resultado terminal**, se a CI finalizar sem interrupção. Essas partidas **não começam do tabuleiro inicial**; são partidas novas, completadas a partir de posições históricas de abertura com FEN e identidade do jogo-fonte auditáveis.

Após o ply 40, o residual pode alterar decisões nas condições GUIDED mediante análise independente Stockfish profundidade 8 e comparação forçada de profundidade 12. O livro histórico afeta apenas o jogador focal, jamais o Rodent Simple. Os arquivos de personalidade não são reescritos.

## Origem dos erros na mesma posição

Uma auditoria separada recolhe amostras com escolha determinística por jogador, força oponente, fase e origem real:

- **Motor-base:** a escolha de Rodent puro em SHADOW é comparada à alternativa de referência Stockfish na posição exata.
- **Repertório:** o lance efetivamente escolhido pelo livro é comparado ao lance que o Rodent autêntico d4 escolheria **no mesmo FEN**, ambos avaliados pela mesma profundidade Stockfish.
- **Rede neural:** o lance efetivamente modificado é comparado à escolha original de Rodent **registrada na mesma posição**, ambos avaliados independentemente.

Mates permanecem separados de centipawns. A classificação aproximada de final usa o **material presente na posição**, não só o número de lance. Não há imputação de erros de livro ao motor, nem de erros originais de Rodent à rede neural.

## Interpretação e limites

Uma vantagem contra Simple depth 2 não demonstra Elo humano nem reprodução fiel do campeão. A busca Stockfish depth 12 não é um oráculo completo de qualidade estratégica, e diferenças em centipawns não se traduzem diretamente em pontos perdidos. Os resultados de partidas pareadas divergem depois de decisões diferentes; essas partidas não estimam efeitos causais por lance. A atribuição no mesmo FEN é apenas observacional em relação à referência de busca.

Não foi aberto TEST/holdout para escolher parâmetros. O residual ainda é um componente pequeno treinado em CPU, e não um fine-tuning genuíno do backbone DSC/Maia. O workflow publica apenas PGNs das partidas novas entre motores, identificadores e relatórios agregados, não as fontes PGN históricas originais. Nenhuma avaliação deve ser anunciada antes da CI real.
