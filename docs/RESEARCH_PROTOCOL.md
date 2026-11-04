# Protocolo de pesquisa

## Hipótese

É possível induzir um motor de xadrez a exibir preferências de decisão sistematicamente mais próximas das partidas históricas de um jogador específico do que de perfis genéricos ou de outros enxadristas.

## Procedimento

1. Registrar fonte PGN, jogadores identificados, anos, controles de tempo conhecidos e qualidade dos metadados.
2. Deduplicar partidas, segregar fontes e congelar partições por **partida** para evitar vazamento entre posições semelhantes.
3. Ajustar candidatos apenas no treino e fazer seleção somente em validação autorizada; preservar conjunto final lacrado.
4. Comparar cada candidato em posições compartilhadas, fora de aberturas conhecidas e com controles negativos.
5. Separar avaliação de força (CP loss, rating de torneio entre motores) de identificação de estilo.
6. Reportar intervalos de incerteza, número efetivo de partidas, variabilidade entre sementes e impacto da profundidade do avaliador.

O corpus de Mecking contém recortes exploratórios cuja partição e mecanismos de embargo são preservados por identificadores internos no código. Não se deve abrir ou recalcular conjuntos finais reservados durante ajustes.

## Limitações

Os perfis Rodent IV são configurações de um mesmo motor, não redes treinadas de ponta a ponta de forma independente. Uma simulação jogável não é uma reconstrução comprovada de um jogador histórico.

## Independência das partidas simuladas

A análise pós-jogo registra um SHA-256 canônico do FEN inicial e de toda a sequência de lances UCI. Nomes, data, número de rodada e resultado PGN não criam artificialmente uma nova partida quando a trajetória é a mesma. As séries conservam `games_total`, `games_distinct`, `games_duplicate` e `effective_independent_games`, além das sementes e das condições de abertura observadas. Um PGN vazio não constitui observação independente; partidas incompletas com lances podem subsidiar apenas análises descritivas dos movimentos, não estimativas de resultado de jogo concluído.

Nos relatórios de qualidade e estilo, trajetórias repetidas não devem inflar a amostra independente. A análise diagnóstica de posições duplicadas deve marcar que tais posições **não** entram nas médias agregadas. A alternância de cores e sementes não prova independência estatística. Para inferência, usar jogos distintos e intervalos de incerteza com agrupamento adequado; não reinterpretar decisões de uma mesma partida como observações independentes.
