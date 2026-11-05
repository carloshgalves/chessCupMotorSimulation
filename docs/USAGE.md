# Manual de execução e exemplos reproduzíveis

Este guia descreve o fluxo operacional disponível na CLI atual. Os exemplos usam Bash em Linux/WSL e caminhos genéricos relativos ao repositório. Nenhum caminho local, executável de motor ou corpus PGN é presumido.

> **Limite importante:** instalar o pacote Python não instala Rodent IV, Stockfish nem bases históricas. Esses recursos são externos e precisam ser obtidos e configurados separadamente. Consulte também a [auditoria de dependências, fontes e distribuição](THIRD_PARTY.md).

## 1. Requisitos e instalação

- Python 3.11 ou superior, Git e venv.
- Uma instalação funcional do [Rodent IV](https://github.com/nescitus/rodent-iv) para gerar partidas. O projeto não baixa o executável do motor.
- [Stockfish](https://stockfishchess.org/download/) instalado separadamente somente para análises independentes de qualidade.
- Arquivos PGN históricos obtidos pelo usuário de fontes apropriadas ou fornecidos localmente.

~~~bash
git clone https://github.com/carloshgalves/chessCupMotorSimulation.git
cd chessCupMotorSimulation

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

python -m chesssim --help
python -m chesssim roster
~~~

O pacote também instala o comando de console chesssim. Os exemplos usam python -m chesssim para deixar explícito qual ambiente Python executa a aplicação. Para executar apenas a aplicação, sem ferramentas de desenvolvimento, python -m pip install -e . é suficiente; o extra .[dev] também instala pytest, build e Ruff.


### 1.1. Instalação automatizada Linux/WSL

O fluxo recomendado para máquinas novas está em [Setup Linux/WSL e smoke test UCI real](SETUP_LINUX_WSL.md). Ele executa pip check e pytest, verifica caminhos configurados por `RODENT_IV_HOME` e `CHESSSIM_UCI_ENGINE`, e só altera os perfis quando `--apply-profiles` é fornecido:

~~~bash
bash scripts/setup_linux_wsl.sh
bash scripts/setup_linux_wsl.sh --skip-install --skip-tests --rodent "/caminho/para/Rodent-IV" --smoke
~~~

O PGN de smoke com limite de meios-lances pode ser **INCOMPLETE**, o que não é um empate. Os relatórios são gravados em destino exclusivo para não sobrescrever histórico de testes.

## 2. Configurar o Rodent IV

A variável RODENT_IV_HOME aponta para a **raiz** da instalação. O projeto espera encontrar esta estrutura:

~~~text
<RODENT_IV_HOME>/
├── rodent-iv-plain.exe
├── personalities/
│   ├── basic.ini
│   └── <arquivos de personalidade .txt>
└── books/
    └── <livros Polyglot, quando exigidos pelos perfis>
~~~

O nome rodent-iv-plain.exe é o nome de arquivo esperado pelo código. O executável precisa ser iniciável no sistema operacional em que o Python está rodando.

No Bash:

~~~bash
export RODENT_IV_HOME="/caminho/para/Rodent-IV"
test -f "$RODENT_IV_HOME/rodent-iv-plain.exe"
test -f "$RODENT_IV_HOME/personalities/basic.ini"
python -m chesssim check
~~~

No PowerShell:

~~~powershell
$env:RODENT_IV_HOME = "C:\caminho\para\Rodent-IV"
python -m chesssim check
~~~

Os comandos que aceitam --rodent permitem informar a raiz diretamente; esse argumento prevalece sobre RODENT_IV_HOME. Não passe o caminho do executável como valor da variável: passe a pasta que o contém.

### 2.1. Verificar e instalar os perfis oficiais

roster lista os 64 participantes. Nesta revisão do catálogo, 21 possuem alias oficial Rodent reconhecido; os demais continuam pendentes de construção de perfil.

~~~bash
python -m chesssim roster
python -m chesssim check
~~~

setup obtém os arquivos de personalidade ausentes do repositório upstream do Rodent IV, em uma revisão fixada pelo código. A opção --with-books também obtém os livros Polyglot referenciados pelos perfis. A instalação do executável do motor continua sendo uma etapa separada.

setup começa em modo de prévia e não altera a instalação:

~~~bash
python -m chesssim setup
~~~

Para autorizar a instalação dos perfis ausentes e o registro dos aliases:

~~~bash
python -m chesssim setup --apply
python -m chesssim check
~~~

Se também forem necessários os livros referenciados pelos perfis oficiais:

~~~bash
python -m chesssim setup --apply --with-books
python -m chesssim check
~~~

Cuidados:

- setup não instala nem substitui o executável do Rodent IV.
- Arquivos de personalidade já existentes não são sobrescritos.
- Antes de modificar personalities/basic.ini, o programa cria backup em personalities/_backups/.
- A instalação recusa um conjunto de personalidades ativo diferente do primeiro conjunto. Inspecione a instalação antes de qualquer ajuste manual.
- A prévia não aplica mudanças. A mutação local exige explicitamente --apply.

## 3. Executar um duelo

Escolha participantes com perfil disponível:

~~~bash
python -m chesssim roster
python -m chesssim check
~~~

Exemplo de duas partidas com cores invertidas:

~~~bash
python -m chesssim play fischer tal --depth 6 --reverse --max-plies 400 --out runs
~~~

Os identificadores podem ser os slugs apresentados por roster ou o nome completo cadastrado. O exemplo usa dois participantes com perfis oficiais no catálogo.

Opções relevantes:

- --depth N: profundidade de busca por lance. O padrão é 6.
- --no-books: desliga o livro UCI em duelo de desenvolvimento/smoke; não permitido com --preset official (protocolo oficial exige livros).
- --movetime SEGUNDOS: tempo de busca por lance, alternativa a --depth. Os dois argumentos são mutuamente exclusivos.
- --reverse: executa uma segunda partida com as cores trocadas. Sem essa opção, é gerada apenas uma partida.
- --max-plies N: limite de meios-lances (plies), não de lances completos. O padrão é 400.
- --out DIR: diretório-base dos artefatos.

O programa cria uma subpasta exclusiva por tentativa dentro do diretório informado, no padrão <brancas>-vs-<pretas>/<timestamp>-<id>/. Os principais arquivos são:

- jogo_1.pgn e, quando houver inversão, jogo_2.pgn;
- duelo.pgn, com as partidas do confronto;
- summary.json, com estado, resultados, término, parâmetros e hashes dos recursos utilizados.

O caminho efetivo é informado no terminal. Uma partida interrompida pelo limite de meios-lances fica marcada como incompleta; não é convertida artificialmente em empate.

### 3.1. Presets de relógio

play também aceita --preset. Todos os presets exigem --reverse, para que os dois participantes joguem com ambas as cores.

| Preset | Relógio configurado | Observação |
|---|---:|---|
| official | 10 min + 2 s | Protocolo interno do projeto; exige os livros referenciados pelos perfis |
| classical | 60 min + 30 s | Preset experimental |
| rapid | 15 min + 10 s | Preset experimental |
| blitz | 3 min + 2 s | Preset experimental |
| bullet | 1 min + 0 s | Preset experimental |

Exemplo:

~~~bash
python -m chesssim play fischer tal --preset blitz --reverse --out runs
~~~

Os presets experimentais padronizam a execução; não comprovam o ritmo histórico de um jogador nem equivalência de força. official identifica o protocolo interno da Copa, não uma afirmação de que esse relógio reproduz todas as condições históricas.

Para usar --preset official, verifique se os livros Polyglot exigidos pelos perfis estão instalados. Quando necessário, use setup --apply --with-books e confira novamente a instalação.

### 3.2. Sorteio de confrontos

O sorteio gera os 32 confrontos da primeira rodada, mas não executa partidas:

~~~bash
python -m chesssim draw --seed 2026 --out runs/sorteio-2026.json
~~~

A semente permite reproduzir o mesmo sorteio. O comando recusa sobrescrever o arquivo de saída.

## 4. Obter e preparar corpora PGN

O repositório não contém os corpora históricos. A CLI consulta o [índice público do PGN Mentor](https://www.pgnmentor.com/files.html), mas esse índice é uma fonte de partidas disponíveis, não uma garantia de completude da carreira de qualquer jogador.

### 4.1. Consultar a disponibilidade

~~~bash
python -m chesssim corpus-sources --out datasets/pgnmentor-index.json
~~~

O relatório registra a data de consulta, o endereço do índice, seu hash e a disponibilidade encontrada para cada jogador do elenco. A disponibilidade pode mudar quando a fonte externa é atualizada.

### 4.2. Pré-visualizar e baixar partidas

Prévia sem download:

~~~bash
python -m chesssim corpus-fetch mequinho --out datasets/pgnmentor --dry-run
~~~

Download de um jogador:

~~~bash
python -m chesssim corpus-fetch mequinho --out datasets/pgnmentor
~~~

Para consultar/baixar os jogadores listados no catálogo:

~~~bash
python -m chesssim corpus-fetch all --out datasets/pgnmentor --dry-run
python -m chesssim corpus-fetch all --out datasets/pgnmentor
~~~

O comando usa o índice consultado no momento da execução, respeita o intervalo padrão de dois segundos entre downloads (ajustável por --delay), não sobrescreve PGNs existentes, informa jogadores não listados e grava um arquivo .source.json ao lado de cada PGN baixado, com proveniência e hashes.

O arquivo obtido representa somente as partidas contidas no pacote disponibilizado pela fonte. Não o descreva como corpus completo.

### 4.3. Consolidar arquivos PGN locais

corpus-build aceita arquivos PGN e diretórios contendo arquivos .pgn. Ele filtra partidas do jogador identificado, rejeita registros inválidos ou variantes não suportadas, remove duplicatas segundo a sequência de lances e metadados e gera um manifesto de auditoria.

Exemplo para Mequinho:

~~~bash
python -m chesssim corpus-build mequinho --match-name Mecking --sources datasets/pgnmentor/mequinho.pgn --out datasets/corpus/mequinho.pgn
~~~

Também é possível passar um diretório ou vários arquivos:

~~~bash
python -m chesssim corpus-build fischer --match-name Fischer --sources datasets/pgnmentor/fischer.pgn datasets/outra-fonte --out datasets/corpus/fischer.pgn
~~~

O nome informado em --match-name deve corresponder ao identificador do jogador nos cabeçalhos PGN. A identificação é feita por correspondência de palavra nos campos White e Black; se o nome aparecer de formas diferentes entre fontes, inspecione os cabeçalhos antes de consolidar.

Para cada corpus, o programa grava também um manifesto .manifest.json com hashes, fontes, contagem de partidas aceitas e exclusões. A completude permanece desconhecida. O comando recusa sobrescrever o PGN ou o manifesto existentes; escolha um novo caminho para uma nova versão.

## 5. Construir e usar um repertório histórico

O repertório é uma política opcional de escolhas nas posições iniciais. Ele não melhora por si só a força do motor e não constitui prova de fidelidade histórica. O relatório registra a origem e o hash do PGN usado.

### 5.1. Repertório de Mequinho

Para Mequinho, opening-build exige a cadeia de evidências da calibração v5 e o embargo da calibração v0.3 anterior. Use os artefatos originais, sem editá-los:

~~~bash
python -m chesssim opening-build mequinho \
  --pgn datasets/corpus/mequinho.pgn \
  --match-name Mecking \
  --v5-report /caminho/para/run-v5/calibration.json \
  --legacy-v3 /caminho/para/run-v3/calibration.json \
  --legacy-pgn /caminho/para/Mecking-original.pgn \
  --out datasets/opening-books/mequinho-v09.json
~~~

Se o arquivo de registro de coorte v5 não estiver no caminho armazenado no relatório, informe também --cohort-ledger /caminho/para/registro-v5.json. O registro precisa ser o original correspondente àquele relatório e ao mesmo PGN.

O comando verifica hashes e partições. O relatório v5 precisa continuar sem seleção solicitada e com o teste final lacrado. O PGN original da v0.3 deve corresponder ao hash registrado naquela calibração. Se a cadeia não puder ser comprovada, a execução deve falhar; não substitua esses arquivos por versões aproximadas.

### 5.2. Repertório exploratório de outro jogador

Para jogadores diferentes de Mequinho, a implementação atual só permite construir um repertório sem partição histórica congelada mediante autorização explícita:

~~~bash
python -m chesssim opening-build fischer --pgn datasets/corpus/fischer.pgn --match-name Fischer --ack-unpartitioned --out datasets/opening-books/fischer-exploratory.json
~~~

Esse resultado é exploratório e não validado historicamente. Não use --ack-unpartitioned para contornar a cadeia de embargo de Mequinho: o código não aceita essa substituição.

O arquivo de saída não pode existir previamente. O limite de repertório é controlado por --max-plies (padrão 16, intervalo permitido de 2 a 40); --from-year e --to-year podem restringir o período.

## 6. Gerar, instalar e jogar com o perfil provisório de Mequinho

O perfil v0.8 é uma personalidade experimental jogável, não uma reconstrução historicamente validada. A instalação é separada dos perfis oficiais.

### 6.1. Pré-requisitos da construção

A construção de Mequinho requer:

1. O corpus PGN cuja hash corresponda à calibração v5.
2. O relatório v5 original, com seleção não solicitada e teste final ainda lacrado.
3. O relatório v0.3 original e o PGN original usado naquela calibração, necessários para reconstruir o embargo anterior.
4. O registro de coorte v5 original, no caminho indicado pelo relatório ou informado por --cohort-ledger.
5. A instalação Rodent IV e os perfis-base necessários.

O registro de janela histórica distribuído no repositório classifica a janela de Mequinho como provisória. Por isso, o exemplo usa --allow-provisional; essa opção aceita a hipótese de janela para fins exploratórios, mas não a transforma em evidência aprovada.

~~~bash
python -m chesssim build-provisional mequinho \
  --pgn datasets/corpus/mequinho.pgn \
  --v5-report /caminho/para/run-v5/calibration.json \
  --legacy-v3 /caminho/para/run-v3/calibration.json \
  --legacy-pgn /caminho/para/Mecking-original.pgn \
  --allow-provisional \
  --out calibracoes/mequinho-v08
~~~

Se necessário, acrescente --cohort-ledger com o registro v5 original. O diretório de saída não pode existir. Ao concluir, ele contém:

- mequinho_v08_PROVISIONAL.txt: personalidade experimental;
- provisional_profile_report.json: proveniência, hashes, parâmetros, amostra de treino e avisos.

O relatório deve conservar o status PROVISIONAL_PROFILE_NOT_VALIDATED. Não abra o teste final lacrado para produzir esse perfil. O fluxo v0.8 reutiliza apenas os grupos de treino autorizados pela cadeia v5/v3.

### 6.2. Pré-visualizar e registrar o perfil

Primeiro, execute a prévia. Ela não altera a instalação:

~~~bash
python -m chesssim provisional-install --profile calibracoes/mequinho-v08/mequinho_v08_PROVISIONAL.txt
~~~

Depois de inspecionar o relatório, autorize a cópia e o registro do alias experimental:

~~~bash
python -m chesssim provisional-install --profile calibracoes/mequinho-v08/mequinho_v08_PROVISIONAL.txt --apply
~~~

O comando exige o relatório provisório no mesmo diretório do arquivo de personalidade. O alias MequinhoV08 é separado dos aliases oficiais. Antes de alterar basic.ini, é criado um backup; um perfil experimental divergente já instalado nunca é sobrescrito.

### 6.3. Executar um duelo provisório

~~~bash
python -m chesssim play-provisional fischer --depth 5 --reverse --out runs
~~~

O adversário deve ser um participante com perfil oficial disponível. O comando gera um duelo com Mequinho v0.8 e o adversário, invertendo as cores quando --reverse é informado. Também aceita --movetime ou os presets experimentais classical, rapid, blitz e bullet; não aceita o preset official para o perfil provisório.

## 7. Executar duelos com repertórios e gerar replay

play-styles permite jogar com perfis oficiais ou com mequinho-v08, usando repertórios históricos opcionais:

~~~bash
python -m chesssim play-styles mequinho-v08 fischer --white-book datasets/opening-books/mequinho-v09.json --depth 10 --rounds 1 --seed 2029 --out runs
~~~

Cada rodada é um par de partidas com cores invertidas. Portanto, --rounds 1 gera duas partidas; o padrão é duas rodadas (quatro partidas). --depth e --movetime são mutuamente exclusivos. O padrão é depth 10.

Para um repertório de jogador oficial, informe --black-book ou --white-book conforme a cor. Cada arquivo deve ter sido criado para o participante correspondente. Para o perfil mequinho-v08, o repertório deve ter sido construído com o slug mequinho.

Quando recebe repertórios, o comando usa as escolhas históricas fornecidas e desativa os livros internos do Rodent durante o duelo. Sem --white-book e --black-book, nenhum repertório histórico externo é aplicado.

O comando cria summary.json, duelo.pgn e, por padrão, replay.html em cada diretório de par. Os pares ficam agrupados em um diretório novo `style-duels-<timestamp>-<id>`, que contém `series-report.json`. Esse relatório registra os pares já concluídos, `seed_base`, seed por par, variação observada e contagens de partidas totais/distintas/duplicadas em toda a série; as sementes controlam apenas a política externa de repertório histórico quando ela está ativa. O replay é um arquivo HTML autônomo: não exige servidor, rede, CDN ou motor de xadrez.

### 7.1. Gerar replay a partir de qualquer PGN

~~~bash
python -m chesssim replay-html --pgn /caminho/para/duelo.pgn --out /caminho/para/replay.html
~~~

Abra o HTML gerado em um navegador. O visualizador permite escolher a partida, avançar e retroceder lances, ir ao início/fim, reproduzir automaticamente e ajustar a velocidade. O programa valida a leitura e a legalidade dos lances antes de gerar o arquivo.

O destino não pode existir previamente. Se houver erro de leitura ou lance ilegal, o replay não é produzido.

## 8. Avaliação posterior das partidas

A avaliação independente com Stockfish é opcional e não é executada automaticamente durante o duelo.

~~~bash
python -m chesssim analyze-quality \
  --pgn /caminho/para/duelo.pgn \
  --stockfish /caminho/para/stockfish \
  --depth 18 \
  --out reports/duelo-quality.json
~~~

O relatório registra hashes do PGN e do executável Stockfish, profundidade, posições amostradas, variantes principais e perda em centipeões. A amostragem padrão é balanceada por partida e cor; por isso, o número efetivamente analisado pode ser menor que --max-positions.

Essa métrica é um diagnóstico dependente de profundidade, não Elo, força de torneio ou prova de identidade histórica. As avaliações `analyze-games` e `analyze-quality` usam a identidade de partida baseada na posição inicial e na sequência completa de lances UCI. Metadados PGN diferentes não transformam uma repetição em uma observação nova. `--keep-duplicate-games` permite inspecionar posições repetidas para diagnóstico, mas **não** inclui as decisões duplicadas nas estimativas agregadas. Os relatórios apresentam `diversity.games_total`, `games_distinct`, `games_duplicate` e referências à primeira ocorrência.

Para estatísticas descritivas de características dos lances (deduplicadas por trajetória completa, sem apagar os jogos brutos):

~~~bash
python -m chesssim style-audit --pgn /caminho/para/duelo.pgn --out reports/duelo-style.json
~~~

Para comparar relatórios de qualidade produzidos a partir do mesmo PGN e do mesmo executável Stockfish:

~~~bash
python -m chesssim compare-quality --reports reports/quality-depth-12.json reports/quality-depth-18.json --out reports/quality-comparison.json
~~~

A comparação exige os mesmos hashes de PGN e Stockfish e alinha somente decisões coincidentes. Diferenças entre profundidades não devem ser interpretadas como validação independente de estilo.

## 9. Referência da CLI e pesquisa

A CLI possui comandos de operação, aquisição de dados, calibração, auditoria e pesquisa. Consulte a ajuda do comando antes de executar uma operação:

~~~bash
python -m chesssim --help
python -m chesssim play --help
python -m chesssim build-provisional --help
python -m chesssim analyze-quality --help
~~~

Principais famílias:

| Finalidade | Comandos |
|---|---|
| Catálogo e operação | roster, protocol, check, setup, draw, audit, play |
| Corpora | fetch-mecking, corpus-sources, corpus-fetch, corpus-build, corpus-annex-plan |
| Calibração e pesquisa | calibrate, calibrate-v4, calibrate-v5, calibrate-v6, sensitivity, peak-windows-init, peak-annotations-init, peak-audit, temporal-compare, temporal-controlled, style-signature, tournament-evidence, benchmark, final-eval |
| Perfil e repertório experimental | build-provisional, provisional-install, play-provisional, opening-build, play-styles |
| Partidas e avaliação | replay-html, analyze-games, analyze-quality, compare-quality, style-audit |

**Atenção ao teste final:** final-eval abre uma única vez o conjunto final lacrado de uma calibração v0.3. Não o execute como teste exploratório, para conferir se a instalação funciona ou para escolher parâmetros. A metodologia e as restrições estão em [Protocolo de pesquisa](RESEARCH_PROTOCOL.md).

## 10. Arquivos locais e reprodutibilidade

Os exemplos usam datasets/, calibracoes/, runs/ e reports/ como diretórios locais. Eles são ignorados pelo Git. Mantenha PGNs, executáveis, perfis experimentais, relatórios e registros de coorte fora dos commits. Só publique um recurso externo após verificar e documentar a autorização de redistribuição, a proveniência e as condições aplicáveis.

Para reproduzir uma execução, preserve pelo menos:

- o comando completo e seus parâmetros;
- os hashes de PGN, executável, perfis e livros;
- o resumo JSON produzido;
- a versão do código e o protocolo utilizado;
- os registros de coorte e de embargo exigidos pela pesquisa.

Consulte também [Arquitetura](ARCHITECTURE.md), [Protocolo de pesquisa](RESEARCH_PROTOCOL.md) e a [auditoria de dependências, fontes e distribuição](THIRD_PARTY.md).

## 11. Licença e geração de SBOM

O código próprio é licenciado sob GPL-3.0-or-later. Veja [a decisão de licença](LICENSE_DECISION.md) e a [auditoria de terceiros](THIRD_PARTY.md); isso não altera os direitos sobre PGNs, engines, perfis, livros ou redes externas.

Para obter um inventário de **pacotes Python resolvidos e realmente instalados**, execute (precisa de acesso ao índice de pacotes):

~~~bash
python scripts/generate_sbom.py --out sbom-artifacts
~~~

O diretório informado deve ser novo. O processo cria ambientes virtuais temporários para aplicação, dependências de desenvolvimento/build e ferramenta de geração. Produz `runtime.cdx.json` e `development.cdx.json` (CycloneDX JSON 1.6) e, para cada escopo, evidências de `pip list`, `pip inspect` e `pip freeze --all`; o `manifest.json` registra SHA-256 de cada arquivo, commit e quantidades verificadas.

O CI executa a geração apenas em Python 3.13 quando há PR ou push na `main`, no runner já existente. O artefato `resolved-sbom-py313` fica disponível por 7 dias no GitHub Actions; não deve ser commitado como se representasse outras máquinas. O SBOM não inventaria Rodent IV, Stockfish, seus recursos e PGNs, que devem ser tratados separadamente quando distribuídos.

## 12. Auditoria de diversidade (issue #15)

`play` e cada par de `play-styles` preservam todas as partidas, mas incluem `diversity` e `games[*].mainline_sha256` em `summary.json`. A identidade canônica deriva do FEN inicial e de todos os lances da linha principal em UCI, ignorando cabeçalhos como `Event`, `White` e `Round`. Partidas vazias são reportadas e não constituem evidência. Partidas incompletas com lances podem ser descritas estatisticamente, mas não equivalem a partidas concluídas.

`play-styles` cria ainda `series-report.json` com a agregação das **duas cores e de todos os pares**. Seis partidas produzidas por três pares podem corresponder a apenas duas trajetórias únicas — o relatório conserva ambas as contagens, marca a primeira ocorrência e exclui as repetidas do `effective_independent_games`. Em caso de erro, salva o último checkpoint da série com status `FAILED`, sem apagar os PGNs anteriores. Seeds distintas não garantem aberturas/lances distintos; quando `--white-book` e `--black-book` são omitidos, a semente é apenas agendada e não altera o repertório. A aleatoriedade de livros internos UCI e da busca do motor não é controlada pela semente externa.

**Observação:** contagem de trajetórias únicas não constitui, por si só, uma estimativa de variância, força Elo ou identidade histórica; resultados de partida dependem também da posição inicial, relógio, engine e condições de execução.
