# ChessCup Motor Simulation

Motor local de simulação de partidas e torneios entre personalidades enxadrísticas. A aplicação utiliza perfis de motores UCI, repertórios derivados de partidas históricas e ferramentas de avaliação independente. O objetivo é estudar **qualidade de decisões e semelhança de estilo** como propriedades distintas.

## Estado do projeto

Há um protótipo de linha de comando que gera duelos, PGNs e replays offline, constrói repertórios a partir de corpus PGN e executa avaliações independentes com Stockfish. O perfil experimental de Henrique Mecking não representa uma validação definitiva de estilo ou força. A interface web, a organização completa de torneios e a validação do conjunto de 64 personalidades estão em desenvolvimento.

## Requisitos

- Python 3.11 ou superior e ambiente virtual.
- Rodent IV instalado **separadamente**, com executável e perfis autorizados pelo usuário.
- Stockfish instalado **separadamente**, caso deseje analisar qualidade dos lances.
- PGNs históricos obtidos legalmente e armazenados localmente. Os corpora não são distribuídos neste repositório.

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
python3 -m pytest -q
```

No Ubuntu/WSL, o motor Stockfish costuma ser instalado com `sudo apt install stockfish` e executável em `/usr/games/stockfish`. Configure `RODENT_IV_HOME` para o diretório do seu Rodent IV:

```bash
export RODENT_IV_HOME="/caminho/para/Rodent-IV"
python3 -m chesssim --help
```

## Fluxo de uso

1. Prepare um corpus PGN local e verifique seus metadados, proveniência e partições.
2. Construa o repertório de aberturas por posição com `opening-build`.
3. Prepare o perfil experimental com `build-provisional` e registre-o com `provisional-install` quando apropriado.
4. Execute confrontos por `play-styles` ou `play-provisional`; os PGNs e relatórios permanecem locais.
5. Analise partidas com `analyze-games-v091` (Stockfish), compare relatórios com `compare-quality` e gere estatísticas descritivas com `style-audit`.

Consulte `python3 -m chesssim <comando> --help` para argumentos específicos.

## Regras de pesquisa

- Dados de treino, validação e teste final devem ser separados por partida. Um teste final reservado não pode orientar seleção de modelos, repertórios ou parâmetros.
- Repetições da mesma partida não constituem observações independentes.
- Decisões em posições equivalentes fornecem evidência mais robusta de diferenciação de estilo que estatísticas brutas de partidas distintas.
- Qualidade avaliada pelo Stockfish, Elo, força de motor, resultado de partida e fidelidade histórica **não são sinônimos**.
- Resultados de calibração e relatórios precisam registrar fonte, data, hash, parâmetros, engine e condições de execução.

## Planejamento

As frentes de trabalho, critérios de aceite e dependências são acompanhados nas [issues](https://github.com/carloshgalves/chessCupMotorSimulation/issues).

## Direitos e distribuição

O código deste repositório não incorpora executáveis de motores externos nem bases PGN de terceiros. Antes de redistribuir qualquer recurso adicional, verifique sua licença e proveniência. A escolha de uma licença para este código está pendente de decisão de manutenção.
