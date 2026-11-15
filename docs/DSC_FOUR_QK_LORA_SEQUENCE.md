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
