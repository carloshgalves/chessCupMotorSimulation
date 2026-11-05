# Copa dos Imortais — orquestração eliminatória (v1)

A CLI mantém duelos individuais separados de um **torneio persistente**. Este
módulo implementa chave eliminatória com 2, 4, 8, 16, 32 ou 64 participantes,
resultados por pares de jogos com cores invertidas, checkpoints atômicos e
retomada após interrupção. Não inclui todos-contra-todos nem interface ao vivo.

## Pré-requisitos

- Rodent IV compilado e configurado; ambiente Python instalado.
- Perfis registrados para **todos** os participantes selecionados.
- Para `--preset official`, todos os livros exigidos pelos perfis devem existir.
- O sistema **não cria** os 43 perfis ausentes e não permite nomes fictícios.
- **Não troque binário, perfis ou livros durante um campeonato:** os hashes
  são congelados no início e revalidados antes da retomada.

## Criar uma Copa piloto de 4 jogadores

~~~bash
export RODENT_IV_HOME="$HOME/Projetos/Engines/rodent-iv"
python -m chesssim tournament-init \
  --players lasker marshall fischer tal \
  --preset official --seed 2026 \
  --out runs/copa-piloto-4
~~~

A inicialização realiza o pré-voo e cria **uma pasta nova**, recusando sobrescrita.
O torneio não inicia nenhuma partida nesta etapa. A semente embaralha os
participantes uma vez. O estado fica em `tournament.json` e registra o
chaveamento, os confrontos, hashes congelados e as decisões de arbitragem.

## Executar sem bloquear o computador em uma sessão longa

~~~bash
python -m chesssim tournament-run --run runs/copa-piloto-4 --limit-pairs 1
python -m chesssim tournament-status --run runs/copa-piloto-4
~~~

Repetir `tournament-run` retoma os confrontos restantes. Sem
`--limit-pairs`, executa todos os confrontos que conseguir até encontrar
um campeão ou um bloqueio. A opção `--limit-pairs` conta **pares de duas
partidas**, e não lances ou jogos isolados.

Um confronto registra suas tentativas em
`matches/rNN-mNN/duels/<participantes>/<tentativa>/` com PGN e JSON.
O orquestrador lê esses artefatos para reconstruir resultados no caso de
queda após gravação do duelo e antes da atualização do manifesto.

Os duelos utilizam o mesmo motor, os mesmos perfis e os mesmos relógios
congelados. O preset `official` é **10+2**, com livros internos habilitados.
Os presets experimentais usam os relógios já disponíveis no projeto e não
habilitam os livros internos.

## Parar uma Copa com segurança

O controle é feito por **outra aba do terminal**, sem encerrar à força a
execução ativa. O comando cria um pedido persistente e não precisa adquirir
o lock usado pelo processo que está jogando:

~~~bash
python -m chesssim tournament-stop \
  --run runs/copa-piloto-4 --reason "Encerrar sessão de hoje"
python -m chesssim tournament-status --run runs/copa-piloto-4
~~~

O motor verifica o pedido **antes de cada lance seguinte**. A solicitação não
interrompe instantaneamente uma busca UCI em andamento. O `SimpleEngine`
utiliza timeout de comunicação e tenta encerrar o motor no bloco de limpeza
(com `close` de fallback quando `quit` falhar). Não há garantia contra
desligamento forçado do sistema nem processos externos que ignorem o kernel.

Cada lance aceito gera um checkpoint atômico
`jogo_N.partial.pgn`, com resultado `*`; esse arquivo é evidência de
progresso, **não uma partida concluída nem um resultado para classificação**.
O PGN de cada jogo concluído e `summary.json` continuam sendo as fontes
oficiais do resultado. A saída do terminal mostra também os relógios
restantes após os lances quando houver preset de tempo.

Para retomar quando a sessão parar:

~~~bash
python -m chesssim tournament-clear-stop --run runs/copa-piloto-4
python -m chesssim tournament-run --run runs/copa-piloto-4 \
  --retry-incomplete --limit-pairs 1
~~~

`tournament-clear-stop` exige que nenhuma execução esteja ativa. A retomada
recomeça o **par de jogos** interrompido; os artefatos parciais continuam
arquivados e não entram na pontuação.

### Limites de tempo de operação

Os comandos aceitam também orçamentos de parede (wall-clock), distintos do
relógio enxadrístico:

~~~bash
python -m chesssim tournament-init \
  --players lasker marshall fischer tal \
  --preset official --seed 2026 \
  --max-game-wall-seconds 7200 \
  --out runs/copa-piloto-4

python -m chesssim tournament-run --run runs/copa-piloto-4 \
  --max-run-seconds 1800 --limit-pairs 1
~~~

Esses valores são exemplos, não configurações obrigatórias. O limite por
partida é **congelado na criação do torneio**; atingir esse orçamento marca a
partida `INCOMPLETE`, sem atribuir derrota/empate. O limite por execução
faz uma pausa graciosa e não se mantém entre chamadas. Os limites são
verificados entre lances, não são deadlines preemptivas durante uma busca
bloqueada. A limitação de CPU permanece em uma thread por motor do protocolo
e **não equivale a uma quota do sistema operacional**.

## Interrupções e empates

- Partida incompleta ou falha **não é declarada empate nem vitória**; o
  confronto fica `RETRY_REQUIRED`. Após diagnóstico, uma nova tentativa
  pode ser autorizada com `--retry-incomplete`, preservando o artefato anterior.
- Se houver empate no total do par com cores invertidas, joga-se outro par
  sob as mesmas regras. O limite é configurado em `--max-tiebreak-pairs`
  (padrão 3, incluindo o par inicial).
- Se o limite se esgotar e persistir a igualdade, o status será
  `TIE_PENDING`; o torneio **não inventa vencedor**.
- Uma decisão externa do árbitro, se houver, pode ser registrada, mas fica
  explicitamente rotulada como tal, jamais como vitória gerada pelo motor:

~~~bash
python -m chesssim tournament-resolve --run runs/copa-piloto-4 \
  --match r01-m01 --winner fischer \
  --reason "Decisão externa documentada em súmula de desempate"
~~~

Esse comando só é aceito para empate após o limite de pares. **É obrigatório
fundamentar a decisão externamente**; o sistema não valida uma partida de
desempate externa nem substitui um regulamento competitivo completo.

## Limitações operacionais

- O bloqueio de execução é local e impede duas chamadas simultâneas
  de `tournament-run` ou `tournament-resolve` sobre o mesmo torneio.
- Em caso de interrupção durante uma partida, `play_duel` conserva o que
  já tiver sido salvo, mas a tentativa incompleta não conta na tabela. Uma
  nova tentativa recomeça o par inteiro.
- O replay HTML existente é **pós-partida**, não transmissão lance a lance.
- A integração com interface ao vivo, todos-contra-todos e tiebreak por
  Armageddon automatizado permanecem etapas futuras. Nenhum perfil é
  apresentado como historicamente validado por esta implementação.

## Isolamento

Não realize `git switch`, `git pull` ou alterações nos arquivos do motor
enquanto um duelo estiver ativo. Execute um novo torneio somente depois que
a execução atual terminar e após instalar uma versão aprovada do código.
