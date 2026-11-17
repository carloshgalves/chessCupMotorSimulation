# Real85 — calibração experimental de orçamento focal d4 × d6 (#170)

## Pré-registro prospectivo

A #185/#187 observou força relativa pequena do Rodent focal d4 contra
Simple d6 e erros atribuíveis ao próprio motor-base. Este protocolo testa
se aumentar apenas o orçamento focal para depth 6 altera os placares e
diagnósticos por jogador e fase. Não seleciona um peso neural novo nem
estima Elo histórico.

- Pilotos: Fischer, Tal, Karpov, Kasparov; mesmos checkpoints residuais
  congelados, mesmos livros reais, mesmos 12 inícios FIT documentados.
- Braços focais: d4 (controle) e d6 (candidato).
- Adversário: Simple d2/d6, sem livro; cores invertidas; SHADOW/GUIDED
  com o mesmo guard Stockfish depth8, confirmação depth12 e cutoff
  neural a partir de ply41. Não alterar esses limiares após os resultados.
- 96 partidas previstas por braço (4 jogadores × 3 raízes × 2
  profundidades do Simple × 2 cores × 2 modos); 192 no total.
- A matriz deve ser integralmente terminal e legal. Partidas INCOMPLETE
  não viram pontos nem empate.
- Comparação descritiva pareada por jogador, raiz, adversário, cor e modo:
  pontuação e contagem de pares melhor/igual/pior. Não inferir independência
  dos 96 pares, dado o reaproveitamento de posições e trajetórias.
- Auditoria Stockfish d14 nas FENs originais; separar livro, Rodent e
  residual, e finais por material. Contrafactual Rodent sem livro na
  **profundidade focal efetivamente executada**, comparado ao d8.
- Rodar os braços no mesmo ambiente e confrontar hashes de corpus/painel,
  binários, personalidades, livros e modelos. Divergência bloqueia comparação.
- FIT já foi usado em experimentos; nenhum resultado constitui confirmação
  independente. Nenhum acesso adicional ao outer TEST ou holdout por anos,
  nenhum ajuste pós-hoc, nenhuma alteração dos perfis originais.

## Execução

O workflow real85-graded-phase-attribution executa os dois braços com
os mesmos insumos congelados no mesmo runner. O comparador revalida as
partidas e produz depth-comparison.json, com placares por jogador e
profundidade do adversário. Artefatos incluem os PGNs realmente simulados
e os relatórios de fase; não distribuem o corpus PGN histórico.

## Interpretação

Este protocolo calibra *orçamento relativo de busca*, não rating humano.
Um eventual ganho a d6 não equivale a melhoria de estilo. Uma perda pode
indicar limitações de busca, abertura ou intervenção; os diagnósticos
devem ser analisados separadamente por jogador e fase. A expansão aos
demais nomes depende da cobertura documentada (#143/#184), da integração
opt-in e do gate científico. Não converter 43 adversários Simple em 43
perfis falsamente individuais.
