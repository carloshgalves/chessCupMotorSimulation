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
faz uma pausa graciosa e não se mantém entre chamadas.

**Proteção adicional da busca UCI (#27):** a cada `go`, quando existe
`--max-game-wall-seconds`, um watchdog independente aguarda somente o
**tempo restante da partida** e, se vencer, encerra o transporte UCI da
busca em andamento. O timeout interno do `SimpleEngine` também é reduzido
quando aplicável. Isso é necessário porque, em buscas por profundidade ou
relógio, `python-chess` não aplica automaticamente `SimpleEngine.timeout`
ao `play()` sem `Limit.time`. Se o orçamento da partida expirar,
o resultado é `INCOMPLETE` com `Termination=WallTimeLimit`. Se o próprio
transporte UCI detectar timeout por outra razão, usa
`EngineResponseTimeout`; a queda do motor durante a busca usa
`EngineTerminated`. **Sem um orçamento explícito, não há garantia de timeout
por lance nas buscas de profundidade/relógio.** Nenhum desses eventos é derrota,
vitória ou empate enxadrístico.

Lances recebidos após o orçamento de parede são descartados, preservando o
checkpoint anterior; o transporte do motor sem resposta é fechado diretamente,
sem aguardar `quit` UCI. Um duelo interrompido não inicia o jogo com cores
invertidas e não é computado como confronto finalizado. Uma nova tentativa
deve ser autorizada via `--retry-incomplete`.

O watchdog e o timeout do `python-chess` limitam **a espera do
aplicativo sob um orçamento explícito**, mas não equivalem a um prazo
incondicional do sistema operacional
(`SIGKILL`, falha elétrica, processo em espera ininterruptível, suspensão do
host). O pedido `tournament-stop` continua cooperativo, verificado entre
lances. A limitação de CPU permanece em uma thread por motor do protocolo
e **não equivale a uma quota rígida do kernel**.

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


## Diagnóstico após SIGKILL ou queda de energia (#27)

Antes de retomar um torneio após encerramento não cooperativo, com a execução
original **parada**, use:

~~~bash
python -m chesssim tournament-recovery-preview --run /caminho/torneio
~~~

É uma inspeção **somente leitura**: não chama Rodent IV, não cria lock,
não altera PGNs, relatórios, classificação nem tournament.json. O resultado
é um JSON com o estado checkpointado, tentativas completas encontradas no
disco que ainda não constam do manifesto, tentativas incompletas e confrontos
que exigem autorização explícita de nova tentativa. O campo **recovery_gate**
prevalece sobre o status antigo do checkpoint: EXPLICIT_RETRY_REQUIRED exige
decisão do operador, STOP_REQUESTED exige liberação prévia do pedido de parada,
TIEBREAK_DECISION_REQUIRED exige arbitragem, COMPETITION_COMPLETE indica fim
e NO_INCOMPLETE_ATTEMPTS_DETECTED informa apenas que não foram encontradas
tentativas incompletas (não autoriza iniciar execução concorrente).

**Janela adicional coberta:** se o processo receber SIGKILL após criar a
pasta da tentativa, mas **antes da gravação atômica inicial de summary.json**,
essa pasta é registrada como MISSING_SUMMARY. Ela não é interpretada como
partida concluída, nem descartada silenciosamente. Arquivos partial.pgn,
se existirem, permanecem preservados. A retomada sem autorização entra em
BLOCKED e não inicia outro par automaticamente.

Quando o operador verificar a situação e optar por nova tentativa:

~~~bash
python -m chesssim tournament-run \
  --run /caminho/torneio \
  --rodent /caminho/RodentIV \
  --retry-incomplete
~~~

A tentativa anterior permanece indicada como excluída. Uma partida completa
com summary.json, duelo.pgn, hashes, cores e resultados validados pode ser
reincorporada à classificação sem ser jogada novamente. Tentativas já
registradas que perderam o resumo, arquivos corrompidos e diretórios/arquivos
simbólicos provocam erro explícito, em vez de recuperação insegura.

## Fatia de durabilidade POSIX — checkpoints de confirmação (#27)

As gravações de confirmação `tournament.json`, `summary.json`,
`jogo_N.pgn` e `duelo.pgn` passaram a usar **publicação atômica com
sincronização explícita** em sistemas POSIX (incluindo Debian):

1. Criar arquivo temporário na **mesma pasta** do destino; escrever e
   `flush()` seguido de `fsync()` no descritor do temporário.
2. Substituir o destino via `Path.replace()` (rename atômico dentro do mesmo
   filesystem).
3. `fsync()` no diretório que contém o arquivo e seus ancestrais, para
   solicitar persistência dos nomes, inclusive pastas de tentativa recém-criadas.
4. **Se qualquer sincronização falhar, a operação lança exceção.** Antes
   do rename, o destino antigo continua íntegro. Depois do rename, o destino
   novo pode estar visível mas não foi confirmado como durável: o operador
   precisa executar a inspeção de recuperação, **não presumir commit**.

Em POSIX, o marcador `.stop-requested.json` é preparado em um
temporário no mesmo diretório, faz `flush/fsync` **antes de ser
publicado por hardlink exclusivo** (`link(2)`): nunca sobrescreve outro
pedido de parada e não se torna visível parcialmente escrito. Depois,
o temporário é removido e os diretórios são sincronizados. Se
`fsync(directory)` falhar após a publicação, o erro é propagado e o
operador pode repetir a solicitação idempotente. Marcadores preexistentes
que sejam symlink ou não sejam arquivos regulares são recusados.
A remoção pelo comando `tournament-clear-stop` sincroniza a pasta.
Em ambientes não POSIX, permanece a semântica preexistente de substituição
atômica, **sem alegação de durabilidade por `fsync`**.

**Escolha deliberada de desempenho:** `jogo_N.partial.pgn` continua sendo
gravado atomicamente **sem fsync a cada meio-lance**. Isso reduz latência,
uso de SSD e interferência no motor, mas significa que, após queda de energia,
a última posição pode não ter sido persistida. Checkpoints parciais não
são resultados oficiais, e qualquer tentativa incompleta exige
reconciliação e autorização explícita para novo jogo.

A sincronização explícita melhora o pedido de persistência ao sistema de
arquivos; **não é prova de durabilidade física** contra controladoras com
cache volátil, dispositivos com `fsync` ineficaz, erros de hardware ou
falha elétrica. Renomeação de vários arquivos **não é transação atômica
entre arquivos**: uma queda pode ocorrer entre PGN e resumo; a recuperação
existente deve verificar coerência dos artefatos, sem dupla contagem.

**Gates de validação:** testes de injeção de falha verificam ordem
`fsync(file) → rename → fsync(directory)`, integridade do arquivo anterior
se o sync do temporário falhar, propagação de falha de sync após rename,
ausência de arquivos temporários órfãos e durabilidade do marcador de
parada. Testes automatizados não simulam os caches do SSD, SIGKILL
do Rodent nem falha elétrica real.

**Limites:** o comando de inspeção é um *snapshot* e não segura lock; executar
somente sem partida simultânea. A validação física Debian/Rodent, ausência
de processos UCI órfãos e cotas efetivas de CPU/RAM continuam pendentes
em #27 e #83 (`NOT_RUN`).
