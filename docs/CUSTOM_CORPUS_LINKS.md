# #63 — Vinculação de corpus PGN a personagens fictícios (Slice 4)

Esta fase conecta **metadados de partidas locais** a uma identidade
fictícia da biblioteca da #117, sem copiar lances, instalar Rodent IV,
criar personalidade UCI ou declarar força/estilo histórico. O objetivo
é tornar a **cobertura de evidência** verificável e distinta da identidade
do personagem.

## Pré-requisito e direitos

Usar exclusivamente uma pasta `pgn-cache --apply` já criada conforme
`docs/PGN_RIGHTS.md`, com `manifest.json` e `games.pgn`. O algoritmo
revalida integralmente o hash do arquivo, cada linha legal de PGN,
a integridade das trajetórias, a contagem por registro e a autorização
de uso `derived_openings` em **todas as fontes**.

**O operador atesta e revisa direitos, nomes e fontes**; o comando
não comprova legalidade ou identidade do indivíduo indicado no PGN,
não baixa conteúdo, não usa rede e não se comunica com motores.

O nome `--pgn-name` representa exatamente o participante White ou
Black no **PGN de origem** (ignoradas apenas diferenças de maiúsculas
e espaços repetidos). Ele **não é o nome do personagem** e não prova
que as partidas foram dele. Nome igual em ambos os lados é ambíguo e
não entra no recorte. Sem correspondências exatas, a cobertura é zero
e isso é comunicado como `NO_MATCHING_GAMES`, não fabricado como
corpus suficiente.

## Fluxo local

1. Criar personagem via `custom-player-create` ou usar UUID existente.
2. Criar/validar o cache por `pgn-cache --apply`.
3. Ler o estado e copiar o `head_sha256`.
4. Vincular o cache com `--expected-head`. Esse parâmetro compara a
   última revisão do **vínculo**, não da renomeação.
5. Para desfazer a associação sem apagar o corpus, acrescentar evento
   `DETACH`. Um novo vínculo exige desvinculação prévia explícita.

```bash
chesssim custom-corpus-status \
  --library /dados/criador \
  --id user-0123456789abcdef0123456789abcdef

chesssim custom-corpus-link \
  --library /dados/criador \
  --id user-0123456789abcdef0123456789abcdef \
  --cache /dados/cache-pgn-autorizado \
  --pgn-name "Nome Completo no PGN" \
  --expected-head SHA256_ATUAL_DO_VINCULO

chesssim custom-corpus-detach \
  --library /dados/criador \
  --id user-0123456789abcdef0123456789abcdef \
  --expected-head SHA256_ATUAL_DO_VINCULO
```

O recibo é criado em
`user-<32hex>.corpus.000001.json`, e novos eventos incrementam a
sequência, até 32. Todos apontam para o SHA-256 do registro original,
SHA do evento anterior, revisão do nome no ato da vinculação e horário
UTC. O lock cooperativo da biblioteca impede publicações concorrentes
pela aplicação; a escrita usa o mecanismo exclusivo e durável já
existente. Atualizações obsoletas são recusadas. Repetir associação
idêntica com o head correto é idempotente; tentativa de trocar a fonte
sem desvincular antes é recusada.

## O que o recibo realmente mede

Para o nome exato selecionado, o relatório inclui:

- número de trajetórias distintas presentes no cache;
- quantas partidas têm o nome nas brancas ou pretas;
- quantas têm data completa válida ou data desconhecida/inválida;
- quantas têm um resultado **declarado** (não necessariamente comprovado
  por posição terminal);
- SHA do arquivo de cache, SHA do catálogo de fontes autorizadas,
  IDs das fontes e SHA determinístico da seleção de trajetórias;
- faixa **descritiva** `ZERO_GAMES`, `LOW_1_TO_19`,
  `EXPLORATORY_20_TO_199` ou `EXPLORATORY_200_PLUS`.

O cache de entrada contém partidas únicas por FEN inicial + lances
UCI conforme o `pgn-cache`, mas isto não prova independência entre
partidas ou cobertura exaustiva da carreira. Controle de tempo **não
pode ser derivado com confiança** porque o cache conserva apenas
campos PGN selecionados; será uma dimensão pendente. Qualquer número,
mesmo 200, recebe `SNAPSHOT_NOT_VALIDATED`.

O recibo **não inclui PGNs ou lances**. Portanto não cria um
dataset treinável, não fecha um holdout, não verifica a fonte em tempo
real após o vínculo e não prova que o caminho original continuará
existindo. O cache externo permanece no lugar; mover, alterar ou
revogar direitos requer nova auditoria e um novo vínculo.

## Restrições e limites

- O alvo deve ser `fictional-player` local válido, com identidade
  imutável, e não pode estar desativado para criar novo vínculo.
  É permitido desvincular um personagem desativado.
- O algoritmo recusa manifestos/PGNs alterados, symlinks conhecidos,
  autorizações incompletas, linhas ilegais, IDs adulterados, saltos
  de sequência, checksums inconsistentes e eventos com datas futuras
  ou cronologia regressiva.
- Os novos recibos **não integram o bundle portátil v1/v2** de
  identidade/renomeação. Exportar o personagem não copia nem associa
  direitos de PGN a outra biblioteca automaticamente. A portabilidade
  de vínculos precisará de contrato próprio e revalidação da fonte.
- A integridade SHA-256 **não é assinatura de autoria**. Pessoas com
  escrita direta na biblioteca podem adulterar eventos e recalcular
  hashes; o lock é cooperativo, não barreira de segurança.
- Esta fase **não instala, altera nem torna elegível personalidade
  Rodent IV**. `uci_eligible=false` e
  `historically_validated=false` são permanentes aqui.
  A Arena permanece apenas com visualização de personagens; não recebe
  nova rota de launch.
- Tests usam PGNs sintéticos; corpus histórico real e ensaio físico
  Debian/Rodent continuam `NOT_RUN` na #83. A #63 segue aberta para
  isolamento de dataset, auditoria de identidade, calibração de força,
  construção de repertório e integração UCI.
