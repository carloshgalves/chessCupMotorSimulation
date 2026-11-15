# Comparação real CPU — Maia3-23M, 79M, DSC-150M e adaptador V7

A PR sequencial depende da implementação e dos resultados da #144.
Sempre prediz as mesmas 64 decisões de VALIDATION por jogador,
com o histórico completo reconstruído a partir de PGNs de fonte fixa.

Modelos: Maia3 5M/23M/79M do CSSLab no mesmo código pinado,
checkpoint 23M na revisão HF \`51a0145a8178046f7de23119160b136672deeb2b\`,
5M/79M nos hashes da #144; DSC-150M em
\`ChessDS/chessds-dsc@229ef4291c38c088a9fd3a410e4c0e926a20cea1\`,
weights SHA256 \`66b21d7882bb3fb9737bc798e709671325838e5469ed6bfcf114b3abbcb48e24\`.
O DSC verifica pesos e config no registry do pacote e a CI verifica
o commit do código antes de executar. O Maia23M registra SHA256
efetivamente resolvido da revisão imutável; congelar também esse digest
na revisão posterior antes de extrapolar evidência.

Apenas checkpoint original, em CPU; nenhum modelo é treinado a partir
de dados de validação. A personalização do 79M é um diagnóstico de
força 1, já negativo na V7, não um motor aprovado.
Todos os modelos recebem os mesmos tabuleiros e histórico legal;
Maia \`Elo=2400\` é cenário de rating da distribuição de treino Lichess,
DSC \`Elo=2400\` é cenário FIDE com contexto de jogo clássico.
**Esses ratings não são equiparáveis**, nem inferências de ratings
históricos verdadeiros. A comparação mede desempenho sob contextos
declarados, não igualdade de escala.

Top-1, top-3, macro por jogo, IC95 por jogo e duração de execução
incluem custo de inicialização/CPU, mas não são benchmark de memória
pico (este ainda precisa de instrumentação própria se exigido no aceite).
Nenhum resultado de validação já consultada é teste final. Sem
promoção, sem override em partidas, sem leitura de HOLDOUT.

Não declarar #146 concluída até CI real verde, verificação de fontes,
publicação de métricas, medição de memória e revisão adversarial.
