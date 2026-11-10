> **Revisão administrativa — 09/11/2026:** código e CI das PRs #75, #81 e #82 foram integrados; **não** há evidência nova para converter verificações manuais `NOT_RUN` em `PASS`. Os 64 retratos estão presentes; análise territorial das 16 marcações PDM continua `BLOCKED`. Gate #53 permanece aberto, e QA dos modos/engines fora do escopo de História fica em #83.

# Gate #53 — História: acessibilidade, segurança, direitos e homologação

**Escopo:** `docs/HISTORY_UX_ARCHITECTURE.md`, catálogo e retratos #47, carrossel #48, perfil #49, API de partidas #50, replay #51 e estatísticas #52.

**Regra de liberação:** uma matriz verde de testes automatizados **não substitui** a validação manual em Chrome no Debian nem a análise jurídica dos arquivos. Separar `PASS`, `FAIL`, `NOT_RUN` e `BLOCKED`, sem atribuir `PASS` por ausência de evidência.

## Matriz de verificação

| Verificação | Evidência automatizada | Estado manual em 06/11/2026 |
| --- | --- | --- |
| 64 jogadores, ordem, navegação circular e retomada de foco ao selecionar pelo retrato lateral | `tests/assets/history_browser_harness.cjs` e `tests/test_history_gate.py` | Chrome/Debian: **NOT_RUN** |
| Textos e links seguros, créditos visíveis, biografia não apresentada como declaração real | DOM simulado e validação dos JSONs | Revisão de leitor de tela (Orca/NVDA): **NOT_RUN** |
| Replay com 0/1/4/5 jogos, troca rápida, teclado e FEN inválida | `tests/assets/history_replay_browser_harness.cjs` | Acompanhamento visual de partidas reais: **NOT_RUN** |
| Estatísticas: vazio, parcial, fonte, concorrência, adversários e não mistura com carreira | `tests/assets/history_stats_browser_harness.cjs` e testes Python | Conferência visual de dados reais: **NOT_RUN** |
| Contraste de texto/CTA WCAG AA (4,5:1), alvo 44 px e `reduced-motion` | `tests/test_history_gate.py` (tokens CSS definidos, não pixels renderizados) | 1440×900 / 390×844 / 320×568: **NOT_RUN** |
| HTTP GET-only, Host local, CSP, ausência de token e de bibliotecas remotas | `tests/test_history_gate.py` e `tests/test_arena.py` | Pentest com navegador real: **NOT_RUN** |
| Regressão Arena, `watch` e tabuleiro 8×8 alto contraste | `tests/test_arena.py`, `tests/test_live_board.py`, `tests/test_history_replay_ui.py` | Duelo UCI real no Debian: **NOT_RUN** |
| 64 JPEGs locais, hashes, fonte, crédito e licença/status declarado | `tests/test_history_gate.py` e `tests/test_history_profiles.py` | Parecer de direitos territorial: **BLOCKED** |

O requisito automatizado de contraste se limita aos pares de cores declarados e às regras CSS explicitamente testadas; não é auditoria completa de pixels, zoom, reflow, componentes nativos ou WCAG 2.2. A captura de tela e a análise de leitores de tela não foram executadas por este CI.

## Melhorias de acessibilidade e achados resolvidos

1. O CTA `Ver perfil histórico` utilizava texto branco em `#4d856f`, aproximadamente **4,29:1**, abaixo de 4,5:1 para texto normal. Foi alterado para `#3f705b` (aproximadamente **5,71:1**).
2. A seleção de um retrato lateral destruía o botão que tinha foco; após a mudança, o foco retorna ao CTA estável do jogador selecionado.
3. A captura de setas na aba História interferia com foco em links/controles internos. Agora atua somente no contêiner/carrossel e nos controles de navegação previstos, sem capturar atalhos com Alt/Ctrl/Meta.
4. O elenco incompleto/duplicado era filtrado silenciosamente, podendo aparentar carrossel completo. Agora são exigidos exatamente 64 slugs canônicos únicos; falhas mostram estado de erro sem inventar jogadores.
5. A criação de links externos agora recusa URLs HTTPS malformadas ou com credenciais na autoridade; os dados continuam inseridos como texto via DOM.
6. A interface não rotula `Public Domain Mark` como licença. Exibe explicitamente uma **declaração de status**, sem equiparar PDM a CC0.
7. Alvos do carrossel são dimensionados para pelo menos **44×44 px**, com contorno de foco dentro da aba; `prefers-reduced-motion` preservado.

## Ameaças, superfícies e mitigação

