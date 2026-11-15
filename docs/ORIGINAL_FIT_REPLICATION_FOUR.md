# Pre-registered 512-game independent sample — frozen original models

Protocol and seed fixed in issue #157 before execution.

The original DSC-150M and original Maia3-79M, with immutable published
weights/checkpoints and code commits, are compared on 512 **different**
historical game trajectories of the four pilot players: 128 per Fischer,
Tal, Karpov and Kasparov. Every position is a move from a FIT-year
game selected with seed 3109 after ply 20; at most one per real game,
with canonical-position and trajectory deduplication across players.

No final HOLDOUT or previously inspected VALIDATION positions are
evaluated. The original motors themselves are not trained by this
project; however, the FIT historical examples *were used to fit the
separate V7 semantic personalization*, so this is **not** an
independent audit of individual behavior. The Maia and DSC upstream
pretraining sets were not fully decontaminated: game independence from
external pretraining cannot be established.

One preregistered H1: DSC vs Maia79 top-1 game-macro gain positive, paired
95% game-bootstrap lower bound >0, paired two-sided sign-flip p<=0.05;
2,000 repetitions with seed 4109. Top3, heterogeneity by player,
timing and Linux process peak memory are secondary/descriptive.
Ratings 2400 are distinct scenario scales (Maia Lichess vs DSC FIDE);
the study does not estimate historical Elo or tournament strength.

If the result fails, publish it as negative. Even if H1 succeeds it
does not establish four differentiated personalities. Any personal
candidate needs a separate true out-of-time HOLDOUT and blind identity
test #154; official Rodent settings remain intact.


## Resultados efetivamente executados — revisão e auditoria

[GitHub Actions 43669423179](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43669423179) (SUCCESS, HEAD `766198545b88e68aa7e516e47ba27a49f3eefde1`), com qualidade Python 3.11/3.12/3.13 [run 43669423113](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/43669423113) (SUCCESS). Arquivo imutável de resultados `original-dsc-maia-four-512-fit-replication`, artefato **#14144026849**, ZIP SHA256 `f26d0b92524ba139a1daf10d6ac3a2ef1334e9ef1a7b19e70bd9e31da33cef55`. SHA lógico do relatório JSON `bb1a8a39f3fe198444e2182c87b71fac2d456042aaf7a8f9ed95b77d2d3cd5c8`.

| Jogador (128 partidas e 128 decisões cada) | DSC150 top-1 | Maia79 top-1 | DSC top-3 | Maia top-3 |
| --- | ---: | ---: | ---: | ---: |
| Fischer | 81 (63,28%) | 79 (61,72%) | 113 | 114 |
| Karpov | 79 (61,72%) | 74 (57,81%) | 112 | 105 |
| Kasparov | 82 (64,06%) | 72 (56,25%) | 116 | 110 |
| Tal | 77 (60,16%) | 73 (57,03%) | 116 | 112 |
| **Total** | **319/512 (62,30%)** | **298/512 (58,20%)** | **457/512 (89,26%)** | **441/512 (86,13%)** |

**Contraste primário pré-registrado:** diferença DSC−Maia **+4,1016 pontos percentuais**; IC95 bootstrap por partida **[+0,3906; +8,0078] pp**, valor-p bilateral de sinais pareados **0,04347826**, 2.000 repetições seed 4109. Critério da issue #157 **atingido**, com ressalva da proximidade do limiar 5%, da seleção de arquiteturas com base em análise anterior e da independência NÃO demonstrada do pré-treino externo.

Por jogador, nenhum dos 4 contrastes individuais ultrapassou o critério de inferência (Fischer p=0,801; Karpov p=0,451; Kasparov p=0,092; Tal p=0,514). O efeito agregado não implica que todos os quatro apresentam ganho confirmado.

**Custo do mesmo runner CPU:** DSC 80,338 s e pico RSS do subprocesso UCI 1.498.940 KiB; Maia79 480,943 s e 934.092 KiB. Esses tempos variam com infraestrutura e não são avaliação de força de jogo.

**Revisão adversarial do escopo:** fonte RAW e hashes são verificados, TREINO FIT e filtro V7 pré-fixados, seleção `seed=3109`, uma decisão por **trajetória globalmente distinta** e FEN canônica deduplicada entre os quatro jogadores, histórico real reconstruído até a jogada anterior, legalidade e ranking UCI verificados, sem consumo de VALIDATION ou HOLDOUT, assinatura de resultado com hash e upload com resultados agregados. Dois findings de implementação da primeira CI foram corrigidos no HEAD `7661985`: `policy_input.year` em vez de chave `year` ausente e import `chess` não usado. Re-revisão do diff e de ambas as CIs no mesmo HEAD não identificou finding acionável residual **no escopo deste benchmark de motores originais**.

**Nenhuma personalidade foi treinada nesta PR; integração oficial permanece negada.** A amostra FIT não é independente do treino dos adaptadores V7, apesar de não ter sido consultada no benchmark V8 dos motores originais. CHECKPOINTS externos não foram decontaminados e o holdout final permanece selado. A conclusão correta é *replicação de vantagem top-1 de motor original em outra amostra de desenvolvimento*, não certificação independente de quatro personalidades humanas.
