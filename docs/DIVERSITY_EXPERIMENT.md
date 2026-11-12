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


## Incremento 2 — executor local controlado (PR #114)

O comando `diversity-run` transforma **uma parte do plano pré-registrado em execuções Rodent IV reais** quando o operador dispõe dos perfis e executável autorizados. O comando não executa ensaios nesta CI e não garante aumento de diversidade: somente registra resultados de jogos reais que forem executados no host.

**Experimento sem repertório externo** (controle de ausência de efeito da seed):

```bash
python -m chesssim diversity-plan --players fischer tal \
  --seed 2029 --trials 2 --out /dados/ensaio-plano.json

python -m chesssim diversity-run \
  --plan /dados/ensaio-plano.json \
  --rodent /CAMINHO_AUTORIZADO/RodentIV \
  --out /dados/ensaio-execucao-nova \
  --blocks 1 --depth 8 --max-plies 400 \
  --max-game-wall-seconds 600
```

**Com repertório histórico externo autorizado**, gerar previamente o plano com
`--external-repertoire` e fornecer um ou ambos os arquivos `--white-book` /
`--black-book` ao `diversity-run`. Esses nomes se referem **à primeira e
segunda pessoa do plano**, não à cor que terão em cada partida. Os livros devem
ser criados no fluxo `opening-build` com a proveniência de treino aplicável e
restrições de embargo. A mera presença de arquivo/declaração **não é prova**
de ausência de vazamento do holdout: o operador deve auditar o dataset e suas
licenças antes de usar. Sem repertório, a seed não controla a pesquisa interna
do Rodent e nenhuma diversidade extra deve ser esperada.

### Contrato de execução

- O executor valida o JSON contra uma reconstrução determinística de
  `diversity-plan`. Uma alteração em seed, jogador, condição, variável fixa,
  status ou impressão digital é rejeitada **antes de iniciar UCI**.
- Cada bloco usa três braços **na ordem A1/A2/B**, com seeds A1=A2 e B distinta,
  motor/profiles/hashes, cores, profundidade e repertórios idênticos. Cada
  braço executa **duas partidas de cores invertidas**, numeradas 1 e 2, em
  diretório próprio e novo. Os parâmetros UCI e as trajetórias nunca são
  modificados para forçar variedade.
- A execução começa em pasta que **não pode existir**; `--blocks N` executa
  os primeiros N blocos (padrão 1), sem retomada implícita nem sobrescrita.
  O modo `--depth` é limitado a 1–30, `--max-plies` a 2–600 e orçamento
  por partida a 1–7200 segundos. Use o wrapper cgroup opt-in da #27 se
  precisar de limitação adicional de recursos.
- `execution.json` conserva hash SHA-256 do plano original, impressão do
  desenho pré-registrado, perfis e motores congelados, hash de cada repertório,
  parâmetros, resultado de cada braço e SHA/identidade de cada PGN e resumo.
  A confirmação de braço exige `summary.json` `COMPLETED`, **duas partidas
  completas**, ambas com resultado, SHA da linha principal e números de
  meios-lances reconciliados com os PGNs locais.
- Qualquer falha de motor, recusa do recibo, adulteração de hash ou timeout
  impede avançar para o próximo braço: o manifesto é marcado
  `FAILED_REQUIRES_MANUAL_INSPECTION`. Os arquivos ficam preservados para
  perícia. **Nunca** executar novamente no mesmo diretório; decidir
  manualmente como registrar uma nova tentativa e manter o histórico anterior.
- O status de êxito é **`EXECUTED_RESULTS_NOT_CALIBRATED`**: houve execução
  e reconciliação de recibos, **não** validação histórica, prova de diversidade
  efetiva nem independência estatística. A1/A2 são controles negativos;
  resultados divergentes nessas condições sinalizam não determinismo residual.
- O executor não consulta corpus histórico, não abre dados de teste lacrados e
  não altera ratings, estilos, livros ou partidas do torneio oficial. O
  operador deverá rodar `diversity-report`, as avaliações independentes
  #18/#19/#20 e testes de controlabilidade de seed antes de decidir se
  presets de entretenimento podem ser habilitados com segurança.

**Gate ainda pendente #83:** rodar de fato no Debian com Rodent IV e livros
legítimos, verificar logs UCI, árvores de processos, orçamento, cores,
hashes, repetibilidade A1=A2, diferenças A/B e qualidade Stockfish. Testes
Python usam motor **simulado** e comprovam o contrato de orquestração,
não substituem esses ensaios físicos. #69 continua aberta.