| Ameaça | Mecanismo de contenção / verificação | Limite residual |
| --- | --- | --- |
| Host malicioso, DNS rebinding ou execução remota do motor | Servidor ligado a loopback; `Host` estritamente local; endpoints de execução protegidos por token+Origin | Requer teste de navegador/ambiente real |
| Escrita ou lançamento por API de História | Leitura em `GET`; `POST` sem autorização não permite iniciar sessão; assets same-origin | Regras existentes da Arena continuam parte do gate |
| Path traversal, ID forjado ou symlink | Slugs/IDs validados; leitura local por helpers seguros, limites de arquivos, testes negativos | Pacotes/corpora externos ficam fora deste gate |
| XSS em biografia, fonte, crédito ou partida | `textContent`, `createElement`, HTTPS sem credenciais, ausência de `innerHTML`; CSP `script-src 'self'` | Links HTTPS externos ainda podem encaminhar para outra página; abrir é escolha do usuário |
| Exfiltração do token da Arena | Token apenas no fragmento local; `Referrer-Policy: no-referrer`; respostas da História não contêm token | Extensões do navegador e máquina comprometida são fora do modelo |
| PGN adulterado / estatística não comprovada | Replays e contadores a partir de PGN local validado, hashes, ledger e estado; nenhuma vitória presumida | Não prova correspondência com xadrez histórico real |
| Ataque via dependências de UI | Nenhum CDN/script remoto na página, CSS/JS servidos pelo mesmo processo | Dependências Python e imagem requerem auditoria contínua |
| Regressão visual de Arena/`watch` | Componentes compartilhados preservam geometria, paleta e atributos de peças | Sem screenshots reais nem teste UCI da máquina neste ciclo |

**Observação de produtos externos:** não reproduzir arte, interface característica, marcas ou identidade de Play Magnus. O projeto utiliza fotos documentais independentes, cores próprias e atribuições aos autores; nenhuma afiliação é insinuada.

## Direitos de imagem — status obrigatório de publicação

O inventário atualmente contém **64 imagens**: 28 CC0 1.0, 3 CC BY-SA 3.0, 15 CC BY-SA 2.0, 1 CC BY 2.0, 1 CC BY-SA 4.0 e **16 Public Domain Mark 1.0**. Os 64 arquivos têm URL de origem, identificação de autoria/acervo, link de licença ou status, indicação de recorte e hash conferido automaticamente.

**Pendência jurídica:** PDM não é licença nem prova domínio público global. O uso de cada uma das 16 imagens marcadas PDM precisa de análise dos direitos aplicáveis no Brasil e da finalidade de redistribuição, sobretudo comercial. O repositório já distribui esses arquivos publicamente; isso não equivale a aprovação jurídica, e a pendência precisa ser avaliada também para a distribuição atual. CC BY-SA impõe atribuição, informação de modificação e obrigações sobre os derivados. Direitos de personalidade e de marca são análise separada. Nenhuma fonte foi novamente consultada online neste gate; o teste de hash confirma identidade local, não licenciamento vigente da página remota.

Referências: `docs/PHOTO_LICENSES.md` e `docs/HISTORY_PORTRAIT_BACKLOG.md`.

## Roteiro de homologação humana (preencher com evidência)

Usar a **branch da PR do gate**, sem mesclar, no navegador Chrome/Chromium do notebook Debian. Registrar versão do navegador, largura/altura da viewport, escala (100% e 200%), data, commit SHA, diretório de dados e quem validou. Não compartilhar o fragmento `#token` em screenshots públicos.

- **Desktop 1440×900:** abrir Arena, inspecionar formulário sem alterar campos; mudar para História; percorrer primeiro, último e retorno do 64º ao 1º; abrir perfil, conferir créditos, replay e estatísticas; voltar e retomar formulário/duelo ativo.
- **Mobile 390×844 e 320×568:** em DevTools/visualização responsiva, verificar ausência de scroll horizontal, sobreposição, truncamento, controles de 44 px, retratos e créditos legíveis; não confundir emulação com teste touch real.
- **Teclado:** Tab/Shift+Tab, Enter/Espaço, setas apenas no carrossel, Home/End e slider no replay; foco nunca fica em botão removido ou painel oculto. Verificar leitura sequencial da página e anúncios de posição/erros.
- **Movimento reduzido:** ativar preferência do sistema/emulação `prefers-reduced-motion: reduce`, verificar ausência de transições/rolagens animadas na História.
- **Sem partidas e com dados reais:** verificar 0/1/4/5 cards e dados de competições; partidas incompletas sem resultado fictício; confrontos/estatísticas conciliados com os PGNs.
- **Isolamento:** manter duelo em execução enquanto consulta História, verificar que relógio, resultado, token e monitor/`watch` não mudam. Testar perda e retorno de conectividade local sem alterar o duelo.
- **Fotos e direitos:** em amostra representativa CC0, BY-SA e PDM, conferir `alt`, crédito, licença/status, origem e recorte; não aprovar uso comercial antes da revisão jurídica.

Arquivar screenshots, logs e checklist **fora da árvore de dados do usuário** e sem segredos. Somente após essa etapa atualizar o estado manual na issue #53; **não marcar a issue como concluída por CI verde**.

## Gate de CI e critérios de encerramento

O workflow existente executa Python 3.11/3.12/3.13, Ruff, pytest e valida wheel/sdist. Testes opcionais de motores/corpora externos podem permanecer `SKIPPED`, registrados como limitação; não devem ser apresentados como evidência de funcionamento com Rodent IV real.

**Decisão padrão:** CI verde e revisão de código permitem enviar a PR para homologação, mas **não liberam o fechamento da #53**. Ainda são necessários registro manual Chrome/Debian e decisão explícita sobre as 16 imagens PDM.
