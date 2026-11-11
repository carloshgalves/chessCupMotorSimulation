# Auditoria de diversidade entre duelos — issue #69

O comando `diversity-report` compara **jogadas efetivamente gravadas** em vários duelos locais do ChessCup, sem abrir motores, consultar fontes externas, reescrever PGNs ou interferir nos experimentos históricos lacrados.

## Uso no Debian

Com `python-chess` instalado no ambiente virtual:

~~~bash
python -m chesssim diversity-report \
  --runs "$HOME/Projetos/Homologacao/ChessCup/evidencias" \
  --opening-plies 12 \
  --out "$HOME/Projetos/Homologacao/ChessCup/diversidade.json"
~~~

Aponte `--runs` para uma ou mais pastas que contenham duelos produzidos pela função `play_duel`, cada qual com `summary.json` e `jogo_N.pgn`. O arquivo de saída **não pode existir**, evitando substituir relatórios anteriores. Sem duelos verificáveis, com PGN faltando ou ilegítimo, com symlink, resumo divergente, número de plies inconsistente ou resultado contraditório, a ferramenta falha sem produzir estatísticas aparentemente corretas.

## O que o relatório demonstra

- **Repetição exata:** mesmo FEN inicial e sequência completa de movimentos UCI. Cabeçalhos PGN, nomes de eventos e datas não transformam uma partida igual em nova observação. Essas repetições permanecem identificadas, mas não são evidências independentes.
- **Transposição de abertura:** duas ordens legais de lances chegam à **mesma posição** após N meios-lances. Isso é apenas um diagnóstico e **não** transforma partidas diferentes em duplicatas.
- **Comparações agrupadas:** par de jogadores (sem depender da ordem das cores), modo de abertura e semente declarada, quando disponível. São exibidas quantidade total, partidas concluídas, sequências distintas e duplicatas exatas.
- **Partidas incompletas:** aparecem na contagem diagnóstica; não são tratadas como resultados concluídos de vitória, empate ou derrota.
- **Proveniência:** relatório inclui origem relativa dentro das pastas fornecidas, hash de sequência de lances e hash de posição de abertura. Nenhuma referência ao caminho absoluto do computador é incluída.

A presença de `OpeningSeed` **não garante** diversificação. Essa semente influencia a política de repertório histórico externo somente quando o modo correspondente foi habilitado; não controla automaticamente a busca nem o livro interno de um motor UCI. Alterar semente ou escolher abertura diferente não comprova qualidade, identidade histórica, nível de jogo ou melhora da experiência.

## Limitações e próxima etapa

Esta é a etapa de **medição auditável** da #69: comparar corridas e quantificar repetições antes de testar estratégias de diversificação. Não há alteração de perfis, parâmetros UCI, força das jogadas, decisões históricas, mecanismo de pontuação ou política de seleção de abertura.

Para avançar a #69, será necessário executar grupos controlados de duelos no Rodent IV real, mantendo perfis e controles de tempo, variando apenas os fatores explicitamente autorizados. Depois comparar proporção de duplicatas, posições transpostas e impacto na qualidade/fidelidade, sem usar holdout lacrado e sem afirmar melhora antes da medição.

A CLI é um diagnóstico offline. Não é, por si só, homologação da experiência visual no Chrome/Debian ou da diversidade efetiva dos 64 perfis.
