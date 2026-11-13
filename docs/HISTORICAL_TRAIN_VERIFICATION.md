# Verificação de PGN somente de treino contra índice histórico — issue #10

Esta fatia verifica a integridade de um **arquivo PGN contendo exclusivamente
partidas de treino** por confronto com os hashes declarados no índice da
fatia anterior (`historical-split-preflight`). Validação/holdout não são
lidos, exportados, estimados ou usados para selecionar parâmetros.

## Entrada e execução

Requer os seguintes arquivos locais:

- `indice-nao-lacrado.json`: índice estrito com hashes canônicos de trajetórias,
  datas e metadados. As identidades de partidas reservadas nele presentes
  são apenas **metadados privados**, não os seus PGNs.
- `train-only.pgn`: exclusivamente as partidas aprovadas como treino,
  em UTF-8, até 8 MiB, fonte regular sem symlinks, diretórios ou FIFOs.

```bash
chesssim historical-train-verify \
  --index indice-nao-lacrado.json \
  --train-pgn train-only.pgn \
  --out relatorio-novo.json \
  --ack-train-only
```

**A opção `--ack-train-only` é uma declaração do operador**, não um
mecanismo de segurança independente. Nunca forneça o PGN do conjunto final
lacrado, nem o de validação para este comando.

## Verificações

1. O índice deve passar no preflight e possuir treino, validação e holdout
   não vazios, agrupados por ano civil inteiro.
2. A lista de partidas de treino é reconstruída **somente a partir dos
   hashes e datas do índice**; não são lidos lances/PGNs reservados.
3. Cada partida do PGN de treino é interpretada por `python-chess` com
   validação lexical de movetext, sintaxe e legalidade de lances. O arquivo
   deve conter apenas linhas principais, sem comentários, variantes
   secundárias, NAGs ou escape lines PGN — esses conteúdos não aparecem
   em `mainline_identity` e poderiam ocultar decisões reservadas.
   Cada partida deve terminar com seu próprio token explícito de resultado.
   Os cabeçalhos aceitos são Event, Site, Date, Round, White, Black,
   Result, TimeControl, Variant e, para posição inicial especial, SetUp/FEN.
   Cabeçalhos extras e valores excessivos são recusados: use um PGN de
   treino previamente sanitizado, não uma exportação bruta com anotações.
4. O hash de cada jogo cobre a **posição FEN inicial e a sequência inteira
   de lances UCI**, usando `game_diversity.mainline_identity`.
5. O número e a identidade das partidas devem corresponder exatamente
   à partição de treino. Jogos extras, ausentes, duplicados, alterações
   de lances ou datas são rejeitados antes de produzir relatório.
6. Quatro indicadores de qualidade do índice para White, Black, Event
   e TimeControl devem coincidir com os cabeçalhos observados.
7. O relatório é publicado de forma atômica, em novo arquivo exclusivo;
   falhas não deixam arquivo final parcial e não sobrescrevem relatórios.

## Interpretação do relatório

- `status=TRAIN_PARTITION_MATCHED_SOURCE_NOT_VERIFIED`: apenas a
  correspondência entre PGN de treino local e índices declarados foi atestada.
- `training_games_verified`: quantidade de jogos de treino conferidos.
- `train_partition_commitment_sha256`: mesmo compromisso da fatia #128.
- `train_pgn_sha256`: hash dos bytes efetivos do PGN de treino.
- `index_evidence_sha256` e `index_file_sha256`: compromissos de índice
  normalizado e arquivo do índice, respectivamente.
- `validated_partitions=["train"]`: **nenhuma partição reservada foi aberta**.
- `source_rights_status=OPERATOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED`.
- `embargo_status=RESERVED_GAMES_NOT_OPENED_EMBARGO_NOT_PROVED`.

**Importante:** o relatório não publica PGNs, FENs, lances, nomes de
jogadores ou hashes individuais de partidas, sequer de treino.

## Limitações e próxima fatia

Este verificador não autentica as fontes originais ou suas licenças.
Um operador que altere conjuntamente o PGN e o índice pode produzir
correspondência aparente. Também não comprova embargo de posições
transpostas ou isolamento de acesso ao holdout; esses gates exigem
outro protocolo controlado e aprovação humana.

O objetivo desta fatia não é produzir uma métrica de fidelidade histórica
nem avaliar força de jogo. Nenhuma promoção de perfil ou análise de
identidade ocorre neste fluxo.

## Gate opcional: compromissos do índice congelado antes da leitura do PGN

Para detectar alterações do índice entre a aprovação do preflight e a
verificação do PGN de treino, registre os dois compromissos **fora do
diretório de trabalho mutável** quando aprovar o manifesto:

- `input_evidence_sha256` do `historical-split-preflight`;
- `partitions.train.game_commitment_sha256` do mesmo manifesto.

Passe os dois valores originais (64 hexadecimais minúsculos) à CLI:

```bash
chesssim historical-train-verify \
  --index indice-nao-lacrado.json \
  --train-pgn train-only.pgn \
  --out relatorio-congelado-novo.json \
  --ack-train-only \
  --expect-index-evidence-sha256 "HASH_DO_INDICE_APROVADO" \
  --expect-train-partition-sha256 "HASH_DA_PARTICAO_DE_TREINO_APROVADA"
```

Os valores acima são placeholders, **não hashes válidos**. O comando
exige os dois argumentos ou nenhum; valores inválidos ou divergentes
causam falha **antes de abrir os bytes do PGN de treino**. Isso cobre
alterações posteriores nos metadados, na declaração das fontes ou na
partição de holdout mesmo que as partidas de treino permaneçam iguais.
Também impede que o verificador avance silenciosamente com um índice
alterado desde uma aprovação humana anterior.

Quando os dois compromissos correspondem, o relatório acrescenta
`pin_verification.status = MATCHED_OPERATOR_SUPPLIED_PINS_NOT_AUTHENTICATED`,
sem modificar os campos/relatórios anteriores na execução sem pins.

**Fronteira importante:** hashes guardados e fornecidos pelo mesmo
operador não comprovam independência nem imutabilidade criptográfica
contra um atacante capaz de mudar também os valores aprovados.
Arquive os hashes com controle de acesso, revisão e trilha de mudança
separados do checkout (por exemplo, registro administrativo aprovado).
Esta opção não verifica licença, direitos, selagem de dados ou acesso
externo a conjuntos reservados, e nunca deve ser apresentada como
autenticação científica do corpus.

Na API de memória, a conferência ocorre antes do **parse** dos bytes PGN já
fornecidos. No runner de arquivos, ela ocorre **antes de abrir o arquivo PGN**;
o teste de regressão comprova essa ordem, inclusive diante de índice alterado
ou cobertura cronológica insuficiente.
