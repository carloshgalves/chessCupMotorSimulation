# Auditoria das probabilidades da temporada rápida — issue #61

A temporada rápida existente, `fast_statistical`, devolve resultados sintéticos determinísticos a partir da seed. Ela **não é um modelo calibrado de força enxadrística**, não produz PGNs e não participa do Elo competitivo comprovado. Esta entrega acrescenta **medição offline**, sem mudar os resultados do modo rápido nem criar probabilidade condicional por jogador.

## Como usar

~~~bash
python -m chesssim fast-audit \
  --workspace "$HOME/Projetos/Homologacao/ChessCup/arena" \
  --out "$HOME/Projetos/Homologacao/ChessCup/fast-audit.json"
~~~

O `workspace` precisa conter a estrutura `leagues/` de temporadas produzidas pelo ChessCup. O auditor reutiliza `verified_league_games()`, que verifica manifestos, recibos e os PGNs reais dos jogos `engine_full` antes de liberar cada registro. Temporadas com prova danificada bloqueiam o relatório comparativo inteiro, impedindo a seleção oportunista apenas dos resultados válidos. Registros `fast_statistical` são contabilizados **exclusivamente como projeções excluídas**: não entram no conjunto de treino ou teste.

O arquivo JSON é criado exclusivamente (`--out` não pode existir), **fora do workspace de partidas** e em diretório regular sem symlinks. A execução não chama UCI, não atualiza ratings, não altera partidas e não acessa a rede, o corpus de estilo histórico nem o holdout lacrado.

## Protocolo da avaliação

1. **Fonte:** somente resultados de liga `engine_full` com recibo e hash PGN verificados; identidade da competição, modalidade e timestamp exigidos.
2. **Partição:** as competições são ordenadas por criação; as mais recentes formam o holdout de avaliação. Nunca se divide a mesma competição entre treino e teste. Necessários ao menos **quatro eventos** e **20 partidas verificadas em cada partição**.
3. **Previsão do modo rápido:** probabilidades nominais de 38% vitória branca, 24% empate e 38% vitória preta, conforme os limiares do `projected_result` atual. A seed determina o resultado amostrado; não representa rating, força ou características dos jogadores.
4. **Comparador:** frequência W/D/L no conjunto de treino, com suavização de Laplace `alpha=1`. Os resultados do holdout **nunca** são usados para escolher parâmetros ou calcular essas frequências.
5. **Métricas:** Brier multiclasses (soma dos três erros quadráticos por partida), log loss, frequência W/D/L observada, vantagem descritiva de brancas `P(1-0)-P(0-1)`, taxa de empates e diagnósticos por modalidade. A diferença `fast - baseline` positiva significa pontuação de perda pior para o modo rápido nesse conjunto.
6. **Incerteza:** com pelo menos cinco competições no holdout, intervalos percentis de 95% por bootstrap **agrupado por competição**, 300 réplicas, seed fixa. Com menos de cinco competições, o intervalo é `null`, não um intervalo artificialmente estreito por lance ou partida.

O relatório inclui SHA-256 canônico dos registros de entrada, IDs dos eventos de treino e teste, números de jogos, **hashes de versões do motor e perfis usados**, e descrição explícita da avaliação. O pré-voo exige os hashes de engine/perfis autenticados pelo recibo do projeto. Se o holdout incluir modalidade ou revisão de motor não observada no treino, o relatório sinaliza `DISTRIBUTION_SHIFT_DESCRIPTIVE_ONLY` e a limitação de generalização. Entrada insuficiente resulta `INSUFFICIENT_EVIDENCE` sem métricas de comparação. Qualquer temporada danificada resulta `BLOCKED_UNVERIFIED_SEASONS`.

## Interpretação correta

O status `DESCRIPTIVE_HOLDOUT_NOT_CALIBRATED` significa apenas que **a comparação foi computada usando evidência UCI autenticada pelo projeto**; não significa que o modelo rápido tenha boa calibração, realismo, poder preditivo por jogador ou distribuição estável no tempo.

A análise está sujeita à escolha dos participantes, relógios, motores, número de temporadas e dependência entre partidas de engines determinísticos. A separação por temporada reduz vazamento entre treino/teste, mas **não prova independência causal** ou ausência de pseudorreplicação. O rating inicial arbitrário 2500 da interface não entra nas previsões.

## Pendências da issue #61

Esta fatia fornece a instrumentação quantitativa. Ainda faltam séries reais de ligas UCI, cobertura de força e tempo suficientes, validação contra outros baselines, modelo treinável baseado em força observada, gates de calibração out-of-sample e medições de tempo/CPU no Debian com Rodent IV. É proibido converter a distribuição fixa atual em `calibrated` sem essas evidências; nenhuma implementação de combate, partidas, rating ou UI foi alterada.

Testes automatizados usam registros sintéticos verificados na função de avaliação para provar aritmética, particionamento, bloqueios, integridade, reprodutibilidade e isolamento, **não para afirmar desempenho de partidas UCI reais**.
