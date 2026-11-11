# #83 — Homologação mínima Debian 13 / Chrome / Rodent IV

**Estado da execução em 11/11/2026: `NOT_RUN`.** O dispositivo remoto não estava online. Este arquivo é um **roteiro**, não evidência de teste. Não executar automaticamente nem declarar `PASS` sem observar o navegador e os arquivos reais.

## Pré-voo não destrutivo

Na máquina Debian autorizada, em checkout atualizado e sem alterar perfis/livros:
```bash
git status --short
git rev-parse HEAD
python3 --version
google-chrome --version || chromium --version
printf 'Rodent IV em: %s\n' "${RODENT_IV_HOME:-NÃO CONFIGURADO}"
python3 -m chesssim --help
```
Registre a revisão exata do Git e a versão do motor verificada pelo UCI. Se Rodent IV não estiver configurado, marque `BLOCKED`, não instale/substitua o executável automaticamente. Evite salvar paths pessoais ou tokens nos relatórios.

## Smoke de inicialização sem mutações ocultas

1. Execute a verificação UCI reproduzível descrita em [SETUP_LINUX_WSL.md](SETUP_LINUX_WSL.md), somente com o motor já autorizado e instalado.
2. Crie uma **pasta de execução nova**, fora do checkout, com `python -m chesssim arena --out CAMINHO_NOVO --port 8766`. Não utilize pastas existentes e não remova volumes/PGNs anteriores.
3. Abra o endereço **loopback informado pelo comando** no Chrome. Se a URL contiver `#token`, **não fotografe, cole em issues nem registre o fragmento**.
4. Crie uma sessão **Jogar**, com Rodent IV nativo, controle curto, um único perfil legal, sem importação/download nem instalação de livro/perfil. Registre quem joga de brancas e pretas.
5. Durante o turno do motor, arme um pré-move legal (um único), confirme marcação vermelha, revise o estado da intenção durante atualização de lance, e verifique que o POST ao backend ocorre **apenas na vez humana** e no máximo uma vez. Repita com lance que fica ilegal, clique direito/Escape, promoção/subpromoção e troca de sessão. Não atribua diferença de relógio fictícia.
6. Abra o modo espectador em `/viewer` e, em outra execução observável, `watch`. Ative **Adivinhar o próximo lance**; verifique palpite azul, previsão local, resultado somente após revelação visual, deduplicação por polling e ausência de POST para jogos de motor.
7. Em DevTools/Network, verifique `/api/events/v2`, `/api/state` autoritativo e fallback para polling após desconexão do SSE. Recarregue o visor e alterne uma partida; confirme que isso não interrompe o motor nem reescreve PGNs.
8. Finalize pela interface/comando documentado; confira os PGNs com `python-chess` e o resumo/recibos. Não exclua tentativas incompletas; registre seus motivos. Repita invertendo as cores quando autorizado.

## Matriz de evidência

Preencha **cada** linha com `PASS|FAIL|BLOCKED|NOT_RUN`, sem presumir sucesso por CI.

| Cenário | Resultado inicial | Evidência esperada |
| --- | --- | --- |
| Handshake e jogada real UCI | NOT_RUN | log redigido do teste de motor + versão |
| Jogar humano contra motor | NOT_RUN | PGN e resumo íntegros, hash, revisão |
| Pré-move: legal, ilegal, cancelamento, promoção | NOT_RUN | captura/observação da UI e requisições sem token |
| Espectador: acerto/erro/replay e ausência de POST | NOT_RUN | Network/filmagem redigida e PGN inalterado |
| SSE v2 / reconexão / fallback | NOT_RUN | observação de GETs e estado autoritativo |
| Encerramento e ausência de processos órfãos | NOT_RUN | processos UCI antes/depois, sem PID pessoal |

**Metadados obrigatórios:** data, commit `main` ensaiado, Debian/kernel, Chrome/Chromium, versão e hash do motor, perfil, controle de tempo, resultado, hash dos arquivos e referências de evidência sanitizada. Não inclua capturas contendo token, endereço privado, nome de usuário, perfil de sistema ou PGNs com restrição de distribuição.

Uma falha real abre issue própria com passos reprodutíveis; PRs já mescladas não são desfeitas por inexistência de homologação. As quotas rígidas de CPU/RAM, `SIGKILL`, durabilidade `fsync`, partidas 64/4.032 e direitos dos retratos **não** são aprovados por este smoke. Continuam em #27, #83 e #47/#53.

**Critério de encerramento #83:** matriz completada e verificada por evidência de execução no hardware do operador. Este roteiro, sozinho, nunca encerra #83.
