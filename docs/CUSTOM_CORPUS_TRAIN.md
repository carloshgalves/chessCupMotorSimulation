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

## Limitações relevantes

O cache PGN original **continua contendo todos os jogos**, inclusive
os reservados: não existe isolamento criptográfico, controle de
acesso multiusuário ou `sealed holdout`. Posições de xadrez
similares/transposições podem aparecer em partidas distintas
entre partições, pois esta política separa **jogos/datas**, não
impõe embargo de posições compartilhadas. A restrição de uso
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
