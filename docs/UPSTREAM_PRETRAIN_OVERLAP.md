# Pré-treino externo: auditoria de sobreposição para quatro históricos (#159)

**Estado: UNVERIFIABLE_PRETRAIN_OVERLAP.** O ChessCup não dispõe de inventário completo, autenticado e vinculado aos checkpoints dos jogos efetivamente usados no pré-treino ou fine-tuning de Maia3-79M ou DSC-150M. Não inferir disjunção externa da data da partida nem de listas parciais.

## Fontes primárias fixadas

- [DSC-150M — model card em commit pinado](https://github.com/ChessDS/chessds-dsc/blob/229ef4291c38c088a9fd3a410e4c0e926a20cea1/model_cards/dsc-150m.md): descreve pré-treino Lichess 2200+ (out/2024–dez/2025) e TWIC; fine-tuning OTB clássico em 437.051 jogos 2400+ (71.491.584 posições em duas passagens). Exclusão dos próprios jogos de avaliação do DSC NÃO prova exclusão dos PGNs históricos ChessCup.
- [Maia-3 — inferência em commit pinado](https://github.com/CSSLab/maia3/tree/1e13597c42d4858b7cfd7cfdae01e297263364b2) e [artigo Chessformer](https://openreview.net/forum?id=2ltBRzEHyd): código/weights publicados não equivalem a manifesto exaustivo dos jogos treinados. Não existe cutoff temporal suficientemente comprovado nesta auditoria.
- ChessCup: snapshot dos PGNs históricos no mirror rozim/ChessData, commit aaadf90e11862bc6f207fed407a70e7017df26bd. A identidade de partida segue game_diversity.mainline_identity: SHA256 de JSON canônico com FEN inicial + UCI de todos os lances da partida.

## Ferramenta offline e limites

Executar:

    python -m chesssim.upstream_pretrain_overlap --cohorts /caminho/coortes --out /caminho/overlap.json

A ferramenta lê exclusivamente o manifesto e os arquivos TRAIN do ChessCup. Reporta decisões, partidas globalmente distintas, e denominadores por modelo, jogador e ano. Não abre VALIDATION/HOLDOUT, não faz downloads, não lê pesos de modelos e não publica PGNs.

Aceita opcionalmente um JSON externo de fingerprints candidatos por modelo, via --dsc-index ou --maia-index. Contrato estrito: schema chesssim-upstream-fingerprint-subset-v1; model dsc150 ou maia79; coverage CANDIDATE_SUBSET_NOT_TRAINING_ATTESTED; fingerprint_contract chesssim-mainline-v1+fen4-sha256-v1; source_url HTTPS; source_artifact_sha256 hexadecimal de 64 caracteres; listas game_sha256 e position_sha256 de 64 caracteres hexadecimais minúsculos, sem duplicatas.

Os digests de posição usam SHA256 da concatenação de um prefixo versionado (chesssim-fen4-v1 seguido de LF) com os quatro primeiros campos da FEN. O campo de jogo deve seguir a identidade integral UCI do ChessCup, incluindo a FEN inicial. A comparação de posição não prova identidade da partida ou memorização. Um match em corpus candidato tampouco comprova que o registro entrou efetivamente no treinamento do checkpoint.

A rotina recusa índices alegadamente COMPLETE, chaves JSON duplicadas, SHA inválido, source URL sem HTTPS, excesso de fingerprints, arquivos simbólicos e linhas estranhas ao escopo TRAIN. A própria fonte externa e o digest de seu artefato **devem ser autenticados separadamente**: metadados declarados por operador são apenas declarações.

Quando não há índice externo, ou quando um subconjunto candidato não apresenta coincidência, o estado permanece **UNVERIFIABLE_PRETRAIN_OVERLAP**, jamais DISJOINT. Na saída, unknown_training_provenance_games abrange todos os jogos; matches candidatos são relatados separadamente.

## Portas de pesquisa ainda abertas

1. Separação de amostras dentro do ChessCup não demonstra exclusão do pré-treino externo.
2. A auditoria #159 só poderá confirmar ou refutar exposição externa mediante corpora completos e vinculados aos checkpoints, ou deve formalizar a impossibilidade de verificá-los.
3. Uma hipótese de generalização independente exige desenho prospectivo, controle por década, abertura, cor e adversário. F0b reportado pelo autor DSC não é replicação independente ChessCup nos quatro históricos.
4. O HOLDOUT final #154/#147 permanece selado; não alterar pesos, perfis Rodent IV nem critérios após observar o AUDIT da PR #162.

**Escopo desta fatia:** ferramenta, controles negativos, proveniência preliminar e relatório fail-closed. A issue #159 permanece aberta; sem inventário externo completo, nenhuma conclusão de decontaminação é possível.
