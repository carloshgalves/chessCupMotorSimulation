# Arena local de confrontos — issue #22

A Arena é um serviço **local, opt-in** para configurar duelos Rodent IV no
navegador. É separado de `chesssim watch`, que **permanece somente leitura**.
O motor externo não é incluído no projeto nem distribuído pelo instalador.

## Pré-requisitos

- Python/ChessSim instalados e Rodent IV funcional no computador.
- Duas personalidades **registradas e instaladas** no `basic.ini`, com
  arquivos existentes no diretório de perfis.
- O browser no mesmo computador que executa o servidor. Não há rede pública.
- A pasta de saída é escolhida **na CLI**, nunca enviada pelo navegador.

## Iniciar uma arena

Em uma nova aba do terminal:

~~~bash
cd ~/Projetos/GitHub/chessCupMotorSimulation
source .venv/bin/activate
export RODENT_IV_HOME="$HOME/Projetos/Engines/rodent-iv"
python -m chesssim arena --out runs/arena-local --port 8766
~~~

O processo imprime uma URL com um token aleatório após `#token=...`.
**Copie essa URL inteira do terminal para o navegador local**. O token não
é enviado pela rede na URL HTTP; é usado apenas nos cabeçalhos das ações
de início/parada. Não publique nem compartilhe esse endereço completo.

A página mostra os 64 nomes do elenco; apenas perfis efetivamente
registrados/instalados podem ser selecionados para executar duelos.
A presença do alias Rodent IV **não certifica autenticidade estilística**.

## Configuração

- **Brancas e pretas** são selecionadas individualmente. É possível
  inverter o emparelhamento antes de iniciar.
- Escolha **exatamente um** modo de execução: profundidade (1–16),
  tempo por lance (0,05–10 s), ou preset de relógio (`official`, `classical`,
  `rapid`, `blitz`, `bullet`). O modo oficial exige duas partidas com
  cores invertidas, livro interno habilitado e limite de segurança 600.
- Em um duelo exploratório, escolha entre **sem livro**, **livro interno
  UCI**, ou **repertórios externos condicionados**.
- A semente é aceita **somente para repertórios externos**, onde afeta
  escolhas da política de abertura, não a pesquisa interna do motor.
  Para esses repertórios, configure na CLI uma pasta de arquivos
  `fischer.json`, `tal.json` etc., gerados previamente pela pipeline do
  projeto:

~~~bash
python -m chesssim arena --out runs/arena-local --port 8766 \
  --opening-books ~/Projetos/RepertoriosChessSim
~~~

O serviço não recebe caminhos de livros via API: os arquivos são
selecionados pelo slug validado do jogador e conferidos pelo esquema
`chesssim-opening-v9`. Não há download, treinamento ou calibração automática.

A ficha de cada jogador diferencia **referência histórica (quando houver)**,
**hipótese de período** e **estado efetivo de disponibilidade do perfil**.
Nenhum jogador é rotulado como historicamente validado sem os experimentos
e as provas documentais correspondentes. Não há cálculo de Elo na arena.

## Jogar e assistir

1. Escolha os participantes, o modo de busca e a política de abertura.
2. Clique em **Iniciar confronto**. Somente uma execução pode estar ativa
   no serviço local por vez; outras tentativas recebem HTTP 409.
3. A arena grava metadados em `runs/arena-local/session-*/session.json`.
   Os PGNs e resumos são armazenados sob `duels/`, sem apagar partidas
   existentes. A interface acompanha automaticamente o tabuleiro no quadro
   à direita; é possível abrir o visor em outra aba.
4. Ao concluir, o resultado é lido do `summary.json` gerado pelo motor.
   Fechar ou recarregar a página não perde os artefatos. Reiniciar a arena
   volta a exibir a última sessão armazenada, sem reexecutá-la.
5. **Solicitar parada** sinaliza ao motor para encerrar após a busca em
   andamento. O arquivo/resultado inconclusivo não é tratado como vitória
   ou empate. A parada não interrompe preemptivamente a busca UCI.

Uma sessão marcada `RUNNING` no disco após falha ou desligamento é
apresentada como **INTERRUPTED** quando a arena reinicia; nunca é retomada
automaticamente. O motor antigo deve ter sido encerrado antes de iniciar
nova execução. O limite de parede por partida pode ser controlado:

~~~bash
python -m chesssim arena --out runs/arena-local \
  --max-game-wall-seconds 3600
~~~

O tempo de parede é verificado **entre lances**, não encerra imediatamente
uma chamada UCI bloqueada e não corresponde à derrota por tempo do xadrez.
Ao encerrar a arena por Ctrl+C, ela solicita parada cooperativa e aguarda a
limpeza dos subprocessos executados por essa instância.

## Modelo de segurança

- O serviço escuta **somente `127.0.0.1`**. Não aceita conexões externas.
- O navegador não pode fornecer comandos shell, executáveis, paths de
  saída, nomes de arquivos arbitrários ou parâmetros UCI desconhecidos.
- Iniciar/parar exige token de sessão de alta entropia em um cabeçalho
  `X-Chesssim-Token`, além de `Origin` e `Host` locais esperados.
- Requisições POST têm corpo JSON limitado a 4096 bytes, rejeitam campos
  duplicados, tipos incorretos e configurações incompatíveis.
- O visor `/viewer` reutiliza a interface somente leitura; o CSS/JS são
  empacotados localmente, sem dependências remotas. O iframe é liberado
  exclusivamente na mesma origem da arena.
- Só uma partida por vez pode consumir os recursos do motor. Para reduzir
  risco de sobrecarga, os limites do navegador são mais restritos que a CLI
  de pesquisa e o processo verifica tempo de parede.
- **Limitação:** um processo malicioso já executando na mesma conta local
  pode acessar o ambiente e ver o terminal/token. Esse mecanismo não é
  isolamento de sistema operacional nem autenticação multiusuário. Não
  exponha a porta por reverse proxy ou encaminhamento de rede.

## Evidências de teste

~~~bash
python -m pytest -q tests/test_arena.py tests/test_live_board.py
python -m chesssim arena --help
~~~

O CI testa servidor HTTP real em loopback, fluxo de duelos com runner
controlado, controle de acesso, bloqueio de corrida, sessões persistentes,
fichas de proveniência e leitura dos PGNs. **Não substitui** o aceite com
Rodent IV real e inspeção visual no Debian do operador.
