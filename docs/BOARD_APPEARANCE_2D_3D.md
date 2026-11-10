# Tabuleiro 2D configurável e estudo 3D — Issue #68

## Correção visual após homologação da PR #76

O usuário validou a mecânica mas identificou **falta de diversidade cromática e
silhuetas inadequadas de peças** no uso exclusivo de Unicode. Este checkpoint
substitui as opções visuais de peças por **sete conjuntos SVG originais
distribuídos no Lichess, com atribuição e licenças individuais**, e amplia
os tabuleiros para **12 estilos**. O código mantém o núcleo de regras intacto.

### Catálogo de tabuleiros

| ID | Tema | Aparência |
| --- | --- | --- |
| `classic` | Clássico | Marfim e verde-acinzentado original |
| `wood` | Madeira clara | Grãos discretos em areia e caramelo |
| `slate` | Ardósia | Cinza mineral e azul-acinzentado com microtextura |
| `light` | Claro | Marfim suave e verde-pálido, sem textura |
| `dark` | Escuro | Grafite azulado e aço |
| `contrast` | Alto contraste | Branco-gelo e quase preto |
| `green` | Verde tradicional | Creme e verde vivo |
| `blue` | Azul torneio | Azul-claro e azul profundo |
| `walnut` | Nogueira | Marfim quente e marrom-escuro com grãos |
| `marble` | Mármore | Branco mineral e pedra cinza com veios |
| `lavender` | Lavanda | Lilás-claro e roxo |
| `terracotta` | Terracota | Areia-clara e barro queimado com textura |

A miniatura 2×2 do seletor mostra cada paleta antes da aplicação. Temas
de madeira, pedra e terracota recebem **texturas CSS próprias**, sem
download de imagens de terceiros ou CDN.

### Sete conjuntos reais de peças (todos com 12 SVGs)

| ID | Nome | Autor | Licença |
| --- | --- | --- | --- |
| `cburnett` | Cburnett (padrão) | Colin M. L. Burnett | GPL-2.0-or-later |
| `merida` | Merida | Armando Hernandez Marroquin | GPL-2.0-or-later |
| `chessnut` | Chessnut | Alexis Luengas | Apache-2.0 |
| `fantasy` | Fantasy | Maurizio Monge | MIT |
| `spatial` | Spatial | Maurizio Monge | MIT |
| `celtic` | Celtic | Maurizio Monge | MIT |
| `rhosgfx` | RhosGFX | RhosGFX | CC0-1.0 |

