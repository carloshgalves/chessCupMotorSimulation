# Tal × Fischer — estabilidade da referência de qualidade (diagnóstico d24)

## Disposição oficial do primeiro teste integral

Execução original [#45258562373](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/45258562373),
mesclada na PR #214, gerou o artefato
`full-historical-quality-parity-report` (#14859119343).
JSON SHA256 **febdd827c726348ac0560c347de1f785ba5b754eb6a98dd8dcb01dd085062624**.
Todas as **887/887 posições** e os 32 shards têm evidência individual.
Estado original é **`INCONCLUSIVE_NEEDS_HUMAN_QUALITY_SUPPORT`**;
`release_eligible=false`, intervalos de confiança oficiais `null`.

| Amostra histórica | Decisões | Busca global d20 divergiu >35cp da busca de raiz |
| --- | ---: | ---: |
| Fischer 1970–1972 | 238 | **5 (2,10%)** |
| Fischer outras épocas | 231 | **9 (3,90%)** |
| Tal 1957–1961 | 218 | **5 (2,29%)** |
| Tal outras épocas | 200 | **2 (1,00%)** |
| **Todas** | **887** | **21 (2,37%)** |

Ambas as amostras do auge excedem o limiar **pré-fixado de 2%**,
razão suficiente para barrar a inferência. Não se pode elevar o
limiar depois de observar essa falha.

O protocolo usa Stockfish d20 duas vezes na mesma posição:
a melhor linha da busca MultiPV sem restrições, e depois uma
análise de raiz forçada para cada movimento do humano e dos
motores. Em 21 decisões, a segunda análise atribuiu a outro
lance mais de 35 centipeões acima do resultado da referência
forçada. Isso mede **instabilidade do avaliador de referência**,
não um erro de xadrez de Tal ou de Fischer.

## Resultados condicionais e NÃO homologatórios

Para identificar se o problema é apenas o controle técnico da
referência ou também a qualidade, foi feita uma análise de
sensibilidade **sobre os mesmos 887 registros originais**, usando
as mesmas métricas e bootstrap agrupado por partida (2.000
amostras). Essa análise não remove os casos inconsistentes,
não altera os lances e não muda o limiar de 2%.

| Diferença Tal–Fischer no excesso de perda contra os respectivos humanos | Estimativa | IC95 exploratório | Margem congelada |
| --- | ---: | ---: | ---: |
| Média equilibrada por fase | **−11,96 cp** | **[−26,21; +1,24] cp** | Limite superior ≤ +15 cp |
| Posições táticas pré-lance | **−24,60 cp** | **[−66,37; +9,45] cp** | Limite superior ≤ +20 cp |
| Frequência de perdas graves (>100cp) | **−2,73 pp** | **[−7,98; +2,50] pp** | Limite superior ≤ +5 pp |

Valores negativos favorecem Tal Balanced. Todos os critérios
**teriam resultado favorável condicionalmente**, mas não
constituem aprovação: a estabilidade da referência, exigida
previamente, falhou. O benchmark não prova Elo FIDE histórico
nem domínio absoluto da qualidade de sacrifícios.

## Correção experimental sem alteração oportunista do gate

A nova avaliação é um **diagnóstico posterior ao resultado**,
não uma reinterpretação das 887 posições:

1. Reconstruir os 32 artefatos originais do mesmo run via
   `actions/download-artifact@v4`, checar o SHA literal do
   relatório e validar **todas as 887 posições** contra as
   partidas históricas.
2. Selecionar **todas as 21 posições** com referência
   instável, independentemente de favorecerem Tal ou Fischer.
3. Selecionar **21 posições-controle** estáveis, pareadas
   previamente por jogador, época e fase, usando SHA
   determinístico e sem consultar os lances do motor.
4. Reavaliar **cada movimento humano, original e dos motores**,
   além das duas linhas de referência originais e das duas
   melhores linhas novas, com Stockfish **d24** e buscas
   forçadas idênticas para todas as alternativas.
5. Relatar a taxa de divergência residual e instabilidade
   nova nos controles. Nenhum caso é removido do
   denominador original, e a incerteza do d20 não é
   apagada silenciosamente.

O Stockfish deve ter SHA256 exato
`250ff9c3548d06b06616356b01972230607469c95fb187e262f7044c4f6cfbe9`.
O protocolo de d20, arquivo histórico, posições, motores e
perfil TalBalanced mantêm seus hashes originais.

O novo artefato será
`tal-fischer-depth24-reference-report`, mas seu status
continuará **`REFERENCE_INSTABILITY_DIAGNOSED_RELEASE_STILL_BLOCKED`**,
mesmo que todas as inconsistências desapareçam em d24.
Uma repetição de equivalência metodologicamente limpa exigiria
novo protocolo completo fixado antes da medição, com resolução
das instabilidades para **todos** os casos necessários.

## Higiene operacional

A PR #214 foi mesclada apesar de seu workflow ter sido criado
para executar uma única vez. Este patch remove da `main` o
workflow temporário `historical-quality-full-once.yml`;
o workflow permanente `historical-move-quality-parity.yml`
permanece disponível. O processo de verificação d24 é restrito
à branch de diagnóstico, sem execução automática de outros
modelos e sem promoção do Tal oficial.

A PR #211 de homologação de Tal permanece **Draft** e deve
continuar assim até haver demonstração robusta na mesma escala
aceita para Fischer, com exceções de mate e decisões táticas
preservadas.
