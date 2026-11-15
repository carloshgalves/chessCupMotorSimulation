# V7 — evidência observada antes do controle de identidade

Fonte: [run 43652333683](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43652333683), commit `893d219eff729f365a1dca6735c0771cca46f287`.
Artefato `chesssim-v7-all-real-games-maia79-personalization`, ID **14137635767**;
SHA-256 do ZIP **04e353299857ee2d4bc9554483bbb562de2519923280a1ef21d2016d119fc3a4**.
Digest interno do relatório: `d284a2b73c4de454db554f4ba0d9d8fd76129dda6bb3666b95a30092610b3153`.
Dados obtidos de PGNs reais públicos pinados; não são fixtures.

## Treinamento efetivo e cobertura

| Jogador | Jogos aceitos da fonte | Jogos de anos TRAIN | Jogos distintos de FIT | Decisões reais FIT |
|---|---:|---:|---:|---:|
| Fischer | 827 | 620 | 504 | 15.452 |
| Karpov | 3.528 | 2.068 | 1.590 | 50.181 |
| Kasparov | 2.128 | 1.251 | 1.015 | 27.907 |
| Tal | 2.431 | 1.489 | 1.147 | 29.307 |
| **Total** | **8.914** | **5.428** | **4.256*** | **122.847** |

*Total de participações em FIT por perfil, não necessariamente trajetórias
únicas globalmente. TUNE/AUDIT, validação, holdout e meios-lances iniciais
não foram usados para ajustar pesos. O tamanho dos dados é desigual.

## Comparação pareada — 256 decisões em 234 partidas históricas

| Jogador | Maia3-5M | Maia3-79M | Diagnóstico 79M + pesos próprios |
|---|---:|---:|---:|
| Fischer | 35/64 | 33/64 | 27/64 |
| Karpov | 32/64 | 31/64 | 24/64 |
| Kasparov | 37/64 | 43/64 | 30/64 |
| Tal | 30/64 | 35/64 | 24/64 |
| **Total** | **134/256 (52,34%)** | **142/256 (55,47%)** | **105/256 (41,02%)** |

Diferença macro por partida, 5M menos 79M: **−2,56 pontos percentuais**,
IC95 bootstrap por partidas **[−6,20; +1,07] pp**. A diferença não
foi estabelecida estatisticamente pelo protocolo exploratório.

Diferença macro do adaptador diagnóstico menos Maia79:
**−13,68 pp**, IC95 **[−19,02; −8,55] pp**. O ajuste prejudicou
acentuadamente a predição na amostra avaliada. Todos os quatro gates
recusaram ativação, portanto a versão auditada manteve os 142/256
acertos do Maia79 sem alterações. Fischer, Karpov e Kasparov escolheram
força zero no TUNE; Tal experimentou 0,5, mas o AUDIT rejeitou
(CI95 [−23,21; +3,57] pp, 28 jogos distintos).

**Interpretação:** o maior checkpoint é baseline promissor; mais
partidas por si só não fizeram esse adaptador simples aprender identidade.
Estes números são exploratórios, pois a série de validação foi consultada
em versões anteriores; pesos Maia externos não foram decontaminados
independentemente dos PGNs históricos. Nenhum resultado é ganho de Elo,
fidelidade histórica ou autorização de uso oficial. O holdout final
permanece não utilizado. A CI não equivale a revisão adversarial.

O controle correto/genérico/trocado adicionado depois deste run exige
**nova execução no HEAD com seus próprios resultados**; não inferir
métricas dessa versão anterior para o novo relatório.
