# Tabuleiro ao vivo — visualizador local (#21)

O ChessCup Motor Simulation oferece um **visualizador web local e somente leitura** para
acompanhar partidas em execução. Não requer internet, serviços externos, frontend
framework nem compilação de executáveis. O navegador lê snapshots gerados pelo
`python-chess` a partir de PGNs parciais persistidos pelo motor.

## Iniciar para uma Copa

Em um terminal, execute o torneio normalmente:

~~~bash
export RODENT_IV_HOME="$HOME/Projetos/Engines/rodent-iv"
python -m chesssim tournament-run --run runs/copa-piloto-4 --limit-pairs 1
~~~

Em **outra aba** do terminal, sem interromper a execução:

~~~bash
python -m chesssim watch --run runs/copa-piloto-4 --port 8765
~~~

Abra o endereço informado no terminal, por padrão
http://127.0.0.1:8765/. A tela mostra o último confronto detectado,
relógios e últimos lances. No início do torneio pode aparecer **Aguardando
partida** até o primeiro PGN parcial ser salvo.

Para um duelo independente, utilize o diretório da tentativa criado pelo
comando `play` (a pasta que contém `summary.json` e `duelo.pgn`):

~~~bash
python -m chesssim watch --run runs/fischer-tal/fischer-vs-tal/TENTATIVA
~~~

O comando `watch` não inicia partidas nem acessa o motor Rodent IV. O processo
web é encerrado com Ctrl+C; o processo do torneio permanece independente.

## Controles

- **Pausar visualização:** congela o tabuleiro exibido para análise; o motor
  **não para** e as partidas seguem sendo atualizadas.
- **Ao vivo:** volta ao último lance do jogo mais recente.
- **Anterior / Próximo / Início / Fim e barra de lances:** percorrem as
  posições já calculadas, inclusive em uma partida ainda em andamento.
- **Selecionar partida:** permite rever o primeiro jogo enquanto o segundo é
  jogado; ao voltar ao vivo, acompanha a partida mais recente.
- Os relógios são registrados em anotações PGN padrão `[%clk ...]` após cada
  lance quando o motor usa um preset com relógio. A página estima o tempo
  em contagem regressiva entre lances **sem atribuir derrota por tempo**.
  O relógio oficial registrado no JSON do duelo continua sendo a fonte
  autoritativa; o visualizador não arbitra.

Se quiser **parar o torneio**, use outra aba do terminal:

~~~bash
python -m chesssim tournament-stop --run runs/copa-piloto-4
~~~

Isso é distinto de pausar apenas a visualização. Depois de liberada a parada,
a retomada dos confrontos incompletos exige `--retry-incomplete`.

## Segurança e limitações

- O servidor escuta **somente `127.0.0.1`**, não abre a interface à rede,
  não possui endpoints de escrita e rejeita cabeçalhos Host inesperados.
- Assets HTML, JavaScript e CSS são locais e empacotados na distribuição Python.
  Não há CDNs, anúncios, scripts remotos ou necessidade de login externo.
- Os estados do tabuleiro são calculados de PGNs válidos com `python-chess`;
  o servidor não aceita lances enviados pelo navegador.
- Limita a leitura de cada PGN a 2 MiB e recusa links que escapem da pasta
  monitorada. Cabeçalhos da partida são exibidos como texto, sem HTML arbitrário.
- A visualização é atualizada por polling a cada ~1,2 segundo; ela não é uma
  transmissão contínua de vídeo nem a janela de execução do motor.
- Um PGN `jogo_N.partial.pgn` não comprova término nem resultado; o resultado
  oficial continua no `summary.json`/`jogo_N.pgn`.
- A precisão da contagem regressiva visual depende do intervalo entre a
  gravação do PGN e a leitura no navegador; nunca substitui o tempo real do
  motor. Jogos sem controle de tempo exibem relógios indisponíveis.
- Não há administração remota nem controles para iniciar/cancelar partidas a
  partir do navegador. Parada e retomada seguem na CLI.

## Checklist de validação

~~~bash
python -m pytest -q tests/test_live_board.py tests/test_controlled_play.py
python -m chesssim watch --help
~~~

A suíte de CI valida a API e os assets com um servidor HTTP local e verifica
atualização de PGN enquanto o confronto ainda está em andamento. O teste de
ponta a ponta visual com o Rodent IV do notebook é uma verificação operacional
posterior ao merge; não deve ser apresentado como realizado no GitHub Actions.
