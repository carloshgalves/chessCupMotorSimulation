# Estatísticas locais da aba História — contrato #52

A seção **No simulador** utiliza apenas resultados de execuções do
ChessSimulation no diretório local da Arena. **Não é a biografia real do
enxadrista; não indica rating, força do motor ou fidelidade estilística.**
Também não soma arquivos PGN históricos curados da aba de partidas
documentais, porque isso misturaria dados reais e sintéticos.

## Uso

Com a Arena ativa, acesse **História → enxadrista → Ver perfil histórico**.
A seção abre um endpoint GET próprio:

    GET /api/history/players/fischer/stats

Resposta versionada `chesssim-player-stats-v1`:
```json
{
  "schema": "chesssim-player-stats-v1",
  "slug": "fischer",
  "scope": {
    "directory": "arena-local",
    "sources": ["arena_local", "tournament_local"],
    "from_utc": null,
    "to_utc": null,
    "date_basis": "created_utc from persisted manifest, not game duration"
  },
  "totals": {
    "tournaments": 0, "duels": 0, "games": 0, "completed": 0,
    "wins": 0, "draws": 0, "losses": 0, "incomplete": 0
  },
  "opponents": [],
  "coverage": {
    "sessions_seen": 0, "tournaments_seen": 0,
    "candidate_games": 0, "verified_games": 0,
    "arena_verified_games": 0, "tournament_verified_games": 0,
    "rejected_games": 0,
    "rejected_records": 0, "excluded_attempts": 0, "failed_attempts": 0,
    "duplicate_records": 0, "partial": false
  }
}
```

O exemplo apresenta **sem dados**, e não uma taxa de vitórias hipotética.

## Fontes e abrangência

- **Arena:** sessões em `<arena --out>/session-*/session.json`,
  contendo um duelo persistido em
  `session-*/duels/<a>-vs-<b>/<tentativa>/summary.json`.
- **Torneios:** manifestos `tournament.json` na própria raiz da
  Arena, em um diretório filho (`<out>/<torneio>/`) ou em
  `<out>/tournaments/<torneio>/`. Torneios salvos fora dessa
  árvore **não são descobertos automaticamente**; não há varredura do
  sistema de arquivos ou leitura de caminhos fornecidos por HTTP.
- A cobertura informa separadamente jogos efetivamente verificados na Arena
  (`arena_verified_games`) e em torneios (`tournament_verified_games`);
  a soma coincide com `verified_games`. Os nomes em `scope.sources`
  indicam os sistemas de armazenamento consultados, não jogos existentes.
- O período apresentado é a menor e maior data **de criação do manifesto**
  registrada com fuso; valores são normalizados para UTC. **Não** representa
  o tempo de início/fim efetivo de cada partida, sua duração ou a data real
  dos acontecimentos históricos.
- Os contadores `sessions_seen` e `tournaments_seen`
  referem-se aos manifestos descobertos no diretório, inclusive outros
  participantes. O painel de totais é **do jogador consultado**.

## Denominadores e evidências

- `games` = `completed` + `incomplete`.
- `completed` = `wins` + `draws` +
  `losses`. Os resultados são computados pela perspectiva do
  jogador, independentemente da cor; empates recebem uma partida no
  denominador e uma unidade em `draws`.
- `duels` conta confrontos **com pelo menos um jogo válido**. O mesmo denominador aparece por adversário, enquanto `games` distingue os jogos individuais.
  Um par com brancas/pretas invertidas = **dois jogos, um confronto**.
  Nova sessão ou tentativa de desempate com arquivos distintos = novo
  confronto. PGN repetido em outra sessão **não** é automaticamente
  duplicata: são execuções diferentes. A mesma tentativa repetida no
  manifesto, por outro lado, é contabilizada uma única vez.
- `tournaments` conta torneios cujo manifesto válido lista o
  jogador como participante, mesmo sem partida concluída. Presença sem
  jogo (por exemplo, rodada ainda não iniciada) **não gera vitória, bye
  pontuado nem confronto fictício**. O formato atual de mata-mata trabalha
  com chaves de potência de dois e não contém bye competitivo explícito.
- Partidas de torneio são aceitas somente quando pertencem a uma
  tentativa registrada no ledger, cuja fonte e PGN de duas partidas
  correspondem aos hashes, engine/perfis, cores, resultados e trajetórias
  gravados. Tentativas excluídas da classificação **não pontuam**; seu
  número fica visível em `excluded_attempts`.
- Interrupções da Arena com PGN legal e status `INCOMPLETE`
  contam em `incomplete`, nunca em W/D/L. Uma tentativa `FAILED`
  sem resultado verificável aparece em cobertura, não como derrota.
- O arquivo de sessão e o PGN precisam concordar quanto a jogadores,
  cores, sequência de lances, resultado e hash da trajetória, quando
  disponível. PGNs históricos documentais não entram nos números.

## Integridade, segurança e cobertura

As leituras usam um helper de arquivos local limitado e descritores que
recusam symlinks nos componentes do caminho. Arquivos individuais e volume
de varredura possuem limites. O servidor continua em loopback, sem novo
POST e sem invocar Stockfish, Rodent IV ou alterar um manifesto.

Um registro corrompido/incompatível é **excluído**, e os campos
`rejected_games`, `rejected_records` e `partial`
evidenciam perda de cobertura. Registros inteiros não identificáveis também
podem gerar aviso parcial. Não se atribui uma vitória presumida por
comportamento ausente. Os diagnósticos não expõem caminhos completos,
PGNs privados nem erros de sistema ao navegador.

Para não interferir com as partidas em andamento, o endpoint opera em
thread separada do HTTP e não bloqueia, modifica ou sincroniza com a thread
de execução do motor. **Não é um serviço de métricas em tempo real**: os
dados são capturados a cada abertura do perfil. Para grandes temporadas,
futuramente será necessário armazenamento indexado e agregação incremental.

## Validação

- Usar um workspace vazio: todos os totais zero e nenhum resultado fictício.
- Adicionar sessão real com duas cores e comparar o JSON com os PGNs.
- Adicionar torneio local elegível, rematches, empates e interrupções;
  verificar o ledger e o painel, inclusive tentativas excluídas.
- Acessar a aba Arena e assistir a um duelo em paralelo; a consulta não
  pode alterar relógio, lance ou status de execução.
- Corrupção e symlink intencionais nos testes devem produzir aviso de
  cobertura ou resposta genérica de indisponibilidade, nunca leitura
  arbitrária de arquivos.

Gate visual e acessibilidade final: issue #53.
