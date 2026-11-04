# Instalação Linux/WSL e verificação UCI real

Este procedimento cria um ambiente Python separado, roda verificações locais e, com Rodent IV previamente obtido pelo usuário, produz **uma partida UCI real** e valida o PGN gerado. Não instala executáveis de Rodent IV ou Stockfish nem importa bases históricas. É aplicável a distribuições Linux e WSL que suportem a execução do motor configurado.

## Pré-requisitos

- Python 3.11 ou superior, módulo venv, pip e acesso ao índice Python para a primeira instalação.
- Bash e Git para obter o projeto.
- Para executar a partida: **Rodent IV** instalado externamente, com executável de nome rodent-iv-plain.exe, diretório personalities e arquivo personalities/basic.ini.
- No WSL, um executável Windows exige interoperabilidade WSL funcional. Sem ela, use uma compilação de Rodent compatível com Linux, preservando o nome esperado pelo código; não basta renomear outro motor.
- Para verificação de um UCI genérico, Stockfish ou outro motor instalado separadamente é opcional.

## 1. Instalar e validar Python

No terminal, a partir da raiz do repositório:

~~~bash
bash scripts/setup_linux_wsl.sh
~~~

O script usa python3 para criar .venv, instala o projeto e os extras de desenvolvimento, executa pip check e a suíte pytest, e informa que um motor externo não foi configurado quando for o caso. Sem caminho de Rodent, **não simula** um duelo nem inventa um PGN. Nenhum perfil, livro ou executável externo é alterado.

Personalize os caminhos sem modificar o código:

~~~bash
export CHESSSIM_PYTHON=python3
export CHESSSIM_VENV="$HOME/.virtualenvs/chesssim"
bash scripts/setup_linux_wsl.sh
~~~

Para repetir diagnóstico sem reinstalar nem rodar todos os testes:

~~~bash
bash scripts/setup_linux_wsl.sh --skip-install --skip-tests
~~~

O modo --skip-install exige ambiente já populado; ele não deve ser utilizado para declarar uma nova instalação como validada.

## 2. Configurar Rodent IV externo

Aponte **para a pasta-raiz**, não para o executável:

~~~bash
export RODENT_IV_HOME="/caminho/que/contem/Rodent-IV"
bash scripts/setup_linux_wsl.sh --skip-install --skip-tests
~~~

O diagnóstico exige binário executável, personalities/basic.ini e handshake UCI com opção Personality. Emite também uma jogada legal após consulta depth=1, encerrando o processo. Se não conseguir abrir o binário, fornece o caminho falho e orienta verificar permissões e interoperabilidade WSL.

Em caso de arquivo de perfil ausente, rode o comando de previsão sem aplicar mudanças:

~~~bash
.venv/bin/python -m chesssim setup --rodent "$RODENT_IV_HOME"
~~~

Para **autorizar expressamente** a instalação dos perfis oficiais que faltam, alteração do registro basic.ini com backup e, quando solicitado, o download dos livros Polyglot referenciados:

~~~bash
bash scripts/setup_linux_wsl.sh --skip-install --skip-tests --apply-profiles --with-books
~~~

Esta etapa acessa a fonte upstream fixada no programa e não instala ou atualiza o executável Rodent. A alteração só ocorre mediante a opção --apply-profiles. A execução sem essa opção não modifica perfis ou livros. Recursos externos permanecem sujeitos às suas próprias licenças.

## 3. Duelo curto real e verificação do PGN

Com motor e perfis instalados:

~~~bash
bash scripts/setup_linux_wsl.sh --skip-install --skip-tests --smoke
~~~

Ou com parâmetros explícitos:

~~~bash
bash scripts/setup_linux_wsl.sh --skip-install --skip-tests \
  --rodent "/caminho/para/Rodent-IV" \
  --white fischer --black tal --depth 1 --max-plies 12 \
  --smoke --out "runs/meu-smoke-novo"
~~~

A opção --out deve apontar para uma pasta inexistente. O verificador executa a CLI real com motor externo e confere:

- presença de jogo_1.pgn, duelo.pgn e summary.json;
- uma única partida por arquivo, sem erros na leitura PGN;
- legalidade de todos os lances e número de meios-lances igual ao reportado;
- jogadores, resultado, status e causa de término coerentes entre PGN e JSON;
- partida interrompida por max-plies identificada como **INCOMPLETE**, nunca convertida artificialmente em vitória ou empate.

Após sucesso, emite smoke_report.json com caminho dos artefatos, número de meios-lances e SHA-256 do PGN. **INCOMPLETE é esperado** para um teste curto; o smoke prova capacidade de jogar e persistir lances legais, não que o confronto tenha chegado a um resultado final ou que a simulação represente fielmente um enxadrista histórico.

## 4. UCI genérico opcional

Uma instalação independente de Stockfish pode ser usada para verificação de comunicação UCI:

~~~bash
export CHESSSIM_UCI_ENGINE=/usr/games/stockfish
bash scripts/setup_linux_wsl.sh --skip-install --skip-tests
~~~

Alternativamente, passe --uci-engine PATH. O script não exige esse motor para a partida com Rodent e não o baixa. Os testes convencionais não dependem de nenhum motor instalado.

## 5. Diagnóstico e limites

| Mensagem | Ação |
|---|---|
| Python 3.11+ required | Selecionar Python compatível via CHESSSIM_PYTHON |
| could not create venv | Em Debian/Ubuntu, instalar o pacote python3-venv da versão utilizada |
| pip installation failed | Verificar acesso à rede, índice de pacotes e ambiente virtual |
| Rodent IV path missing | Definir RODENT_IV_HOME ou --rodent |
| Rodent binary missing | Instalar Rodent IV externamente, na pasta-raiz esperada |
| Rodent personality registry missing | Apontar para a instalação que contém personalities/basic.ini |
| UCI handshake/move failed | Verificar executabilidade do motor, compatibilidade Linux/WSL e integridade da instalação |
| Rodent profile not ready | Executar setup de prévia e autorizar --apply-profiles apenas após revisão |
| Smoke output directory already exists | Escolher outra pasta --out para preservar execuções anteriores |

**Segurança:** nunca reutilizar uma instalação Rodent não confiável. Rodent e Stockfish são executáveis terceiros; um caminho configurado pelo usuário é executado pelo sistema operacional. A ferramenta não verifica a assinatura criptográfica desses binários, não instala automaticamente o Wine, não eleva privilégios e não autoriza redistribuição de motores, perfis, livros ou corpora.

**CI:** testa sintaxe do script, diagnósticos e um processo UCI **sintético**, compatível com o protocolo, para validar geração/verificação de PGN sem motor proprietário ou binário externo. Esse teste não é evidência de execução real do Rodent instalado na máquina do usuário.

Consulte [o manual da CLI](USAGE.md) e [a política de terceiros](THIRD_PARTY.md).
