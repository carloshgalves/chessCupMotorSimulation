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
