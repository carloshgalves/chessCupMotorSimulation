# Issue #57 — Duelo e Jogar (humano contra Rodent IV)

## Produto e UX

A Arena local possui duas opções lado a lado: **Duelo** (motor × motor, implementação preservada) e **Jogar** (humano × um perfil Rodent IV realmente instalado). A implementação utiliza a **primeira proposta visual aprovada**, com configuração compacta à esquerda, tabuleiro no centro, lances/informações/resultado à direita e **sem botão Limpar**.

O modo Jogar permite escolher o adversário do catálogo, peças brancas/pretas/aleatórias, controle de tempo (clássico, rápido, blitz, bullet, sem relógio ou personalizado), profundidade 1–16 e livro interno UCI opcional. A lista de 64 perfis não implica que todos estejam instalados; perfis não disponíveis permanecem desabilitados. Status do perfil **não comprova** reprodução histórica da pessoa.

O tabuleiro usa o mesmo renderer, peças e temas aprovados na PR #76; clique na peça seguido da casa de destino, ou Enter/Espaço nas casas com foco. A promoção oferece Dama, Torre, Bispo e Cavalo. Quando o humano joga de pretas, tabuleiro, nomes e relógios são orientados para o jogador na parte inferior. A notação é numerada com brancas à esquerda e pretas à direita.

**Cadência do espectador:** respostas UCI rápidas continuam acontecendo em tempo real no backend; a interface apresenta até um meio-lance a cada 650 ms para preservar leitura, sem atrasar o motor nem conceder tempo extra. Enquanto o replay visual está atrasado, novos lances humanos ficam temporariamente bloqueados, evitando ações sobre uma posição visual desatualizada.

## Regras e segurança

- Somente o servidor local, com `python-chess`, decide lances legais, vez, resultado, cheque-mate, afogamento, repetição, regra dos 50 lances, desistência e tempo. O navegador **não** fornece FEN arbitrário, caminho de executável ou parâmetros de shell.
- Os POSTs exigem Host loopback permitido, Origin igual à origem do servidor, token de 32+ caracteres emitido no fragmento da URL e Content-Type JSON. Payload tem limite de 4 KiB; chaves duplicadas, chaves extras, tipos inválidos, lances ilegais, lances fora do turno e revisões antigas são recusados.
- Um único motor ou duelo pode executar por workspace de Arena. O modo Duelo mantém seus contratos; parar um Duelo não é a mesma coisa que interromper uma partida humana.
- Lances humanos usam `expected revision`/ID de sessão para impedir aplicação repetida. O motor recebe limites UCI (`depth`, `wtime/btime`, `winc/binc` quando houver relógio); startup, reflexão, execução UCI e desconexão são contabilizados pelo relógio mantido no servidor.
- **Desistência** gera resultado definitivo. **Reivindicar empate** só funciona na vez humana e quando `python-chess` reconhece situação reivindicável. **Oferecer empate** não é uma negociação UCI real; a ação informa que a oferta foi recusada, porque o protocolo UCI não possui canal de aceitação. Nunca inventar concordância do motor.
- O navegador pode fechar sem interromper a partida: o relógio permanece no servidor, e a reabertura recupera o último estado. Encerrar o serviço torna uma partida não concluída `INTERRUPTED`; a retomada exige ação explícita e verifica que hashes de motor e perfil permanecem iguais.
- Checkpoints são escritos por substituição atômica, com PGN antes do manifesto. Divergência de número de lances detectada na reabertura marca o jogo `FAILED/CheckpointMismatch` e proíbe uma retomada que poderia restaurar tempos indevidamente.
- Ao desistir, interromper ou encerrar o serviço, o transporte UCI em execução é encerrado; resultados de workers antigos são ignorados por `session_id`, `revision` e geração interna de execução.
- Existe um orçamento de segurança de tempo de sessão, ampliado para comportar relógios clássicos ou personalizados longos; partidas sem relógio têm limite de execução do servidor. O modo Jogar não utiliza avaliação Stockfish obrigatória.
- PGNs de partidas humanas ficam em `<workspace>/human-games/human-<id>/game.pgn`, com hashes, perfis, modalidade, limites e relógios por lance. Manifestos da mesma pasta guardam estado e relógios. A API pública não oferece caminhos arbitrários nem serve esses arquivos diretamente.

## API local

| Método | Rota | Função |
| --- | --- | --- |
| GET | `/api/arena/human` | Estado/posição/PGN reconstruído em FENs e lances |
| POST | `/api/arena/human/start` | Cria partida com perfil instalado |
| POST | `/api/arena/human/move` | `{id,revision,uci}`, um lance legal |
| POST | `/api/arena/human/action` | `{id,action}`: `resign`, `claim_draw`, `offer_draw`, `interrupt` |
| POST | `/api/arena/human/resume` | `{id}`, retoma uma partida interrompida elegível |

