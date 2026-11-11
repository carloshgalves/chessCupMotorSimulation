# #69 — Planejamento pareado e pré-registro de um ensaio de diversidade

## O que esta fatia entrega

`diversity-plan` cria uma **matriz determinística somente de planejamento**, com dois jogadores canônicos e `N` blocos. Não abre UCI, não produz PGN, não baixa corpus, não altera livro, não seleciona lance, não calibra força e não toca em holdout histórico.

```bash
python -m chesssim diversity-plan \
  --players fischer tal --seed 2029 --trials 6 \
  --out /CAMINHO_AUTORIZADO/ensaio-pareado.json
```

Cada bloco possui três condições: `A1_RESEARCH_CONTROL` com a semente base, `A2_EXACT_REPLICATE` com **mesma** semente (controle negativo) e `B_ALTERNATE_SEED` com semente diferente. Todos devem preservar *versões/hashes de motor, perfil, livro*, controle de tempo, profundidade, ordem de cores, **número da partida/rodada** e política de repertório. A escolha de livro externo usa `seed`, número de jogo e FEN em seu hash: só a seed deve variar entre A e B. O SHA-256 do plano é a impressão digital de seu conteúdo canônico, **não de uma partida executada**.

Por padrão o relatório afirma `NO_EFFECT_EXPECTED_WITHOUT_EXTERNAL_REPERTOIRE`: a semente não controla a busca interna do Rodent IV. A opção `--external-repertoire` apenas registra a **declaração não verificada do operador**; não confirma que o livro foi instalado ou que há múltiplos lances historicamente sustentados.

## Execução humana futura no Debian (#83)

1. Verificar autorização e versão do conjunto PGN e do repertório, usando dados de **treino**, nunca amostras do teste lacrado.
2. Criar pasta **nova** por cada condição; executar confrontos reais com os perfis/hashes fixados, alterando exclusivamente a semente quando tiver efeito no livro externo. Sem repertório externo, o braço B é controle de ausência de efeito.
3. Executar `diversity-report --runs <pastas...> --out <novo.json>` para comparar duplicatas de trajetória, transposições, seeds e partidas completas/incompletas, mantendo classificação de pseudorreplicação.
4. Usar #18/#19/#20 para verificar perda de qualidade e estabilidade *com a mesma amostra e a mesma profundidade* antes de considerar presets de entretenimento/diversificação. Não forçar lances piores/ilegais nem contar clones como observações independentes.
5. Documentar N, critérios pré-registrados, relógios, hashes, falhas, intervalos e incerteza. Não prometer diferenças reais entre partidas nem afirmar fidelidade histórica sem controles negativos/validação cega #10–#12.

## Por que não muda a política do motor agora?

A issue #69 propõe presets **fidelidade/reprodutibilidade** vs **entretenimento/diversidade**. Implementar troca arbitrária de lances agora distorceria o corpus e confundiria hipótese de estilo com entretenimento. Esta fatia entrega o desenho testável para decidir o efeito antes de permitir variação. **Presets executáveis continuam pendentes**, com gate experimental em #83. O novo comando não fecha #69.
