# Preflight de índice histórico e compromissos de partições — issue #10

Este procedimento é **somente offline e index-only**. Ele não abre PGNs,
não executa UCI, não acessa os conjuntos finais lacrados nem valida
juridicamente as fontes. Produz um registro **reproduzível de partição
cronológica por ano**, baseado em hashes de partidas e metadados
**declarados pelo operador**.

## Entrada

Crie um JSON local no esquema `chesssim-historical-game-index-v1`: 

```json
{
  "schema": "chesssim-historical-game-index-v1",
  "target_slug": "jogador-exemplo",
  "sources": [
    {
      "id": "arquivo_01",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "license_id": "OPERADOR_DECLAROU_LICENCA",
      "license_evidence_uri": "https://example.org/evidencia"
    }
  ],
  "games": [
    {
      "mainline_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "date": "1980-01-10",
      "source_ids": ["arquivo_01"],
      "metadata": {
        "white_known": true,
        "black_known": true,
        "event_known": true,
        "time_control_known": false
      }
    }
  ]
}
```

Os hashes e links desse exemplo são **fictícios** e não provam fonte,
licença ou identidade. `mainline_sha256` deve ser calculado de forma
canônica sobre a posição inicial e os lances completos (consulte
`game_diversity.mainline_identity`); o preflight **não verifica**
se o hash declarado corresponde ao PGN real.

Regras de entrada:

- Até 32 fontes e 10.000 partidas indexadas; JSON até 4 MiB.
- Cada `mainline_sha256` deve ser um SHA-256 hexadecimal minúsculo e único.
- `source_ids` referencia fontes existentes, sem duplicatas e em ordem alfabética.
- Data completa `YYYY-MM-DD` válida ou `null`; datas desconhecidas
  são contabilizadas como quarentena, não atribuídas a partições.
- Os quatro campos de qualidade de metadados são booleanos explícitos.
- Campos extras, datas inválidas, identidades repetidas, arquivos
  simbólicos, não regulares ou JSON com chaves duplicadas são rejeitados.

## Execução

```bash
chesssim historical-split-preflight \
  --input indice-nao-lacrado.json \
  --out manifesto-novo.json \
  --ack-unsealed
```

O arquivo de saída **não pode existir anteriormente**. A confirmação
`--ack-unsealed` é uma declaração humana e não autentica a origem do índice.

## Partição por período

O sistema agrupa partidas válidas por **ano civil inteiro**, em ordem
cronológica, e tenta aproximar o alvo de 60% treino, 20% validação
e 20% holdout. Nenhum ano é dividido. Quando existem poucos anos,
um conjunto pode ficar vazio: o status será
`INSUFFICIENT_PERIOD_COVERAGE` e jamais será apresentado como pronto.
Com três partições não vazias, o status é
`INDEXED_NOT_SOURCE_VERIFIED`, não uma certificação científica.

O manifesto publica para cada partição:

- quantidade de jogos, anos representados e primeira/última data;
- SHA-256 canônico de sua lista ordenada de pares (data, hash da partida);
- **nenhum hash individual de partida reservada** e nenhum PGN.

`input_file_sha256` identifica os bytes do JSON de entrada. Já
`input_evidence_sha256` é o hash canônico dos dados declarados,
independente da ordem dos jogos. Mudanças nos hashes/datas das partidas
alteram os compromissos das partições afetadas. Mudanças nas
declarações de licença alteram o compromisso do índice, não as partições.

## Limites de segurança e epistemologia

Este módulo não fornece cifragem, assinatura digital, isolamento de
filesystem nem garantia contra reconstrução por alguém que já possua
o índice original. A fonte original, sua licença, a identidade do
jogador, as datas e as partições são **declaradas**, não verificadas
contra PGN real. O relatório marca explicitamente
`source_provenance = OPERATOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED` e
`embargo_status = NOT_VERIFIED_BY_THIS_INDEX`.

Não use hashes de validação/holdout lacrados nesta ferramenta sem
um protocolo de autorização e embargo já aprovado. O preflight
não deve ser usado para seleção de modelos, calibragem ou ajuste de
repertórios. A etapa seguinte da #10 deve amarrar esses compromissos
a uma fonte efetivamente validada e comprovar que nenhum jogo reservado
foi acessado para ajuste. A #12 seguirá com identificação cega.
