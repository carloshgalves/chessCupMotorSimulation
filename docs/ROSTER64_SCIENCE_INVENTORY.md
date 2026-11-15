# 64 jogadores — auditoria de corpus científico (#143)

\`python -m chesssim.roster_science_inventory --fetch --all-roster
--position-audit --out resultado.json\` tenta os 64 slugs registrados,
consultando o snapshot imutável PGN Mentor/rozim já aceito nas PRs #135-#144.

O resultado distingue fonte HTTP 404 de erro de download/identidade/PGN.
Somente 404 genuíno é classificado \`SOURCE_MISSING_HTTP_404\`;
qualquer outra falha invalida a execução, nunca converte silenciosamente
uma exceção em ausência de evidência.

Cada fonte presente possui SHA-256, contagem de trajetórias distintas,
anos e fases da carreira observadas, braços train/validation/holdout por
anos inteiros, cobertura proxy de meio-jogo/final por lance do jogador
e repetição de FEN canônica de quatro campos, usando apenas posições
do braço TRAIN. O SQLite de hashes é temporário e o artefato publicado
não inclui partidas ou FENs brutos.

Menos de 100 jogos de validação OU menos de 100 de holdout implica
\`UNDERPOWERED_INDEPENDENT_EVALUATION\`. Mesmo cobertura suficiente é
apenas \`CANDIDATE_NOT_STYLE_VALIDATED\`. Transposição é aproximação
por reobservação de FEN, não demonstração de identidade tática.
Um mesmo jogo disputado por dois participantes pode entrar nas duas
fontes, e os cruzamentos exigem embargo global antes de ajustar qualquer
novo modelo.

O workflow de 64 fontes possui limite de 120 minutos de CPU do Actions.
A existência do workflow e do script não comprova que as 64 fontes
foram efetivamente baixadas/validadas. Publicar as contagens do artefato
real e a auditoria adversarial antes de declarar o aceite da issue #143.
