# #63 — Conjunto de treino isolado de corpus vinculado (Slice 5)

Esta fase produz um **artefato experimental de treino, sem motores**, a
partir de um vínculo PGN local que já passou pelo fluxo de `pgn-cache
--apply` e `custom-corpus-link` da Slice 4. O objetivo é impedir que
dados reservados de validação/holdout sejam copiados **por este comando**
para o diretório de treino. Não é uma prova de segurança de arquivo
original, nem de autorização legal independente.

## Fluxo

```bash
chesssim custom-corpus-status \
  --library /dados/criador \
  --id user-0123456789abcdef0123456789abcdef

chesssim custom-corpus-train \
  --library /dados/criador \
  --id user-0123456789abcdef0123456789abcdef \
  --cache /dados/cache-pgn-autorizado \
  --expected-head SHA256_DO_VINCULO_ATIVO \
  --out /dados/novo-treino-exclusivo \
  --min-train 20 \
  --max-output-mb 32
```

A pasta `--out` **não pode existir**, estar dentro do cache ou da
biblioteca de identidades, ou passar por symlinks conhecidos. Seu pai
deve existir. Até 32 MiB (PGN de treino + manifesto) são permitidos
por esta interface. Nunca sobrescrevemos a saída, nem alteramos cache
ou recibo de vínculo; a identidade continua `fictional-player`.

O comando exige que o personagem esteja **ativo** e tenha evento
`LINK` atual, e compara `--expected-head` com o hash do último
recibo. Ele carrega **uma única vez** o manifesto e PGN de cache
aplicado, verifica hashes de jogos, legalidade, atestação de direitos,
uso permitido `derived_openings` e recomputa a seleção exata usada
pelo vínculo. Qualquer divergência entre o vínculo e o cache invalida
a saída. A verificação e publicação são feitas sob o lock cooperativo
da biblioteca para que uma CLI de `DETACH` não altere o head durante
o export. Esse lock **não impede alteração fora da aplicação**, e não
autentica a origem de licenças ou jogadores.

## Partições

- Somente o nome completo exato White/Black atestado para o vínculo
  seleciona partidas. Nome igual nas duas cores é ambíguo e excluído.
- Partidas duplicadas por posição inicial + sequência UCI já são
  deduplicadas pelo `pgn-cache`. Não é prova de que partidas com
  trajetórias diferentes sejam estatisticamente independentes.
- Datas `YYYY.MM.DD` completas e **não futuras** são obrigatórias
  para divisão; datas ausentes, incompletas, impossíveis ou futuras
  não entram nem em treino nem nas partições reservadas.
- Aplica a política já utilizada em `age_cohort`: alvos 60/20/20,
  distribuição cronológica, **sem separar partidas do mesmo dia**
  entre treino, validação e holdout. Dias com muitas partidas podem
  resultar em partições desequilibradas ou vazias.
- A saída contém somente `train.pgn` (jogos da parte treino) e
  `split.json`. Validação e holdout são registrados apenas com
  contagens, datas mínimas/máximas, distribuição por cor e um
  compromisso SHA-256 das trajetórias. Seus hashes individuais e
  lances **não** são exportados. O manifesto inclui hashes
  individuais apenas para a partição de treino.
- `TRAIN_ONLY_PREPARED_NOT_VALIDATED` significa apenas que há
  pelo menos `min_train` jogos de treino **e** partições de
  validação/holdout não vazias. Caso contrário,
  `INSUFFICIENT_PARTITION_EVIDENCE`. Nenhum desses estados
  concede instalação ou elegibilidade UCI.
- O hash do PGN de treino e seu tamanho são registrados no manifesto.
  Se uma gravação falhar após criar a pasta exclusiva, a rotina
  tenta limpá-la. Não se afirma resiliência comprovada a falha
  de energia ou corrupção de filesystem físico.

## Slice 6 — embargo posicional experimental (opt-in)

Para auditar **sobreposição de posições** entre as partições, acrescente
`--embargo-min-ply 2` (ou um limite escolhido entre 1 e 80). Sem essa
flag, a saída `chesssim-custom-train-only-split-v1` continua compatível e
`position_embargo.status=NOT_RUN`: **não pode ser descrita como auditada
por posições**. Com a flag, o manifesto passa a ter o novo schema
`chesssim-custom-train-only-split-v2`; um consumidor que só reconheça
v1 deverá rejeitar v2 em vez de presumir equivalência de critérios.

