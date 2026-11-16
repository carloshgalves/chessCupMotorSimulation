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


## Verificação independente de fontes públicas — 15/11/2026

Auditoria feita **sem solicitar informações aos desenvolvedores**. A autenticidade abaixo se refere à atribuição/publicação da fonte, **não** à prova de consumo de cada amostra durante o treinamento. Não abrir os braços reservados para tentar compensar ausência de proveniência externa.

| Evidência | Referência verificável | O que ela prova | O que NÃO prova |
| --- | --- | --- | --- |
| DSC-150M: modelo + cartão | [Cartão ChessDS do DSC-150M em commit `229ef429...`](https://github.com/ChessDS/chessds-dsc/blob/229ef4291c38c088a9fd3a410e4c0e926a20cea1/model_cards/dsc-150m.md), Git blob `5213c4257680a135dda8ec2965d047f14748339c`, pesos `66b21d7882bb3fb9737bc798e709671325838e5469ed6bfcf114b3abbcb48e24` | Fontes e parâmetros do treinamento declarados pelo editor do modelo: 1.620.000.768 posições de pré-treino Lichess/TWIC e 437.051 jogos clássicos OTB no fine-tuning (71.491.584 posições em duas passagens) | Identidade e hashes de todos os exemplos efetivamente vistos, versão integral dos dumps e prova de exclusão de Fischer/Tal/Karpov/Kasparov |
| Maia3-79M: cartão oficial | [Cartão UofTCSSLab/Maia3-79M na revisão `a107d6ceb7b298cb04ae1da4edffe2939858b894`](https://huggingface.co/UofTCSSLab/Maia3-79M/blob/a107d6ceb7b298cb04ae1da4edffe2939858b894/README.md); [inferência oficial CSSLab no commit pinado](https://github.com/CSSLab/maia3/tree/1e13597c42d4858b7cfd7cfdae01e297263364b2), Git blob README `c52b52b63516226886a00f0dbb431510b0340e94` | Declara que foram usados dumps mensais de jogos Lichess jan/2023–jul/2025 e que esse repositório público disponibiliza inferência | Quais partidas/posições entraram nos pesos do checkpoint de 79M; configuração de treinamento original mencionada como `ablate_size.sh` mas ausente na árvore do commit pinado do repositório público de inferência |
| Dataset candidato não oficial Maia3 | [mcmcmcmc/maia3-training-data](https://huggingface.co/datasets/mcmcmcmc/maia3-training-data) | Publicação de 31 parquets mensais 2023-01 a 2025-07, 334.438.119 linhas pré-processadas, com 7 posições de histórico; dataset publicado independentemente do checkpoint | Que esses bytes tenham sido realmente consumidos pelo treinamento oficial, deduplicação e pesos, ou inventário de partidas com trajetória integral; esquema exibido não expõe `game_id`, FEN/PGN ou sequência UCI completa por partida |

### Conclusão de autenticidade

**DSC-150M:** inventário das **fontes gerais declaradas**, sim; inventário exaustivo dos exemplos **realmente usados**, não. O cartão não fornece todos os hashes por partida ou posição nem os liga criptograficamente à execução do treinamento. TWIC/OTB são parte da seleção declarada; origem dos PGNs históricos do ChessCup e exclusão dos conjuntos de avaliação do DSC são assuntos distintos.

**Maia3-79M:** declaração do período Lichess, sim; inventário exato autenticado, não. O período de coleta dos jogos on-line ser posterior às partidas históricas não torna seus **lances e posições** independentes: reprodução ou análise de partidas históricas pode criar sobreposição de posições, e não há manifesto para conferir.

**Dataset candidato:** a existência pública do parquet e sua contagem de linhas não estabelecem cadeia de custódia até `Maia3-79M`. Mesmo um SHA256 do parquet serviria apenas à integridade do parquet, não à prova de exposição do checkpoint. O esquema são posições individuais pré-processadas, sem reconstrução confiável de trajetória UCI integral. Nenhum índice fabricado ou reconstruído daqui poderá ser rotulado `TRAINING_ATTESTED`.

**Censo local:** a CI real prévia [run 43784946492](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43784946492) comprovou 160.518 decisões TRAIN e 5.181 trajetórias globalmente distintas para os quatro históricos; isso caracteriza apenas o braço TRAIN ChessCup, **não** o conteúdo de pré-treino dos dois checkpoints.

### Critério científico de desbloqueio, sem contato

Se um artefato público futuro permitir certificar ausência de overlap, exigir conjuntamente: (i) identidade e hash do checkpoint; (ii) manifesto **exaustivo** de posições/jogos realmente processados, estratificado por etapa e filtros; (iii) versão imutável e hashes das fontes selecionadas; (iv) método reproduzível de fingerprint compatível com ChessCup; (v) vínculo auditável da execução específica aos exemplos, e prova de integridade/autenticidade de tal vínculo. A falta de qualquer elemento mantém `UNVERIFIABLE_PRETRAIN_OVERLAP`. Não basta um index de dados *candidatos*, código de inferência, cartão com volumes, ausência de correspondências em amostra parcial, nem score de benchmark.

**Decisão:** manter #159 aberta; PR #163 fornece auditor TRAIN-only; #154/#147 continuam bloqueadas quanto à independência externa. HOLDOUT lacrado, Rodent IV oficial intocado, **zero** personalidade histórica homologada. Sem exigência ou compromisso de contatar qualquer autor.
