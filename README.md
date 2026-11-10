# ChessCup Motor Simulation

Motor local de simulação de partidas e torneios entre personalidades enxadrísticas. A aplicação utiliza perfis de motores UCI, repertórios derivados de partidas históricas e ferramentas de avaliação independente. O objetivo é estudar **qualidade de decisões e semelhança de estilo** como propriedades distintas.

## Estado do projeto

O pacote Python/CLI, a Arena web local, o monitor `watch`, os modos **Duelo/Jogar (humano × UCI)**, Copas, Ligas, História dos 64 enxadristas e o **Elo interno experimental** estão implementados em `main`. A análise independente com Stockfish e o laboratório de representação histórica têm código e testes. O perfil experimental de Henrique Mecking e os demais perfis **não** têm validação definitiva de estilo/força histórica; `fast_statistical` ainda é um demonstrador **não calibrado** (#61), não previsão certificada. O Histórico dos **motores** inclui Arena/torneios/Copas/Ligas e **exclui deliberadamente partidas humanas**. O modo Jogar terá a área independente **Minhas Partidas** (#84, implementação em PR #86), com estatísticas e replay particulares, sem efeito no Elo ou nas estatísticas dos motores. A avaliação visual avançada #24, a importação legal ampliada #62 e a certificação experimental dos 64 continuam abertas. CI automatizada não equivale à homologação física com Rodent/Stockfish/Chrome (#83) nem ao gate de acessibilidade e direitos dos 16 retratos marcados PDM (#53).

## Requisitos

- Python 3.11 ou superior e ambiente virtual.
- `python-chess` (distribuição `chess` no PyPI; GPL-3.0-or-later), dependência obrigatória de runtime instalada pelo pacote.
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


### Setup Linux/WSL com smoke test real

O instalador em Bash cria/reutiliza uma venv, valida dependências e executa os testes. Rodent IV e Stockfish devem ser obtidos separadamente; o script não distribui nem baixa executáveis:

~~~bash
bash scripts/setup_linux_wsl.sh
export RODENT_IV_HOME="/caminho/para/Rodent-IV"
bash scripts/setup_linux_wsl.sh --skip-install --skip-tests --apply-profiles --smoke
~~~

A instalação de perfis só ocorre com `--apply-profiles`. O segundo comando verifica um motor UCI real e gera um PGN curto de teste, com JSON auditável; partidas encerradas por limite de lances ficam corretamente como **INCOMPLETE**. Consulte [instalação e diagnóstico Linux/WSL](docs/SETUP_LINUX_WSL.md) para detalhes e erros acionáveis.

## Manual de execução

O [manual de uso](docs/USAGE.md) reúne os comandos da CLI e exemplos reproduzíveis para configurar o Rodent IV, obter e preparar corpora PGN, construir repertórios, gerar duelos, usar o perfil provisório e abrir replays offline. Os exemplos usam caminhos genéricos; executáveis e corpora externos precisam ser instalados/obtidos separadamente.

## Testes e validação de motores

A suíte padrão não exige Rodent IV nem Stockfish e pode ser executada em uma máquina limpa:

```bash
python -m pytest -q -ra
python -m compileall -q src tests
python -m ruff check src tests
```

O teste de integração UCI real é separado e identificado pelo marcador `requires_engine`. Para executá-lo, instale um motor UCI e informe seu executável:

```bash
CHESSSIM_UCI_ENGINE=/usr/games/stockfish python -m pytest -m requires_engine -q
```

O mesmo teste pode verificar outro motor compatível com UCI, desde que esteja instalado e acessível no ambiente. A integração não instala nem redistribui motores automaticamente. O CI executa a suíte sem motor externo; nesse cenário, o smoke test UCI é reportado como ignorado com justificativa explícita.

## Fluxo de uso

1. Prepare um corpus PGN local e verifique seus metadados, proveniência e partições.
2. Construa o repertório de aberturas por posição com `opening-build`.
3. Prepare o perfil experimental com `build-provisional` e registre-o com `provisional-install` quando apropriado.
4. Execute confrontos por `play-styles` ou `play-provisional`; os PGNs e relatórios permanecem locais.
5. Analise partidas com `analyze-quality` (Stockfish), compare relatórios com `compare-quality` e gere estatísticas descritivas com `style-audit`.

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

O **código próprio** é distribuído sob a **GNU GPL versão 3 ou posterior** (`GPL-3.0-or-later`): veja [LICENSE](LICENSE), [aviso de direitos](NOTICE.md) e [comparativo/justificativa da licença](docs/LICENSE_DECISION.md). O uso da aplicação não tem taxa de licença, mas a distribuição de cópias precisa respeitar as obrigações da GPL e das licenças dos componentes envolvidos.

O projeto não incorpora executáveis de engines nem corpora PGN de terceiros. Rodent IV, Stockfish, perfis, livros e bases históricas devem ser obtidos separadamente e utilizados de acordo com as respectivas licenças e condições. A auditoria das dependências, fontes e regras de distribuição está em [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md).

## SBOM das dependências resolvidas

Para auditar o que foi **efetivamente instalado**, use `python scripts/generate_sbom.py --out sbom-artifacts`. O script cria ambientes virtuais isolados e gera dois inventários CycloneDX JSON 1.6: `runtime.cdx.json` (aplicação) e `development.cdx.json` (aplicação, ferramentas de desenvolvimento e backend de build). Também conserva as listas de versões resolvidas, `pip inspect` e um manifesto com hashes SHA-256.

Na integração contínua, esse processo roda **somente na matriz Python 3.13**, em pull requests e pushes na `main`, sem contratar serviços pagos ou criar outro runner. Os arquivos ficam disponíveis por **7 dias** no artefato da execução do GitHub Actions. O inventário é específico da plataforma/versão Python e não inclui executáveis UCI, livros, corpora PGN ou bibliotecas do sistema operacional. Consulte [a política de distribuição](docs/THIRD_PARTY.md).