**Não há Elo FIDE, Elo competitivo dos 64 motores nem rating de carreira atribuído ao humano nessa entrega.** As partidas humanas não são misturadas ao ledger de Elo experimental da PR #77; integração futura com arquivos da simulação pertence à #70.

## Homologação

1. No Debian 13, com Rodent IV validado: `python -m chesssim arena --rodent ~/Projetos/Engines/rodent-iv --out ~/chesssim-pr78-evidence --port 8766`.
2. Verificar modo Duelo com dois perfis, inversão de cores, profundidade + relógio e replay existente.
3. Selecionar Jogar: adversário instalado, brancas, blitz 3+2; efetuar um lance legal, rejeitar jogada ilegal, acompanhar resposta UCI e cronômetros.
4. Tentar clique repetido na mesma jogada, ação antes da vez e troca de abas História/Arena. Confirmar nenhum duplo lance.
5. Jogar de pretas: motor deve abrir; nome/relógio do humano ficam na base e peças devidamente orientadas. Testar promoção e reivindicação de empate apenas quando disponível.
6. Testar botão de desistência e reentrada, botão de interrupção e retomada, PGN resultante; deixar um tempo expirar e verificar a adjudicação.
7. Sem motor instalado, opção fica indisponível; não apresentar partida simulada por dados artificiais.

**Limitação de gate:** CI contém subprocesso UCI sintético, testes de segurança HTTP, relógios, PGN e testes estáticos/Node do frontend. A inspeção visual e partidas reais com Rodent IV em Debian ficam para homologação do mantenedor; a PR não é mesclada automaticamente.

## Incremento #89 — Arrastar e soltar no modo Jogar

O tabuleiro pessoal aceita **arrastar com mouse, toque ou caneta** através de Pointer Events, sem substituir os controles existentes de clique na peça seguido da casa ou Enter/Espaço. Após deslocamento de 8 px, **o próprio nó gráfico da peça já carregada** passa a acompanhar o ponteiro (sem cópia da casa, fundo colorido, moldura ou sombra do container). A casa de origem fica vazia durante o gesto e recebe apenas um contorno discreto; os destinos legais continuam destacados. O ponto onde o usuário pressionou a peça é preservado, evitando salto quando o arraste começa. Em cancelamento, drop ilegal ou fora do tabuleiro, o mesmo nó retorna à origem, sem recarregar o SVG. **Nenhum POST de lance é enviado** nessas situações. O comportamento foi ajustado após comparação visual dos vídeos de homologação do ChessCup e da referência Chess.com; não se reutilizam imagens nem outros assets proprietários.

