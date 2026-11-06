# Catálogo local de partidas da aba História (#50)

O catálogo é uma projeção **somente leitura**. Não baixa partidas, não executa
motores e não escreve arquivos. A aplicação continua funcional quando não
existem partidas: retorna uma lista vazia. O limite da API é **cinco jogos por
jogador**, com dados de origem e hashes verificáveis.

## Endpoints

| Rota GET | Resultado |
| --- | --- |
| /api/history/players/{slug}/games | schema chesssim-history-games-v1, contagem, limite e até cinco cartões |
| /api/history/games/{game_id}/replay | schema chesssim-history-replay-v1, cartão e posições FEN/SAN calculadas de PGN válido |

As respostas efetivas usam as versões de contrato
`chesssim-history-games-v1` e `chesssim-history-replay-v1`.
Os IDs são opacos: o cliente não envia caminhos de arquivos e não escolhe PGN
arbitrariamente. O serviço atende apenas em loopback e verifica o cabeçalho
Host, como as demais rotas da Arena. A rota de replay não converte erros de
integridade em posições aparentemente válidas.

Os cartões informam `origin=historical|simulation`, brancas/pretas,
evento, data informada no PGN, status `COMPLETED|INCOMPLETE`,
resultado apenas quando concluído, fonte, e SHA-256 dos bytes originais do PGN.
Uma partida incompleta pode ser reproduzida até o último lance conhecido, mas
não pontua como vitória/derrota/empate final.

## Histórico curado pelo usuário

Para registrar fontes históricas autorizadas, o operador coloca os arquivos
**somente no diretório local da Arena** `<workspace>/history-games/`:

- `manifest.json` (máximo de 256 KiB);
- `games/h-<identificador>.pgn` (um jogo legal por arquivo, UTF-8, máximo 2 MiB).

Exemplo de manifesto de formato, **sem jogo histórico real embutido**:

```json
{
  "schema": "chesssim-curated-games-v1",
  "games": [
    {
      "game_id": "h-arquivo-exemplo-001",
      "white_slug": "fischer",
      "black_slug": "tal",
      "pgn": "games/h-arquivo-exemplo-001.pgn",
      "sha256": "<SHA-256 real do arquivo PGN, 64 dígitos hexadecimais>",
      "source_url": "https://example.org/registro",
      "source_label": "Identificador do acervo autorizado",
      "priority": 10
    }
  ]
}
```

Os campos de exemplo devem ser substituídos por fontes verificadas; a URL
de exemplo não é uma referência histórica. O hash pode ser calculado com
`sha256sum games/h-arquivo-exemplo-001.pgn`. Nomes nas tags
White/Black do PGN precisam coincidir exatamente com os nomes canônicos no
catálogo de 64 participantes, e a sequência de lances deve ser legal.

Nenhum PGN histórico ou corpus privado é distribuído pelo wheel. O manifesto
é opcional, fica fora do repositório e deve respeitar as licenças das fontes.
O módulo não constitui autorização para republicar conteúdo de terceiros.

## Fallback de confrontos simulados

Caso haja menos de cinco jogos históricos, o serviço consulta partidas salvas
em sessões locais válidas da Arena, sob
`session-*/duels/<jogador-a>-vs-<jogador-b>/<tentativa>/summary.json`.
Somente jogos referenciados pelo resumo e com PGN correspondente são
considerados. Jogos `FAILED` e `INTERRUPTED` não entram como
partidas válidas. Um jogo `INCOMPLETE` é identificado e não recebe
resultado final. O catálogo dá prioridade aos históricos curados (ordenados
por prioridade explícita e ID), depois aos simulados (sessões mais recentes
primeiro). A deduplicação por hash e assinatura semântica (jogadores, evento, data,
rodada, posição inicial e lances) impede preencher os cinco cartões com
cópias da mesma partida mesmo se o PGN tiver espaços diferentes.

O diretório de sessões fica sob controle do operador. Arquivos corrompidos,
symlinks, caminhos divergentes, hashes incompatíveis, jogadores inconsistentes,
encoding e PGNs ilegais e JSON com chaves duplicadas ou números não finitos são recusados; o endpoint devolve erro genérico em
vez de fornecer replay inseguro. Para reduzir uso de recursos, há limites de
volume para arquivos, manifestos e quantidade de sessões pesquisadas.

## Diagnóstico e execução

Com a Arena ativa (exemplo de porta 8766), abrir no navegador:

```text
http://127.0.0.1:8766/api/history/players/fischer/games
```

O campo `count` pode ser zero sem ser erro. O resultado vazio foi homologado no Chrome/Debian após o merge da frente de retratos; a disponibilidade de partidas depende dos arquivos locais do operador. Se houver jogo,
copie o `game_id` para:

```text
http://127.0.0.1:8766/api/history/games/ID_AQUI/replay
```

A UI de seleção e tabuleiro interativo no perfil é escopo posterior da
issue #51; esta entrega fornece somente os contratos e as garantias de
integridade para essa interface. A integração usa as sessões da Arena,
sem interferir na execução dos motores.

## Limites explícitos

- Resultados de partidas históricas dependem dos metadados declarados pela
  fonte e da verificação da legalidade do PGN, não são certificados por FIDE.
- Data e evento são lidos das tags existentes e não completados por inferência.
- O protocolo não confirma autenticidade do estilo dos motores; isso exige
  avaliação independente.
- Duplicação semântica entre PGNs de conteúdo distinto exige futura
  normalização por partidas/fonte, além do hash literal.
