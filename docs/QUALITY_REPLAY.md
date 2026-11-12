# #24 — Barra de avaliação no replay do `watch` (incremento 1)

## Contrato e limites

A interface `watch` oferece **avaliações Stockfish pré-calculadas, opcionais e somente de leitura**. O relatório precisa ter sido produzido pelo comando `analyze-quality` para **um único arquivo final `jogo_N.pgn`**, com hash exato. Partidas parciais, relatórios obsoletos ou incompatíveis e posições não amostradas **não mostram avaliação**. Não há download, subprocesso Stockfish, POST, cálculo ao vivo, nem alteração de PGN, UCI, relógios ou Elo.

A análise existente amostra posições a partir de `--min-fullmove` (padrão 9), e `--max-positions` limita custo. O painel não interpola nem propaga a última nota para lances sem evidência. O estado inicial e o arquivo de outra partida permanecem **Indisponível**.

## Uso local

Com o PGN **final** selecionado de uma tentativa concluída e Stockfish obtido separadamente:
```bash
python -m chesssim analyze-quality \
  --pgn /CAMINHO_AUTORIZADO/duelo/jogo_1.pgn \
  --stockfish /CAMINHO_AUTORIZADO/stockfish \
  --depth 18 --min-fullmove 1 --max-positions 120 \
  --out /CAMINHO_AUTORIZADO/analise-nova.json

python -m chesssim watch \
  --run /CAMINHO_AUTORIZADO/duelo \
  --quality-report /CAMINHO_AUTORIZADO/analise-nova.json
```

Abra o URL loopback informado pelo comando. Somente a partida exata cujo arquivo corresponde ao `inputs.pgn_sha256` recebe avaliações; o relatório não é publicado como arquivo navegável. O `watch` habitual, sem `--quality-report`, tem comportamento anterior, sem avaliações.

## Semântica

- A avaliação da decisão `ply=N` é mostrada **após** sua reprodução; o score é a busca da continuação forçada do lance jogado, calculada **antes desse lance** pelo `analyze-quality`. Não é uma nova avaliação da posição posterior independente.
- A perspectiva é sempre **brancas**: `+1.25` indica 125 cp favoráveis às brancas na análise amostrada, `M+3` mate a favor das brancas e `M-2` a favor das pretas. O medidor é escala visual suavizada, **não porcentagem de vitória**.
- Marcadores de lance: perda desde a melhor linha nessa busca `>=100 cp` = *crítico*; `>=250 cp` = *grande perda*. Os limiares são **descritivos e dependentes de profundidade**, não uma nota de precisão Chess.com.
- Versão do motor, profundidade e perda de CP são mostradas em texto; diferenças entre depths, UCI, perfis, cor e livro não certificam estilo/força.
- Se a análise não contém um `ply`, exibe `Indisponível`, não zero, não 50%, não “lance bom”.
- O visualizador via Arena `/viewer` **não carrega automaticamente** este arquivo; o opt-in fica no comando `watch`, escopo inicial intencional.
- Dados de saída local ficam sob a responsabilidade do operador e não devem ser commitados.

## Gate

Testes Python de formato/versão/PGN/hash, `ply` e UCI/FEN, números finitos, symlinks e HTTP somente leitura; harness Node para renderização de score e estado desconhecido. A homologação real de Chrome, zoom, Stockfish e Rodent IV pertence à #83 e permanece `NOT_RUN` até ocorrer. Estatísticas agregadas/fases por perfil ficam para a segunda fatia da #24.


## Incremento 2 — gráficos descritivos por faixa/participante (#24)

No mesmo comando `watch --quality-report` do incremento 1, o painel **Perdas de avaliação por faixa e participante** aparece **somente** quando todas estas condições são verdadeiras: arquivo final `jogo_N.pgn` com SHA-256 idêntico ao relatório; relatório de `analyze-quality` v9.1 válido; recibo `summary.json` com `status=COMPLETED` e resultado compatível com o PGN (`1-0`, `0-1` ou `1/2-1/2`). Um PGN incompleto ou recibo divergente não gera gráfico nem é contado como observação verificada.

- **Faixas cronológicas heurísticas:** meios-lances 1–16, 17–60, 61+. Não são fases calculadas por material ou estratégia. O filtro mostra todas ou uma faixa, mas **não altera** o arquivo, a partida ou a análise Stockfish.
- **Por participante/lado:** cada lance pertence às brancas se o ply é ímpar, às pretas se é par. Nome e eventual `WhiteModel`/`BlackModel` são **declarações do PGN**, não comprovação da identidade do jogador histórico nem de um perfil calibrado.
- **Denominador:** `Amostras` = decisões vinculadas ao PGN exato; `N elegível` = somente decisões com `mate_affected == false` e `search_disagreement == false`, explicitamente declarados pelo analisador na profundidade-base. Decisões com mate, divergência da busca ou flags ausentes são **excluídas da média centipawn**, mas **mantidas na contagem de amostras**. Não apresentam zero no lugar de dados ausentes.
- **Valor:** média aritmética de `cp_loss` dos **N lances elegíveis** naquele grupo. As barras são **relativas ao maior valor visível**; 100% da largura **não** significa precisão, chance de vitória ou intensidade absoluta. N=0 = `Indisponível`.
- **Proveniência:** nome declarado pelo motor e profundidade-base; prefixos de 12 caracteres dos SHA-256 do PGN e do executável Stockfish. A comparação é descritiva de **uma única partida**; o mesmo jogador em várias partidas não é agregado, porque os lances são correlacionados e a amostragem do analisador não é equivalente a um ensaio independente.
- **Sem vazamento da reprodução:** durante playback automático de uma partida concluída, os gráficos só são exibidos quando sua última posição é alcançada. Navegar intencionalmente no replay libera a consulta, preservando a posição selecionada.
- **Exclusões obrigatórias:** nenhuma amostra `fast_statistical`, motor fictício ou relatório histórico não verificado entra no gráfico; não se usa corpus/holdout para calibrar ou selecionar perfis. Os testes de estabilidade por profundidade e comparações pareadas continuam disponíveis em `compare-quality` / `paired-evidence`, com proveniência, sem converter essas comparações em um novo escore de identidade na interface.

A atualização de `/api/state` entrega apenas a estatística calculada pelo servidor junto à partida selecionada. O cliente não lê arquivos locais arbitrários, não inicia processo UCI e não envia POST. Relatórios que não correspondam ao PGN selecionado preservam `Indisponível`.

**Gate de QA:** executar no Chrome/Debian com uma partida Rodent IV real, PGN concluído e análise Stockfish autorizada, alternando replay, partida, faixa e tema; validar teclado, foco, zoom/mobile e cancelamento de stream SSE em #83. Até esse ensaio, marcar `NOT_RUN`, mesmo com CI verde. Este incremento não encerra automaticamente #24.