```bash
chesssim custom-corpus-train \
  --library /dados/criador \
  --id user-0123456789abcdef0123456789abcdef \
  --cache /dados/cache-pgn-autorizado \
  --expected-head SHA256_ATUAL_DO_VINCULO \
  --out /dados/treino-com-embargo-novo \
  --min-train 20 \
  --embargo-min-ply 2
```

A partir do meio-lance escolhido, o sistema examina todas as posições
**após cada lance, inclusive o último**, usando o estado FEN de quatro
campos: peças, lado a jogar, roque e en passant, excluindo relógios de
lances. **Ply 1** significa sempre o primeiro lance do PGN carregado,
inclusive com `SetUp/FEN` inicial; não é o número absoluto de lances
da partida no tabuleiro. O campo de en passant é canonizado segundo
a representação legal da biblioteca de xadrez. O histórico de repetição
e o contador de 50 lances não integram a comparação. A regra usa igualdade **exata de posição** — transposições de
lances que chegam ao mesmo estado FEN são detectadas. Não examina
posições anteriores ao limite escolhido; o corte deve ser interpretado
como uma decisão metodológica do operador, nunca como garantia de
ausência de vazamento fora desse intervalo.

A regra é conservadora e determinística:

- O **holdout** tem prioridade máxima e nunca é descartado nem
  exportado para viabilizar um status verde.
- Partidas de **validação** que compartilhem alguma posição auditada
  com holdout são colocadas em quarentena (não entram na partição
  de validação restante).
- Partidas de **treino** que compartilhem qualquer posição auditada
  com **toda a validação original ou holdout original** são descartadas
  do arquivo de treino, mesmo que o jogo conflitante de validação
  também tenha sido colocado em quarentena.
- Partidas de treino curtas demais para produzir posição no intervalo
  auditado também são descartadas. Partidas reservadas curtas demais
  impedem o status `AUDITED_DISJOINT` e geram
  `INCOMPLETE_RESERVED_POSITION_COVERAGE`. Se a cobertura reservada
  for incompleta, o status geral será
  `INSUFFICIENT_POSITION_EVIDENCE`, jamais `PREPARED`.

O `split.json` registra apenas **contagens** de conflitos anteriores,
jogos em quarentena, posições examinadas (quota de 200.000), limites
do corte e o estado final. `AUDITED_DISJOINT` atesta somente **que
os jogos retidos não compartilham estados FEN pós-lance no intervalo
auditado**. Se a quarentena esvaziar treino ou validação, o status
geral continuará `INSUFFICIENT_PARTITION_EVIDENCE`; não se
fabricam partidas para atingir o mínimo.

Nenhum movimento/posição individual das partições reservadas é
incluído na saída, e os hashes de jogos reservados permanecem
ocultos como na Slice 5. O hash do treino refere-se **apenas ao treino
remanescente**. Não se alteram o `pgn-cache`, as identidades e os
recibos de vínculo. O limite de posições produz **falha explícita**,
não análise truncada que aparente sucesso.

Mesmo `AUDITED_DISJOINT` **não equivale a holdout selado**, ausência
de posições comuns antes do corte, independência estatística de jogos,
verificação de licença/identidade ou validação da força e estilo UCI.
O cache original continua acessível e a avaliação física no Debian
permanece `NOT_RUN` na #83.

## Limitações relevantes

O cache PGN original **continua contendo todos os jogos**, inclusive
os reservados: não existe isolamento criptográfico, controle de
acesso multiusuário ou `sealed holdout`. Posições de xadrez
similares/transposições podem aparecer em partidas distintas
entre partições, pois esta política separa **jogos/datas**, não
impõe embargo de posições compartilhadas **sem a flag opt-in da
Slice 6**; mesmo com a flag, somente o intervalo pós-lance
selecionado é auditado. A restrição de uso
`derived_openings` é atestada pelo operador, não verificada
judicialmente; redistribuição e publicação não são autorizadas
automaticamente por este comando.

O resultado não é um modelo Rodent IV, não incorpora um repertório ao
motor, não mede Elo, força, estilo ou fidelidade histórica, não cria
rating e não altera os 64 jogadores canônicos. O corpus associado
pode estar indisponível ou revogado após a exportação; validamos
seu estado **no instante da leitura**, e reuso posterior exige nova
verificação. O registro de vínculo não é alterado.

**QA operacional #83: NOT_RUN** para execução física em Debian/
Chrome/Rodent, PGNs reais e comprovação independente de direitos.
A #63 permanece aberta para validação de corpus, auditoria de
posições/holdout e instalação UCI experimental.
