# Real85 — banco de partidas reais para os 64 jogadores

Objetivo: preparar um conjunto global de partidas documentadas para treinar e comparar
Fischer, Tal, Karpov, Kasparov e, posteriormente, o restante do elenco. A fonte é
o snapshot PGN Mentor/rozim fixado pelo projeto. Não representa todas as partidas
da história nem comprova direitos de redistribuição do corpus.

## Execução

    python -m chesssim.real85_neural_stream build --fetch --all-roster --allow-missing --out /tmp/real85-historic-private
    python -m chesssim.real85_roster_pool_report --inputs /tmp/real85-historic-private --out /tmp/real85-roster64.json

O primeiro comando lê todas as fontes correspondentes aos 64 perfis. Não há corte
arbitrário em 128 partidas, nem máximo de decisões por jogo. Cada PGN completo
tem identificador por trajetória, e os lances elegíveis de ambos os lados são
preservados conforme a identidade do jogador representado.

A mesma partida que aparece no arquivo de dois grandes mestres tem apenas
uma identidade global, mas produz exemplos individuais para o lado histórico
de cada um. O relatório publica contagens brutas e globais, quantas fontes
estão ausentes, aberturas distintas de 12 meias-jogadas e distribuição de
lances por número de ply. **Ply 61+ não comprova final de jogo**.

Os jogos do TEST histórico são excluídos da serialização; FIT e TUNE são
determinísticos por hash da trajetória, globalmente, para impedir que um
mesmo jogo esteja em FIT para um jogador e TUNE para outro. As fontes ainda
podem conter erros de identidade, partidas históricas ausentes e falta de
metadados; a integridade técnica não corrige esses defeitos de coleção.

O workflow CI de 64 jogadores executa a validação integral e publica
apenas o manifest e os números, não PGNs nem trajetórias individuais.
Não confundir volume de lances, qualidade de dados ou diversidade de
aberturas com rating humano, força tática ou fidelidade histórica.

## Treinamento e avaliação posteriores

Os jogadores marcados como READY podem ser treinados individualmente com
o residual experimental já existente, ainda que outra fonte (por exemplo,
Menchik) esteja indisponível. O treinamento de todos os 64 modelos e sua
integração ao motor **não** são resultados desta fatia. O residual atual
é pequeno, por CPU, e não substitui o ajuste de Maia/DSC ou uma força
competitiva historicamente calibrada.

O experimento anterior usou **Rodent IV depth 4** como motor que escolhe os
lances. Stockfish depth 8/12 analisava possibilidades de substituição
neural e fazia uma checagem de segurança: não jogava sozinho a partida.
Assim, muitos erros de meio-jogo ou final podem vir da força do próprio
Rodent em profundidade baixa, da política neural ou do limite da análise,
e não necessariamente da personalidade histórica. A comparação relevante
a seguir deve usar adversários em diferentes forças reais, abertura
diversificada, cores invertidas e medições das decisões por posição, sem
selecionar parâmetros a posteriori com base em jogos já observados.
