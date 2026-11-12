# #66 — Narração editorial fictícia no espectador (incremento 1)

O comando `watch --run ...` disponibiliza, em sua interface, o painel
**Comentários e coletiva fictícia**. O recurso permanece **desativado por
padrão**, é ativado/desativado pelo espectador e não guarda preferências
persistentes. Não existe chamada a LLM, TTS, serviço externo ou motor UCI.

## Fontes permitidas e limites

A API somente leitura `/api/state` entrega o campo
`fictional_editorial` de cada jogo. Os textos são produzidos por regras
estáveis que analisam exclusivamente **lances legais** da linha principal
PGN: início, capturas, xeques, roque, promoção, xeque-mate e marcos de
contagem de meios-lances. A categoria advém do estado `python-chess`
antes/depois do lance — nunca de comentários PGN ou de um nome próprio.

Cada evento inclui `ply`, `uci`, `kind` e `text`. Duas variantes de
frase são escolhidas deterministicamente com SHA-256 da posição **anterior**,
UCI e categoria; um novo lance ao vivo não altera um comentário já
produzido. Não se atribuem aspas, pensamentos, emoções, entrevistas,
falas, memórias ou intenção a enxadristas históricos. A referência é
**o personagem ficcional da simulação**, e a descrição é de fatos do tabuleiro.

A interface só mostra comentários cujo `ply` **já foi apresentado**
pelo replay local. Ela não revela o conteúdo do resultado final durante
a fila visual de lances, nem em posições anteriores à última. Ao trocar
o confronto, a tentativa ou o PGN subjacente, dados de um replay fixado
não são reutilizados contra uma trajetória diferente. Todos os textos
dinâmicos são inseridos via `textContent`, sem HTML de usuário.

## Coletiva pós-partida

A seção **Coletiva editorial pós-partida — fictícia** só é entregue quando:

1. Existe `jogo_N.pgn` final.
2. O recibo correspondente contém `status=COMPLETED`, resultado igual
   ao PGN, número de meios-lances e hash SHA-256 da trajetória compatíveis.
3. A posição final tem desfecho terminal jurídico do xadrez
   (`board.outcome(claim_draw=True)`) igual ao resultado reivindicado.

No MVP, uma **vitória por tempo, abandono ou arbitragem** sem posição
terminal não gera coletiva: esses motivos exigem prova de relógio/recibo
mais rigorosa em um incremento posterior. O comentário normal sobre
lances legais ainda pode aparecer, mas um mero `Result` no PGN não cria
coletiva. A seção é uma pergunta do **editor** com resposta do
**narrador fictício**; nunca representa declaração real de um jogador.

## Testes e evolução

A suíte cobre determinismo, captura, roque, promoção, xeque-mate,
injeção por nomes/PGN, recibo forjado, marcador de jogo final ausente,
posição não terminal, compatibilidade da API e o harness JS que testa:
botão opt-in, navegação regressiva sem spoiler, `textContent` inerte,
troca de jogo, replay fixado obsoleto e ausência de novas requisições.

A implantação física no Debian/Chrome do usuário e a acessibilidade
manual (teclado, leitor de tela, zoom e reduzido movimento) integram
#83 e permanecem `NOT_RUN` até ensaio documentado. A issue #66 continua
aberta para eventos mais ricos, idiomas e opções narrativas sem alterar
as trajetórias oficiais dos motores.
