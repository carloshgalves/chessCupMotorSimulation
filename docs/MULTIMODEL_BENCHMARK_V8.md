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


## Revisão pós-primeiro benchmark real (HEAD 9960c47)

[GitHub Actions 43662441827](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43662441827)
confirmou execução real em CPU de todos os checkpoints e código externo pinado,
em 256 decisões de 234 jogos históricos distintos.
Artefato #14140953409, ZIP SHA256
`0e9da727fc8bc75e4e787a98877232023923a37944e22036fa68ba8797853e06`.
Relatório SHA256 lógico
`9703ff95fd73b8d039208ca04623961bba2cf22a8ab940d0022979bee416f32f`.

| Modelo | Top-1 | Top-3 | Tempo total CPU/engine (s) |
|---|---:|---:|---:|
| DSC-150M | 157/256 (61,33%) | 233/256 (91,02%) | 109,43 |
| Maia3-79M | 142/256 (55,47%) | 213/256 (83,20%) | 249,46 |
| Maia3-23M | 139/256 (54,30%) | 214/256 (83,59%) | 77,71 |
| Maia3-5M | 134/256 (52,34%) | 207/256 (80,86%) | 27,29 |
| Maia79 adaptação diagnóstica | 105/256 (41,02%) | **NÃO AFERIDO** | — |

O relatório da primeira CI representou incorretamente top-3 do adaptador
de uma única sugestão como top-1; isso foi corrigido no novo código
para `null`, sem inventar candidatos não avaliados.

DSC-150M menos Maia79 em macro por jogo: **+5,769 pp**,
IC95 bootstrap pareado **[+0,641; +11,325] pp**, p exploratório
não corrigido `0,0405`; não extrapolar como confirmação final.
**Por jogador**: Fischer DSC 41 vs Maia79 33; Karpov 32 vs 31;
Kasparov 43 vs 43; Tal 41 vs 35 (todos por 64 decisões).
O código confirma checksum original do Maia23M observado neste run
`bce6cd1af5f0399ac7eed33fabb7a6a2ef6193662c2740f262bf93af7bfb3569`
para futuras execuções. Nova CI será exigida nesse HEAD após a revisão.

Os tempos incluem inicialização/execução de inferência no mesmo runner CPU
e são descritivos, não comparações de força nem independência operacional.
A nova versão registra também VmHWM (pico de RSS do subprocesso UCI real
no Linux) por modelo; não estimar RAM com valores anteriores porque
o run de evidência acima não a mediu.