**Fonte auditável:** [Lichess/lila — COPYING.md](https://github.com/lichess-org/lila/blob/104a754b35740129fade423f920ad6de965031dd/COPYING.md),
commit `104a754b35740129fade423f920ad6de965031dd`.
O manifesto `src/chesssim/data/pieces/SOURCES.json` armazena
o SHA-1 Git de cada SVG; todos os originais são empacotados sem modificar.
Os textos de licença acompanham `pieces/licenses/` no wheel.

Os nomes dos conjuntos identificam os autores/obras livres; não há
reprodução de assets proprietários do Chess.com nem afiliação ou
endosso do Lichess. As marcas não são usadas como componentes da interface.

Peças SVG são carregadas de uma rota local com caminhos estritamente
allowlisted. Caso um SVG falhe ao carregar, o glifo Unicode permanece
como **fallback**, sem tornar a casa vazia nem afetar o lance. A variante
branca/negra é explicitamente escolhida a partir da FEN; rótulos de
acessibilidade continuam em português.

### Persistência e animação

- Chave browser-local `chesssim.board-presentation.v1`; atributos
  `theme`, `pieces` e `motion` somente. Inputs fora de allowlist,
  JSON inválido e armazenamento bloqueado não executam código ou
  alteram estado de jogo.
- Padrões: `classic` + `cburnett` + `off`. Preferências legadas
  `solid` e `outline` migram respectivamente para `cburnett`
  e `spatial` para evitar o glifo inadequado observado em homologação.
- `motion`: `off`, `fast` (160 ms), `normal` (330 ms).
  Realce de último lance somente quando a FEN exibida muda; o polling
  da mesma posição não dispara repetidamente. CSS
  `prefers-reduced-motion:reduce` tem precedência.
- **Todos os tabuleiros da mesma origem** compartilham as preferências
  selecionadas em **Arena → Aparência**: Arena/visualizador `board`,
  modo Jogar `human-board`, replay de Copas `cup-replay-board`,
  replay histórico `history-replay-board`, arquivo e estatísticas dos
  enxadristas `archive-replay-board` e histórico pessoal
  `my-games-board`. O `watch` isolado reutiliza `board`.
  O inventário de `div.board` em `arena.html` e `monitor.html`
  é validado em teste de regressão; novos tabuleiros não podem ficar
  fora da aplicação de tema, SVG ou destaque.
- Uma mudança de conjunto de peças atualiza os `img` existentes sem
  reconstruir casas, perder realce, mudar posição ou tocar o motor.
  O visualizador embutido recebe sincronização por `storage` e
  não exibe um segundo editor redundante. Como as preferências são
  armazenadas em `localStorage`, **portas/origens diferentes** não
  compartilham a chave automaticamente; a preferência não é armazenada
  nos arquivos PGN nem na configuração dos motores.
- Fontes completas de licenças e atribuições: `NOTICE.md`.
  A GPL do código não suprime termos específicos da arte.

### Correção de ergonomia após a segunda homologação

A validação identificou um problema de composição: manter as **12 amostras
abertas ao lado das 7 amostras de peças** aumentava muito a altura do
cabeçalho, empurrando o tabuleiro para fora da janela. Além disso, as
prévias 2×2 tinham altura fixa de 38 px, distorcendo a proporção dos
quadrados. As correções da mesma PR #76 são:

- O controle inicial **Aparência** é compacto. Ao abri-lo, apresenta apenas
  três botões: **Tabuleiro**, **Peças** e **Animação**, inicialmente sem
  galeria expandida.
- Ao clicar em uma categoria, apenas ela abre; a anterior fecha.
  Repetir o clique recolhe a categoria. `aria-expanded`,
  `aria-controls` e `hidden` são sincronizados; a tecla Escape
  encerra o popover e devolve o foco ao resumo.
- O menu ampliado fica flutuando sobre a área de controles à esquerda,
  com altura limitada e rolagem interna. **Não altera o tamanho do
  cabeçalho**, mantendo a Arena e seu tabuleiro visíveis à direita.
- Cada amostra de tabuleiro é um **quadrado 1:1**, com a paleta em quatro
  casas quadradas e o nome abaixo. Em telas estreitas, a galeria se
  adapta de quatro para três colunas.
- As preferências gravadas no navegador não são alteradas ao
  abrir, recolher ou trocar categorias. Arena, replay histórico e
  visualizador independente continuam compartilhando a aplicação visual.

Revalidar visualmente a posição do popover na Arena e no `watch`,
o alinhamento das 12 amostras e as transições entre as três categorias
em diferentes larguras; esse aspecto **não é certificado pelo DOM simulado**.

### Gates e limitações

A revisão de código exige: 84 SVGs íntegros no wheel; endpoint GET
com allowlist, `Content-Type: image/svg+xml`, `nosniff` e CSP
same-origin; ausência de scripts/links ativos nos SVGs; render
das 64 casas e das 12 peças com carregamento e fallback; persistência,
reset, sincronização e migração; invariância de motores, PGN, relógios
e FEN. Matriz de Python 3.11/3.12/3.13 com testes de frontend Node.

**Não afirmar homologação visual por testes DOM.** Em Chrome/Debian, validar
rei, dama, torre, bispo, cavalo e peão para ambas as cores em todos os
sete conjuntos, a 100% e 200% de zoom, temas claros/escuros, 390×844,
320×568 e modo de movimento reduzido. Verificar ausência de scroll
horizontal, bordas cortadas e foco perdido. A decisão de merge continua humana.

## Estudo técnico — modo 3D opcional (sem implementação nesta issue)

**Decisão: NO-GO de implementação 3D nesta fase.** A issue exige avaliação, não autorização implícita para substituir o tabuleiro atual. O 3D deverá consumir exatamente a mesma FEN legal do backend e apresentar fallback 2D para contextos sem GPU, WebGL desabilitado ou preferência de acessibilidade. Não criar outro motor de regras.

| Estratégia | Benefício | Riscos, custo ou limite |
| --- | --- | --- |
| CSS 3D / perspectiva em DOM | Poucas dependências; reutiliza os mesmos elementos de posição | Não é 3D geométrico completo; sobreposição, hit testing e leitores de tela exigem cuidado |
| WebGL com geometria simplificada | Modelo de câmera, iluminação e peças tridimensionais reais | GPU/driver/consumo de memória, bibliotecas e assets a licenciar; fallback essencial |
| Engine 3D externa | Efeitos sofisticados e câmera física | Aumenta tamanho, manutenção, superfície de ataque e custo de distribuição; desproporcional ao MVP |

Antes de aprovar qualquer protótipo 3D, exigir: (1) análise de licença para peças, tabuleiro e dependências; (2) medição real de FPS, latência, RAM, GPU e pacote no Debian sem aceleração e com aceleração; (3) funcionamento offline, sem telemetria e sem CDN; (4) geometria e orientação corretas para 64 casas; (5) navegação por teclado, rótulos acessíveis e fallback sem gráfico; (6) prova de que FEN, estado, relógios, PGN e replay usam o núcleo já validado. A proposta pode ser isolada em issue futura após esse gate.

**Sem benchmarks reais do notebook, não afirmar números de desempenho ou compatibilidade 3D.**