- A origem só pode ser selecionada se estiver na lista `legal_moves` do snapshot atual e o usuário estiver na vez correta, com a posição exibida sincronizada, sem comando/promoção pendente. Durante o turno do motor, apenas **uma intenção de pré-move** pode ser mantida no frontend. Ela não é jogada/armazenada pelo servidor até chegar a vez humana; #90 implementa essa etapa.
- A lista do servidor é consultada também antes do drop, e a operação usa o mesmo `moveFromTo` de clique/clique para promoções (D/T/B/C). Só `/api/arena/human/move` pode aplicar a jogada; ID de partida, revisão e legalidade são revalidados no backend `python-chess`.
- `pointercancel`, perda de captura, ocultação da aba, foco perdido, troca de modo, sessão, turno ou revisão cancelam o gesto sem enviar lances. A compatibilidade `click` após um arraste não provoca jogada duplicada.
- Para humano de pretas, a área visível é invertida e o mapeamento para coordenadas UCI é feito pela posição visual e `squareName` existente. O drag não altera DOM da notação, relógio, PGN ou replay.
- Estilos de peças/temas continuam sendo definidos em `board_preferences.js`. O arraste usa prévia local, sem dependência externa. O CSS desabilita scroll por toque **apenas dentro do tabuleiro**; fora dele, a rolagem permanece normal. `prefers-reduced-motion` continua respeitado.
- Setas de anotação via botão direito são outro incremento (#91) e não integram esta PR.

**Homologação adicional:** testar Chrome/Debian com mouse e touch (se disponível), arraste legal, fora do tabuleiro, capturas, promoções e preta invertida; verificar que relógios continuam sob controle do servidor. CI/Node não comprova ergonomia real em hardware.


## Incremento #91 — Setas de análise e casas marcadas (Chess.com como referência)

As anotações são **somente visuais no modo Jogar**. Não são lances, pré-moves, análise da engine nem comandos de relógio. Não são gravadas em PGN, JSON ou `localStorage`; nenhuma requisição HTTP contém esses desenhos. A geometria é um SVG local com `viewBox="0 0 800 800"` sobre o tabuleiro, alinhado às 64 casas sem participar de `board.children`.

Referência funcional oficial, consultada em 10/11/2026: https://support.chess.com/en/articles/8568781-how-can-i-draw-arrows-on-the-board-like-streamers-do

| Operação no desktop | Resultado |
| --- | --- |
| Arrastar com botão direito | Seta amarela origem → destino |
| Ctrl + arrastar com botão direito | Seta vermelha |
| Alt + arrastar com botão direito | Seta azul |
| Shift + arrastar com botão direito | Seta verde |
| Clique direito na mesma casa | Casa vermelha |
| Ctrl + clique direito | Casa amarela |
| Alt + clique direito | Casa azul |
| Shift + clique direito | Casa verde |
| Clique esquerdo dentro do tabuleiro, Escape ou Limpar setas | Remove todas as anotações |

Um segundo gesto idêntico remove a seta/marca da mesma cor; selecionar outra cor substitui a anterior para o mesmo par de casas. A seta pode ligar qualquer par de casas — **não tem relação com legalidade do xadrez**. Um arraste que termina fora do tabuleiro é cancelado; botão direito não inicia o arraste de peças. Desabilitar menu contextual se limita ao tabuleiro do modo Jogar, e o SVG está com `pointer-events:none`.

### Ciclo de vida e acessibilidade
- A UI informa a quantidade de setas e casas marcadas através de status `aria-live`.
- O botão `Limpar setas` oferece alternativa ao mouse; Escape com foco no tabuleiro também limpa. Nenhum atalho captura o botão direito fora dele.
- A mudança de sessão, avanço de revisão da partida ou saída do modo Jogar descarta as anotações, mas não move peças. Durante o turno do motor elas podem ser desenhadas normalmente.
- O `viewBox` e o mapeamento de orientação em `human_play.js` mantêm posição correta de brancas/pretas e escalonam com zoom e viewport; as cores são fixas e independem de temas ou conjuntos SVG.
- Esta implementação é desktop (mouse com botão direito, caneta com botão secundário quando mapeada pelo navegador); não oferece gesto touch específico para setas. Movimentação normal por toque segue funcionando.
- O **pré-move #90 foi implementado em um incremento independente**. Quando há uma intenção aguardando, clique direito curto em qualquer casa tem prioridade sobre marcar casa; arraste com botão direito continua criando setas. Sem pré-move pendente, o comportamento das anotações permanece inalterado.

### Homologação Chrome/Debian
Verificar 4 cores de setas e 4 cores de casas, trajetória entre casas não legais, orientação de pretas, zoom 100–200%, tema Verde/Chessnut e outro tema, ausência de menu contextual, clique esquerdo limpando sem impedir lance, botão e Escape limpando, anotações durante turno do motor, ausência de POST e de mudanças de PGN/relógio. Testes automáticos simulam Pointer Events e geometria, mas **não substituem inspeção visual real do Chrome**.

## Pré-moves (issue #90)

Inspirado na interação do Chess.com, as casas de origem e destino ficam realçadas em vermelho enquanto um único pré-move aguarda. O jogador pode preparar o lance por arraste ou dois cliques durante a reflexão do motor e cancelar enquanto aguarda pelo botão, Escape ou clique direito em qualquer casa do tabuleiro. Um arraste com o botão direito continua produzindo setas sem cancelar a intenção. Uma segunda intenção substitui a primeira; a promoção abre o seletor D/T/B/C existente. A peça ainda **não se move** no tabuleiro real.

O pré-move é efêmero, em memória do navegador, identificado por `game.id` e `revision`. Ao receber um snapshot da próxima vez humana, o cliente verifica `legal_moves` do servidor e envia uma única vez `{id,revision,uci}` com a **revisão nova**. Enquanto a requisição estiver em andamento, mantém a intenção destacada em vermelho com status de execução; o cancelamento não tenta desfazer lances enviados. O destaque é removido quando a revisão aceita é confirmada por GET e o tabuleiro mostra imediatamente a posição efetiva. Se estiver ilegal, a sessão mudar/terminar ou houver outra ação pendente, descarta e informa; nenhuma alteração em PGN, motor, Elo ou relógio é produzida por uma intenção. O backend mantém a validação autoritativa e o relógio habitual; **não** simular os 0,1 segundo usados pelo Chess.com, que são regras específicas daquela plataforma. Um clique direito curto em casa diferente da origem descarta a intenção ainda aguardando sem gerar marcação, POST ou mudança de relógio/PGN. Um clique direito durante o POST não desfaz o lance; `pointercancel` antes do soltar não cancela a intenção.

Fonte de comportamento e visual (consultada 10/11/2026): https://support.chess.com/en/articles/8562432-what-are-pre-moves-and-how-do-they-work. A plataforma externa permite uma fila maior; a versão inicial do ChessCup deliberadamente limita a uma intenção.

**Gate operacional:** ensaio físico com Rodent IV e Chrome/Debian, touch/pen, orientação preta e promoção permanece `NOT_RUN` em #83. CI/harness simulados não são homologação visual.
