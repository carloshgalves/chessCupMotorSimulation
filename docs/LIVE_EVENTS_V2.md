# Eventos de partida v2 — contrato e reconexão (#17)

O ChessCup já oferecia o endpoint SSE **v1** (`/api/events`), read-only,
com notificações de checkpoint. A v2 adiciona a capacidade de reentregar
**notificações observadas** após queda breve da conexão, ou pedir
ressincronização explícita se o cursor não pertence ao histórico disponível.
Não é um barramento autoritativo de lances, nem um histórico de jogadas
completo. O endpoint **`/api/state` permanece a fonte de verdade** de
PGN, FEN, relógios e resultado.

## API local

- `GET /api/events/v2` — SSE de Arena (sem query) e `watch`
  (opcional `?match=r01-m01&attempt=1`, com validação existente).
- `GET /api/events` — SSE v1 preservado sem alteração de contrato.
- `GET /api/state` — snapshot completo autoritativo, para reconexão,
  renderização e correção de lacunas.
- Nenhuma rota nova de escrita; `POST` continua recusado pelo monitor.

A v2 envia eventos `state` com `id: <epoch>.<sequence>.<revision>`
e `data` JSON contendo `schema=chesssim-game-events-v2`,
`event_id`, `epoch`, `sequence`, `revision` (SHA-256 do snapshot
estável), `changes` (categorias `snapshot`, `session`, `move`,
`clock`, `terminal`, `state`), `resync_required`,
`replay_scope=OBSERVED_CHECKPOINTS_ONLY` e metadados da seleção atual.

O evento **não** envia todos os lances, nem controla a execução: o cliente
recebe a notificação e faz GET `/api/state`. `server_time` volátil
não altera a revisão. Hashes de posição não são sequência monotônica;
a v2 acrescenta um `sequence` ordenado por instância do servidor.

## Reconexão

1. Na conexão inicial, sem `Last-Event-ID`, a v2 responde com o estado
   corrente e `resync_required=true` / `changes=[snapshot]`.
2. Caso a conexão reconecte com `Last-Event-ID` ainda no buffer da mesma
   instância e da **mesma seleção**, a v2 reenvia as notificações observadas
   depois do cursor em ordem de sequência. Se não houver mudanças, o servidor
   aguarda e envia comentários keepalive, sem duplicar eventos.
3. Cursor desconhecido, muito antigo, de outra sessão ou de servidor
   reiniciado resulta em um único snapshot com `resync_required=true`.
   **Nunca** supõe que eventos não observados foram recuperados.
4. O cliente ignora sequências atrasadas da mesma época e fecha a conexão
   anterior ao trocar a seleção. O polling HTTP permanece fallback.

O journal vive somente **na memória**, separado por seleção, com limite de
**16 seleções e 32 observações por seleção**. A sequência é global ao servidor,
não necessariamente contígua dentro de uma seleção. A reinicialização da
Arena/monitor cria uma nova época. Uma transição que ocorreu inteiramente
entre duas leituras nunca pode ser reconstruída por este journal e deve ser
corrigida pelo GET ao estado atual.

## Segurança e isolamento

- Bind estrito em loopback, validação de Host, Origin e Sec-Fetch-Site,
  sem CORS cross-origin; os limites existentes de quatro clientes SSE
  concorrentes e streams curtos com reconexão permanecem.
- Cursors são meros identificadores **não confiáveis**, nunca paths,
  tokens de autenticação ou índices de arquivos.
- Nenhuma escrita em PGNs, clocks, Elo, motor, arquivos de torneio ou
  partições de estilo. Não há retenção em disco, coleta de telemetria,
  downloads ou conexão com serviço remoto.
- `EventSource` usa as políticas do navegador, e os assets continuam
  vinculados à origem local. Falha do SSE não desliga o motor nem bloqueia
  `/api/state`.

## Gates

A CI comprova contratos de replay, concorrência, quota, sessão, reinício,
expiração de cursor, frames SSE e compatibilidade v1. A homologação física
em Chrome/Debian com Rodent IV, browser suspend/resume, reconexão após
interrupção de rede e 100/125/150/200% de zoom pertence à #83.
**CI verde não equivale a homologação manual.**
