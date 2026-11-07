# Tabuleiro 2D configurável e estudo de visualização 3D — Issue #68

## Entrega 2D — contrato funcional

As telas **Arena / História** e **watch** exibem o painel `Aparência do tabuleiro`. Todas usam o renderer canônico `chess_board.js` e a folha `chess_board.css`; o editor de preferências vive em `board_preferences.js`. **Nenhum código desta entrega altera lance, PGN, FEN, motor, relógios, sessão, token da Arena, resultado ou rating.**

| Campo | Valores permitidos | Padrão |
| --- | --- | --- |
| Cores | `classic` (clássico), `wood` (madeira), `slate` (azul-acizentado), `contrast` (alto contraste) | `classic` |
| Peças | `solid` (silhueta com preenchimento e borda), `outline` (contorno) | `solid` |
| Movimento | `off`, `fast` (160 ms), `normal` (330 ms) | `off` |

A animação é apenas o breve realce das casas do último movimento; **não há interpolação física nem movimentação da peça**. Ela ocorre somente em mudanças de FEN efetivamente exibida. Em polling da mesma posição, o destaque não se repete. A regra do sistema `prefers-reduced-motion: reduce` prevalece e desativa a animação, mesmo quando o usuário escolheu `fast` ou `normal`.

O tema altera somente as cores de 64 casas e os contrastes das silhuetas; não afeta as cores de formulários, biografias, retratos ou gráficos. A geometria 8×8, os rótulos de casas e a orientação existente permanecem inalterados.

**Desenho das peças e direitos:** as duas variantes se baseiam nos glifos Unicode de xadrez já existentes e nas fontes que o sistema operacional do usuário disponibiliza (DejaVu Sans / Noto Sans Symbols 2 / Segoe UI Symbol). Nenhuma fonte, imagem vetorial de terceiros ou biblioteca de peças foi incorporada ao repositório. O modo `outline` ajusta o traço CSS da mesma silhueta; não é uma nova coleção de ilustrações. Se futuramente for incorporada uma biblioteca de peças real, exigirá auditoria explícita da licença/atribuição e testes de fallback.

## Persistência, privacidade e integração

A chave `chesssim.board-presentation.v1` usa exclusivamente `window.localStorage` da origem local. O valor é JSON contendo somente `theme`, `pieces`, `motion`; campos adicionais ou valores fora de allowlists são ignorados. JSON inválido, excessivamente longo ou armazenamento indisponível não bloqueiam o programa: aplica-se o padrão ou a alteração somente na página.

- Na **Arena**, as preferências aplicam-se imediatamente ao tabuleiro de replay do perfil histórico e à tela de watch embutida, que recebe o evento de sincronização `storage` da mesma origem.
- No **watch independente**, as mesmas configurações podem ser editadas. Cada origem (protocolo + host + porta) mantém suas preferências próprias, por isolamento normal do navegador.
- Nunca colocar preferências em query string, URL fragment, cookie de autenticação, API HTTP, PGN, JSON de competição ou comandos UCI.
- **Restaurar padrão** grava as três opções iniciais; não reinicia partida.
- O iframe do monitor embutido não deve apresentar um segundo editor redundante. Continua lendo as preferências da origem e recebendo eventos `storage` enquanto a Arena apresenta o único editor visível.

## Evidência e controle de qualidade

`tests/assets/board_preferences_browser_harness.cjs` testa armazenamento válido/inválido, allowlists, reset, fallback em storage bloqueado, sincronização de contextos, 64 casas e atualização de tema sem reconstruir a posição. O teste `tests/test_board_preferences.py` verifica publicação do asset no wheel, GET-only, CSP e consistência com os arquivos do renderer e dos formulários. Os testes existentes da Arena, watch e replay também devem passar em Python 3.11/3.12/3.13.

**Limitação honesta:** CI e DOM simulado não certificam visualmente legibilidade de glifos nas fontes específicas do Debian, telas de alto DPI ou testes touch. Verificar manualmente Chrome/Debian com viewport 1440×900, 390×844 e 320×568, foco, estilos, tabuleiro real e `prefers-reduced-motion`. Não marcar essa etapa como executada sem screenshot ou evidência funcional.

## Estudo técnico — modo 3D opcional (sem implementação nesta issue)

**Decisão: NO-GO de implementação 3D nesta fase.** A issue exige avaliação, não autorização implícita para substituir o tabuleiro atual. O 3D deverá consumir exatamente a mesma FEN legal do backend e apresentar fallback 2D para contextos sem GPU, WebGL desabilitado ou preferência de acessibilidade. Não criar outro motor de regras.

| Estratégia | Benefício | Riscos, custo ou limite |
| --- | --- | --- |
| CSS 3D / perspectiva em DOM | Poucas dependências; reutiliza os mesmos elementos de posição | Não é 3D geométrico completo; sobreposição, hit testing e leitores de tela exigem cuidado |
| WebGL com geometria simplificada | Modelo de câmera, iluminação e peças tridimensionais reais | GPU/driver/consumo de memória, bibliotecas e assets a licenciar; fallback essencial |
| Engine 3D externa | Efeitos sofisticados e câmera física | Aumenta tamanho, manutenção, superfície de ataque e custo de distribuição; desproporcional ao MVP |

Antes de aprovar qualquer protótipo 3D, exigir: (1) análise de licença para peças, tabuleiro e dependências; (2) medição real de FPS, latência, RAM, GPU e pacote no Debian sem aceleração e com aceleração; (3) funcionamento offline, sem telemetria e sem CDN; (4) geometria e orientação corretas para 64 casas; (5) navegação por teclado, rótulos acessíveis e fallback sem gráfico; (6) prova de que FEN, estado, relógios, PGN e replay usam o núcleo já validado. A proposta pode ser isolada em issue futura após esse gate.

**Sem benchmarks reais do notebook, não afirmar números de desempenho ou compatibilidade 3D.**
