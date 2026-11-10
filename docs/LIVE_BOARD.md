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

## Chaveamento, classificação e histórico (#23)

No monitor de um torneio, a seção **Chaveamento da Copa** exibe todas as
rodadas já criadas, os dois participantes de cada confronto, pontuação
acumulada **dentro do confronto**, vencedor e estado de resolução. A
pontuação não é rating Elo nem classificação geral por força: o vencedor
avança à rodada seguinte, e um empate não resolvido permanece sinalizado.

- Clique em um confronto de qualquer rodada para selecionar os PGNs
  anteriores. A escolha é feita por identificador do manifesto (por
  exemplo, `r01-m01`), **sem aceitar caminho de arquivo fornecido pelo
  navegador**.
- Na caixa **Tentativa**, é possível escolher um par de jogos anterior,
  inclusive um interrompido (claramente rotulado como incompleto).
  As partidas não concluídas não viram resultados oficiais na tabela.
- Selecione **Jogo 1** ou **Jogo 2** e use a barra ou setas do replay
  para navegar pelos lances. Para voltar à transmissão atual, clique
  em **Voltar ao vivo**.
- O confronto e a tentativa selecionados ficam no fragmento da URL
  (exemplo: `#match=r01-m01&attempt=2`). É possível recarregar a página
  ou compartilhar essa referência **somente com outro navegador no mesmo
  computador**. Não são armazenadas cópias de resultados no navegador.
- Os dados são lidos do manifesto persistido e dos PGNs a cada atualização.
  Fechar/reabrir o navegador ou o monitor não descarta a tabela; o torneio
  continua independente da aba. Uma seleção para confronto ainda não
  iniciado exibe o confronto pendente, não inventa partidas.
- A seleção explícita não acompanha automaticamente outro confronto:
  o botão **Voltar ao vivo** retorna ao mais recente do torneio.

A API local somente leitura aceita `/api/state?match=r01-m01` e
`/api/state?match=r01-m01&attempt=2`. Confrontos inexistentes,
tentativas fora do histórico e parâmetros desconhecidos são recusados
com HTTP 400, sem revelar caminhos locais. Alterações de resultados
continuam sendo feitas exclusivamente pelo orquestrador, nunca por
essa interface.

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

## Adivinhar o próximo lance — desafio do espectador (#94)

No monitor `watch` e no visualizador de Duelo da Arena (iframe `/viewer`), o botão **Ativar palpites** permite escolher origem e destino ou arrastar para prever um lance da posição exibida **ao vivo**. Os destaques **azuis** identificam o palpite, distintos do vermelho dos pré-moves do modo Jogar (#90) e das setas manuais (#91).

**Contrato de integridade:** a resposta `/api/state` inclui `uci` real por meio-lance (derivado do PGN validado) e apenas para a última posição ainda ativa a lista `legal_moves` produzida por `python-chess`. O observador só pode palpitar quando estiver no último lance conhecido de uma partida `live`, não durante reprodução pausada/histórica. O palpite não altera FEN, PGN, UCI, relógio, classificação, Elo ou arquivos de execução; não envia POST e não consulta uma engine separada. O lance seguinte do mesmo jogo é comparado por UCI exato; snapshots repetidos não pontuam em duplicidade. Há placar local por partida (acertos/tentativas) e mensagens de acerto/erro. Troca de partida, pausa ou navegação histórica descarta intenções pendentes. Sem login, persistência ou ranking multiusuário.

Uma promoção é escolhida explicitamente (D/T/B/C), sem inventar uma variante. Nenhuma previsão retroativa é pontuada. A apresentação segue como renderização local com realce de origem/destino para não falsificar a posição real antes do motor jogar.

**Referências de UX (consultadas 10/11/2026):** a antiga interação `Guess the Move` do Chess.com permitia mover uma peça durante a observação e comparar com o movimento efetivo: https://www.chess.com/forum/view/help-support/guess-the-move-feature. Moderadores relataram remoção do espectador Live tradicional (2024) e disponibilidade residual em Events (2026): https://www.chess.com/forum/view/site-feedback/what-happened-to-guessing-moves-while-spectating-games. Implementação independente; não copia código/assets externos.

**Gate:** homologação manual com PGN real em Chrome/Debian, zoom, toque/arraste, eventos de rede, latência e múltiplos jogos permanece `NOT_RUN` em #83; CI não substitui essa prova.
