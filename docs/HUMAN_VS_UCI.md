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
