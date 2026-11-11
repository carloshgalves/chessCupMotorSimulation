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
