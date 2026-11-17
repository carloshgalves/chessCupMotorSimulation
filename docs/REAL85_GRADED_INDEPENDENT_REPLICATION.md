# Réplica exploratória: adversário intermediário, diversidade de abertura e origem dos erros

Esta nota preserva os resultados da execução independente da [PR #186](https://github.com/carloshgalves/chessCupMotorSimulation/pull/186), sem adicionar uma segunda implementação concorrente ao experimento já mesclado na [PR #185](https://github.com/carloshgalves/chessCupMotorSimulation/pull/185).

## Evidência real

[CI da réplica #43999813403](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43999813403) e [evidências #14291936840](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43999813403/artifacts/14291936840): **96 partidas reais UCI concluídas**, 84 trajetórias distintas, 16 pontos de partida extraídos de FIT, dois por jogador e por cor. Rodent histórico d4 contra Rodent Simple d2/d4/d6, SHADOW e GUIDED, com cores equilibradas.

Pontos do jogador histórico, **GUIDED / SHADOW**, em quatro jogos de cada braço e profundidade:

| Perfil | Simple d2 | Simple d4 | Simple d6 |
| --- | --- | --- | --- |
| Fischer | 4 / 4 | 1,5 / 1 | 0 / 0,5 |
| Tal | 4 / 4 | 0,5 / 1 | 0 / 0 |
| Karpov | 4 / 4 | 3 / 3,5 | 0,5 / 0,5 |
| Kasparov | 4 / 4 | 1,5 / 0 | 0 / 0 |

Amostra independente Stockfish d12 em 69 posições: 36 lances originais Rodent, **6** com diferença superior a 100cp contra o melhor lance de referência; 12 lances de livro, **0** acima de 100cp; 21 substituições neurais, **0** acima de 100cp. Essa auditoria amostral NÃO elimina erros fora dos pontos escolhidos. São três fontes diferentes e comparações separadas em posições próprias; não é válido inferir causalidade por pontos da partida.

Foi feito download do ZIP de evidências e rechecagem independente dos registros por código externo à CI: **10.938 eventos de política**, sendo **232 intervenções neurais** e **152 lances de livro**, com 0 inconsistências quanto a origem do livro, associação à FEN e delta de confirmação d12. O código da réplica permaneceu experimental e **não foi mesclado**, porque a PR #185 foi integrada à main no decorrer da validação e já entrega o mecanismo principal.

## Conciliação com o experimento em main

A PR #185 mantém um desenho diferente: 96 continuações de 12 aberturas históricas no total (três por jogador, sete FENs distintos globalmente), adversários Simple d2/d6 e auditoria Stockfish **d14** com 385 observações e 195 FENs únicos. Nesse ensaio ocorreram 18 diferenças >100cp em 248 escolhas Rodent com score numérico, três em 52 escolhas neurais, e uma escolha neural de Kasparov ficou **115cp pior que o Rodent original** na busca d14. O livro não apresentou diferenças >100cp naquela amostra de 74 observações, mas houve repetição de posições. Ver [CI #43999260267](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43999260267).

As amostras **não podem ser combinadas como observações independentes**, nem comparadas por diferença bruta de taxas: têm raízes históricas, níveis de Stockfish, distribuição por fase e controles distintos. O achado d14 impede afirmar que a guarda d12 é infalível. Da mesma forma, adversário mais fraco não demonstra rating histórico. Os resultados indicam que limitar Rodent a profundidade 4 produz fragilidade tática importante e que o residual necessita avaliação independente mais profunda, sem calibrar parâmetros usando o próprio teste.

## Integração adotada

Preservar **um só mecanismo de avaliação jogável** (PR #185). Esta fatia reforça a reconciliação do registro de confirmação neural profunda da versão principal, exigindo valores de CP coerentes, ausência de linhas de mate e respeito ao orçamento tático fixado. O original Rodent e o TEST histórico não são modificados. Questões científicas de força e identidade permanecem em #170/#169.
