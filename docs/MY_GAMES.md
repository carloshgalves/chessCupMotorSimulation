# Minhas Partidas — domínio pessoal do modo Jogar (#84)

## Contrato de produto
**Minhas Partidas** é a aba pessoal da Arena local, separada da **História dos enxadristas**, Ligas, Copas e do **Elo ChessSimulation**. Seus números W/E/D são medidos **do ponto de vista de “Você”**, com partidas incompletas excluídas desses três contadores. Oponentes UCI são identificados por slug do catálogo, mas não recebem vitória, derrota, título ou Elo de confrontos pessoais.

Nenhuma rota de Histórico/Elo usa os arquivos `human-games/`. Essa fronteira é testada em `tests/test_my_games.py`, inclusive com verificação negativa de que partidas humanas não aumentam `history_archive`, `history_stats` nem `competition_rating`.

## Fonte local e limite de evidência
- `<workspace>/human-games/human-*/state.json` e `game.pgn`, gravados por `human_game.py`; nenhum download, motor, captura de dados da Internet ou operação de recuperação acontece nas requisições GET.
- Leitura limitada a 10.000 sessões, 32 KiB por estado e 1 MiB por PGN; arquivos e diretórios abertos via descritores com `O_NOFOLLOW` e arquivos especiais lidos em modo não bloqueante. Um índice em memória invalidado por metadados de estado/PGN permite paginar sem reabrir todos os PGNs; replay e download sempre revalidam os bytes. Divergências de estado/PGN, linhas ilegais, JSON duplicado, IDs inválidos e resultados terminais não comprovados são rejeitados.
- `created_at_utc`, `updated_at_utc` e `finished_at_utc` vêm do checkpoint. Não há data exata de início dos lances nem duração observada: os campos correspondentes permanecem `null`.
- `pgn_sha256` é hash dos bytes lidos na consulta. `provenance=locally_consistent` **não** significa evidência assinada, autenticação da autoria, antiadulteração criptográfica do usuário ou licença PGN externa.
- Registros inválidos são excluídos dos cálculos com contagem `coverage.rejected` e indicação de cobertura parcial na interface; nenhuma vitória é presumida.

## API GET da Arena local
- `GET /api/my-games?page=1&page_size=20&status=all&outcome=all&color=all&opponent=` — estatísticas completas do usuário, agregação por adversário, filtros e página.
- `GET /api/my-games/<human-id>/replay` — estados FEN/SAN para replay read-only, de partidas concluídas ou incompletas legalmente registradas, mantendo `result='*'` quando não houve desfecho.
- `GET /api/my-games/<human-id>/pgn` — download dos bytes PGN locais, com validação de estado/arquivo repetida na requisição.

IDs não são caminhos. A API HTTP mantém o limite de Host/loopback e a política CSP já existente da Arena. Não existe login individual: **“usuário” é o operador do workspace local**; todos com acesso ao mesmo workspace na mesma máquina compartilham os registros. Multiusuário e controle de acesso ficam fora deste MVP.

## Fluxo visual
No topo da Arena, acessar **Minhas Partidas** → cartões com J/concluídas/V/E/D/não concluídas → tabela com filtros por adversário, status, resultado e cor. O botão **Ver partida** abre um diálogo de replay com o tabuleiro 8×8 compartilhado e controles de lance; o PGN pode ser exportado ali. Navegar entre abas não modifica arquivos, relógios ou engine em execução.

## Homologação operacional
1. Em worktree da branch #84, rodar pytest, Ruff, checagem JS e validação wheel/sdist. A CI em Python 3.11/3.12/3.13 é gate técnico; não se deve declarar PASS antes de verificar o run do HEAD.
2. Com Rodent IV real no Debian, iniciar um jogo contra Tal, executar lances, interromper/retomar, finalizar e reabrir a aplicação; conferir resultado, contagens e replay em **Minhas Partidas**.
3. Conferir que os cartões de Histórico dos motores, tabelas de Ligas e Elo mantêm exatamente seus valores anteriores e posteriores.
4. Testar Chrome com zoom 100/125/150/200%, teclado, Escape no diálogo, ausência de overflow e controles de tabuleiro/peças; guardar evidências sem fragmento token.
5. Registrar separadamente `PASS`, `FAIL`, `NOT_RUN` e `BLOCKED` em #83. Não há autorização automática de merge ou deploy.
