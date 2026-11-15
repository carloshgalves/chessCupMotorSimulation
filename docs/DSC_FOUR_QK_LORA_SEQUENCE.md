# Experimento DSC-150M · LoRA Q/K, quatro identidades por sequência (v1)

Pré-registro: [issue #161](https://github.com/carloshgalves/chessCupMotorSimulation/issues/161), comentário com seed, split e critérios **anterior à primeira execução AUDIT**.

## Modelo e dados

O backbone [DSC-150M original](https://github.com/ChessDS/chessds-dsc)
permanece congelado, com commit
\`229ef4291c38c088a9fd3a410e4c0e926a20cea1\`
e checkpoint SHA256
\`66b21d7882bb3fb9737bc798e709671325838e5469ed6bfcf114b3abbcb48e24\`.
O modelo consulta o histórico de PGN real, cenário FIDE 2400 e
tempo clássico. O valor 2400 é um cenário, não o Elo real dos quatro
jogadores em partidas históricas.

Capturamos \`DSCModel.ln_f\` com forward hook durante
\`DSC.predict_batch(PositionInput)\` genuíno. A saída \`[64, width]\`
e as projeções Q/K congeladas são mantidas **somente na memória do
runner**. O checkpoint publicado contém somente os deltas LoRA e
hashes; não contém latents, PGNs ou pesos DSC originais.

O complemento treinável é **Bq@Aq** e **Bk@Ak**, ranks 2/4/8,
adicionados às projeções Q/K existentes no cálculo por lance legal.
A probabilidade original DSC para todo lance legal é o prior
normalizado; diferenças dos produtos Q·K, inclusive offsets
de subpromoções via pesos imutáveis \`PolicyHead.promo\`, alteram
os logits, nunca o WDL ou as camadas Transformer.
Os canais correspondem à numeração \`move_index\` do DSC em
orientação do jogador que move. O original pode ser restaurado
instantaneamente omitindo o adaptador.

## Protocolo FIXADO ANTES do AUDIT

Somente **Fischer, Tal, Karpov e Kasparov**. Os anos TRAIN são
divididos por SHA-256 de trajetória em FIT 70%, TUNE 10% e
AUDIT 20%, com seed 3719 e sal
\`chesssim-latent-qk-v1-2026-11-15\`. A FEN canônica
compartilhada entre braços é totalmente embargada antes da
seleção. A mesma partida não pode representar dois jogadores
na amostra final.

- FIT: **48 partidas × 8 decisões consecutivas por jogador**.
- TUNE: **12 partidas × 8 decisões por jogador**.
- AUDIT: **12 partidas × 8 decisões por jogador**.

São, respectivamente, **1.536**, **384** e **384** decisões, com
partidas distintas entre braços e jogadores. O protocolo falha
por \`UNDERPOWERED\` quando a fonte não permite as janelas fixas.
Não amplia amostras nem consulta VALIDATION ou HOLDOUT como substituição.

Ranks 2/4/8 e épocas (3 genérico, 4 por jogador) são
escolhidos **apenas TUNE**, com desempate conservador
e limiar de 0,005 nat por decisão para ativação de uma
identidade específica. Os modelos foram treinados apenas
com cross-entropy da jogada FIT, KL ao prior original
(\`0.03\`) e regularização ao head genérico (\`0.0005\`),
AdamW e uma thread CPU. O tamanho dos heads é pequeno
comparado ao original de 150 milhões de parâmetros.

## Identidade cega e controle

AUDIT calcula top-1, top-3, log-loss e diferenças com IC95
por partida para DSC original, LoRA genérica, LoRA correta
e LoRA de nome trocado. Para identificar uma partida, o
classificador lê apenas a soma/média das log-verossimilhanças
dos **oito lances humanos** sob cada perfil congelado.
A identidade correta é usada **somente para pontuar** o
teste cego, não como atributo de entrada.
A matriz 4×4 é avaliada com IC95 bootstrap estratificado
por partida (2.000 amostras), teste de permutação de
rótulos (2.000 permutações), baseline majoritária e
acaso nominal de 25%. Também medimos identidade por
um único lance como controle negativo.

Mesmo que um gate de *candidato de pesquisa* seja positivo,
**não** se permite automaticamente integrar em torneios:
permanece obrigatória a avaliação final com holdout histórico
congelado, decontaminação de pré-treino externa quando possível,
segurança Stockfish, autorização humana e retorno reversível
ao Rodent IV original. O registro do AUDIT é irrevogável como
resultado desta versão: nenhuma mudança pós-hoc de rank,
split ou seed para forçar sucesso.


## Direitos e distribuição

O DSC-150M original tem pesos disponibilizados sob
**CC BY 4.0**, com atribuição a **Albert W. Hamood / ChessDS**;
o código de inferência é **GPL-3.0-or-later**. Referências:
[model card DSC](https://github.com/ChessDS/chessds-dsc/blob/229ef4291c38c088a9fd3a410e4c0e926a20cea1/model_cards/dsc-150m.md)
e [repositório](https://github.com/ChessDS/chessds-dsc).
A PR distribui somente código ChessCup, deltas LoRA
e metadados agregados do experimento, **não** redistribui
os checkpoints externos, os PGNs originais nem caches de
representações latentes. Qualquer distribuição comercial
futura deverá preservar atribuição e cumprir as licenças
correspondentes.


## Primeiro benchmark real e correção adversarial (não confirmatória)

[CI científica #43736828358](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43736828358)
foi **SUCCESS** no primeiro commit da PR #162; o artefato
`chesssim-four-genuine-dsc-qk-lora-blind-sequences` (ID
**14171340649**) tem SHA-256 ZIP
`6fc288d19d2e2e46ead4db2fd38ad6907ef339d211f0300367000c782d0746f5`.
Relatório lógico `e2be7a1aae92e8142859462f777aae638c73441e6431c8aae5be9564da05e45e`;
checkpoint derivado `5a71cb6409440e1f4eef7615e582f1ce9a1931b42347f8de849389ab020890f7`.

Foram **1.536 FIT, 384 TUNE e 384 AUDIT** decisões reais,
em 288 partidas globalmente distintas e janelas de oito jogadas
por jogador. **589** FENs canônicas foram embargadas entre braços.
DSC original no AUDIT: **231/384 = 60,16%**; LoRA genérica
**232/384 = 60,42%**. Todas as cabeças individuais
falharam a regra de ativação de TUNE e, de acordo com o
pré-registro, voltaram à cabeça genérica: **232/384**.
O classificador cego de oito jogadas obteve **12/48 = 25%**
(acaso nominal), IC95 [25%;25%] em distribuição degenerada
porque todas as políticas estavam iguais; p de permutação **1,0**.
**0/4 personalidades aprovadas**. Latência para inferência
original no CPU runner ~262 s, treino LoRA ~48 s; pico RSS
do processo Python **2.208.592 KiB**. Não extrapolar timings
para notebook ou outros processadores.

### Finding de utilidade da auditoria

A política conservadora bloqueou corretamente os quatro heads,
mas o artefato original **descartava as matrizes pessoais rejeitadas**
e o teste cego virava trivialmente 25% pela substituição automática
pela cabeça genérica. Isso não revelava se os heads pessoais
possuíam algum *sinal diagnóstico* nas mesmas posições.
A correção preserva o modelo TUNE-selecionado de cada jogador em
`rejected_or_exploratory_candidate`, marcado
`NEVER_PROMOTE_FROM_DIAGNOSTIC`, **sem ativá-lo**.

Acrescentou-se um segundo classificador
`posthoc_rejected_head_probe` sobre o **MESMO AUDIT JÁ INSPECIONADO**.
Esse resultado é explicitamente
`POST_HOC_EXPLORATORY_SAME_AUDIT_NOT_CONFIRMATORY`, **nunca pode**
ser usado para promoção ou alteração retroativa dos gates,
ranks, split, seed ou protocolo. Os resultados do primeiro
teste primário (0/4 e acaso 25%) são preservados intactos.
Essa correção de transparência exige nova execução completa
da CI científica antes de qualquer merge.

Outros findings de implementação corrigidos: três imports
não usados (F401) identificados na primeira CI Python;
novos testes negativos confirmam que o lance humano e
seu hash de partida não entram no cálculo dos logits
e que os parâmetros Q/K/promo originais não recebem gradientes.
A revisão final ainda depende de novas CIs e de re-review.
