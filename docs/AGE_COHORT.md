# #65 — Corpus por idade exata (Slice 1)

Este incremento extrai **offline** um recorte de partidas em uma idade
civil completa, sem produzir personalidades jogáveis ou cientificamente
calibradas. O nome no PGN e a data de nascimento são declarações do operador,
não identidade verificada pelo programa.

## Entradas

Aceita somente uma pasta produzida por pgn-cache --apply, contendo
games.pgn e manifest.json, com direitos atestados pelo operador e todas
as fontes autorizando explicitamente o uso derived_openings. Licença
não é inferida nem verificada independentemente. Cada arquivo e identidade
de jogo são reconciliados com o manifesto, com tamanho máximo de
32 MiB para o PGN fonte na primeira fatia.

Exemplo de uso com data de nascimento a verificar pelo operador:

    chesssim age-cohort \
      --cache /dados/cache-licenciado \
      --pgn-name "Carlsen, Magnus" \
      --birth-date 1990-11-30 \
      --age 21 \
      --min-train 100 \
      --out /dados/coorte-carlsen-21-nova

O nome deve corresponder a um campo completo White/Black do PGN,
desconsiderando somente espaços repetidos e caixa das letras.
Correspondência vaga por sobrenome ou alias não é aceita; homônimos
exigem verificação externa. Jogos com nome idêntico nos dois lados
são excluídos.

## Idade e partição

- Data do evento exige dia, mês e ano válidos; excluir datas
  parciais/inexistentes, futuras, anteriores ao nascimento e de
  qualquer idade diferente da idade exata solicitada.
- Aniversário é o dia civil completo. Para nascimento em 29 de
  fevereiro, o aniversário em ano não bissexto é 1º de março.
- Deduplicação ocorre antes, no pgn-cache, por FEN inicial e
  sequência completa UCI. Contagens refletem o cache único;
  não comprovam cobertura histórica integral.
- Separação temporal por datas em metas aproximadas de 60% treino,
  20% validação e 20% holdout. Uma mesma data nunca atravessa
  a fronteira entre splits, mesmo se desequilibrar percentuais.
- Somente train.pgn exporta jogadas. cohort.json registra
  hashes das trajetórias reservadas, períodos, razões de exclusão,
  amostra, SHA-256 do treino e proveniência declaratória.
- Os jogos de validação/holdout **continuam no cache fonte**.
  Não copiá-los para o recorte limita exposições acidentais,
  mas não é lacre criptográfico nem prova de isolamento.
- Se treino tem menos que --min-train jogos distintos ou
  validação/holdout está vazio, status é
  INSUFFICIENT_EXACT_AGE_EVIDENCE. Caso contrário, é
  COHORT_PREPARED_NOT_VALIDATED.
- profile_available é sempre false: o programa não cria
  configuração de Rodent, não consulta motores, não ajusta
  parâmetros e não publica selo de fidelidade histórica.
- A saída exige pasta nova exclusiva, com escrita atômica e
  sem symlink conhecido. Nenhuma fonte ou arquivo anterior
  é sobrescrito.

## Limites e gates

A identidade declarada e a licença devem ser auditadas humanamente.
Faltam corpus real autorizado, número suficiente de jogos por idade
e fase, separação de estilo e força, comparação cega com auge e
baseline e teste de drift temporal. A #65 permanece aberta.
O ensaio físico com Rodent/Stockfish/Debian continua NOT_RUN na #83.
