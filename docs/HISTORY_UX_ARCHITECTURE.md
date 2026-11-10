# Aba História — arquitetura UX/UI (proposta V1)

**Status atual (09/11/2026):** arquitetura documental da PR #46 **mesclada e implementada em partes pelas PRs #54/#55, #71–#75 e #81/#82**. O restante do documento registra os **requisitos originalmente aprovados na etapa de desenho**; passagens escritas no futuro e gates de pré-implementação são históricas, não bloqueios de código ainda não integrado. Homologação visual/acessibilidade e revisão territorial de 16 retratos PDM seguem pendentes na #53; testes reais adicionais em #83. A #70 permanece aberta para partidas humanas, especificadas na #84.  
**Frente:** [#5](https://github.com/carloshgalves/chessCupMotorSimulation/issues/5) · **Gate:** [#45](https://github.com/carloshgalves/chessCupMotorSimulation/issues/45)  
**Referências de produto:** esboço manuscrito da aba História e captura do Play Magnus, utilizados **somente como inspiração de organização de componentes**, não como assets reutilizáveis.

## 1. Contexto e premissas existentes

- A Arena (`src/chesssim/arena.py`, `data/arena.html`, `arena.js` e `arena.css`) já é local, opt-in, protegida para ações de motor e consome estado persistido. O visor `/viewer` usa o replay `monitor.html/js/css`.
- `src/chesssim/data/roster.json` registra **64 jogadores** com slugs únicos. Atualmente há **21 aliases Rodent IV** previstos pelo catálogo, sem que isso prove fidelidade histórica.
- `watch` é somente leitura; abrir História não deve converter `watch` em serviço de escrita nem alterar a infraestrutura de execução de partidas.
- `#21`, `#22` e `#23` já entregaram base de tabuleiro, Arena e resultados. Esta aba **não substitui** o painel de Arena e **não implementa** a avaliação Stockfish prevista em `#24`.

## 2. Navegação, hierarquia e wireframes

A aba é acessada por **botão rotulado “História”** na barra principal da Arena. A implementação V1 deve manter o usuário na mesma origem/local e **preservar o token de execução armazenado exclusivamente no fragmento**, sem transmiti-lo em endpoints, parâmetros de query, logs ou telemetria. Preferir troca de vista no documento da Arena com estado de navegação e suporte a voltar/avançar; escolher um esquema de URL sem deixar cópias de links contendo token por padrão.

### 2.1 Visão geral — seleção horizontal de jogadores

```text
┌────────────────────────────────────────────────────────────────────────┐
│ COPA DOS IMORTAIS       [Arena] [História ●]     Status local         │
├────────────────────────────────────────────────────────────────────────┤
│ HISTÓRIA DOS ENXADRISTAS                                               │
│ Explore carreiras documentadas; perfis simulados não validados        │
│                                                                        │
│  ◀     ┌──────────┐   ┌─────────────────────┐   ┌──────────┐     ▶     │
│        │ FOTO     │   │                     │   │ FOTO     │           │
│        │ Anand    │   │ FOTO QUADRADA       │   │ Carlsen  │           │
│        └──────────┘   │ KASPAROV            │   └──────────┘           │
│                       │ SELECIONADO         │                          │
│                       └─────────────────────┘                          │
│                   Garry Kasparov · período / contexto                   │
│                  Síntese biográfica de fonte verificada                 │
│                        [VER PERFIL HISTÓRICO]                           │
│                         13 de 64 · indicativo                           │
└────────────────────────────────────────────────────────────────────────┘
```

- Carrossel **horizontal** da lista completa de jogadores. O item selecionado deve ter retrato **quadrado central, maior**; cartões vizinhos indicam continuidade. Nunca tomar a ordem do elenco como ranking.
- Um cartão combina imagem, nome, período/era **quando documentado**, texto curto sobre realizações verificadas, status da informação e botão de acesso ao perfil específico. Cliques nas laterais mudam a seleção; clicar no CTA abre o perfil.
- Desktop: centro + vizinhos. Mobile: centro quase em tela inteira, pista lateral de scroll/snap. Permitir toque, teclado, setas e botões; indicador textual (“n de 64”), sem depender só de cor ou gesto.
- Fotos são **assets próprios ou licenciados e creditados**. Sem permissão comprovada, utilizar monograma/placeholder neutro acessível, não retratos gerados fingindo serem fotos documentais.
- Fundo próximo do preto, letras brancas, contraste alto e acento discreto coerente com o verde-água/dourado já presente na Arena; aplicar tema só à aba e seus componentes, sem regressão visual da Arena aprovada.
- A referência Play Magnus vale para **hierarquia de cartão** (imagem proeminente, idade/época ou metadado, chamada para ação, texto), não para reproduzir visual, retrato de Magnus, slogan ou texto do aplicativo.

### 2.2 Perfil histórico selecionado

```text
┌────────────────────────────────────────────────────────────────────────┐
│ [← Todos os jogadores]         HISTÓRIA / GARRY KASPAROV                │
├──────────────────────────────────┬─────────────────────────────────────┤
│ Foto / placeholder + créditos    │ Nome, datas/período e biografia     │
│                                  │ Conquistas documentadas + fontes   │
├──────────────────────────────────┴─────────────────────────────────────┤
│ MARCOS DA CARREIRA (linha do tempo, quando existirem fontes)            │
├────────────────────────────────────────────────────────────────────────┤
│ PARTIDAS PARA EXPLORAR (0–5)                                             │
│ [Partida 1] [Partida 2] [Partida 3] [Partida 4] [Partida 5]            │
│ Tabuleiro 8×8 interativo + lances + primeiro/anterior/próximo/último    │
│ Rótulo da fonte: HISTÓRICA DOCUMENTADA ou SIMULAÇÃO LOCAL               │
├────────────────────────────────────────────────────────────────────────┤
│ NO SIMULADOR — placar local; NÃO representa carreira real              │
│ Torneios  · Duelos  · Vitórias/Empates/Derrotas  · Confrontos diretos    │
└────────────────────────────────────────────────────────────────────────┘
```

- Um único tabuleiro instanciado por vez e reutilizado ao trocar a partida selecionada. Controles: início, anterior, próximo, fim; seleção da lista de lances; retorno; status de partida interrompida. Compatibilidade de replay **preferencialmente por extração/reuso do renderizador e parser existente**, sem criar outra regra de lances.
- Não iniciar motor/duelo quando alguém interagir com partidas históricas; o replay é **somente leitura**.
- Se houver **menos de cinco partidas**, apresentar apenas as disponíveis e informar quantidade; se nenhuma, estado vazio explicativo. Não preencher com partidas inventadas.
- Perfil sem biografia ou foto pode ser aberto: “Conteúdo histórico em curadoria”. Um jogador sem motor instalado **continua pesquisável** e visível no histórico.

## 3. Separação semântica e proveniência

| Camada | Conteúdo | Regra de apresentação |
|---|---|---|
| Identidade no elenco | `slug`, nome e grupo | Único vínculo: `roster.json`; nenhuma posição no elenco vira ranking |
| Carreira histórica | fatos, conquistas, texto e cronologia | Curadoria com referência rastreável, ano/data e revisão editorial; desconhecido = ausente |
| Perfil experimental | janela de referência, alias/perfil instalado, fidelidade | Hipótese de pesquisa; **não validado** enquanto não houver estudo específico |
| Partidas históricas | jogos originais documentados e PGN licenciados/localmente disponíveis | Origem e procedência explícitas, sem misturar com simulação |
| Partidas simuladas | PGNs produzidos pela aplicação | Rotular **Simulação local** e indicar condições/runs identificáveis |
| Estatísticas locais | resultados acumulados de duelos/torneios persistidos | Sempre rotular **No simulador**, não como estatística profissional real |

Não inferir períodos, troféus, números de partidas reais ou ELO a partir de nomes, perfis Rodent ou da interface Play Magnus. Se um fato não tiver fonte, mantê-lo pendente.

## 4. Contratos conceituais de dados (a aprovar antes de codificar)

### 4.1 Identidade e curadoria
`PlayerHistory` vincula-se exclusivamente a `Player.slug` existente:

```json
{
  "schema": "history-profile-v1",
  "slug": "kasparov",
  "review_status": "pending",
  "short_bio": null,
  "era_label": null,
  "portrait": null,
  "highlights": [],
  "timeline": [],
  "sources": []
}
```

Proposta de campos de `portrait`: `local_asset`, `alt`, `author`, `license`, `source_url`, `verified_at` (todos submetidos à validação). `highlights/timeline`: `date_or_year`, `title`, `summary`, `source_ids`. `review_status`: `pending|reviewed`. Rejeitar `slug` desconhecido, IDs/fontes duplicados e referências inexistentes. Nunca aceitar URL remota como caminho de asset runtime.

### 4.2 Acervo de até cinco jogos
`HistoryGame` deve carregar `id` canônico, `player_slugs`, `source_kind = historical|simulation`, `white`, `black`, `event`, `date`, `result`, `pgn_ref` (identificador interno resolvido no servidor), `pgn_sha256`, `source_ids`, `curation_order` e status de validação. Metadados desconhecidos permanecem nulos. Não aceitar caminhos absolutos relativos fornecidos pelo navegador.

Política de seleção V1:
- jogos históricos são candidatos somente quando o PGN e a proveniência foram conferidos; jogos simulados somente quando pertencem ao jogador e existem em runs locais persistidos;
- lista de **até cinco** por jogador, ordem determinística e fonte rotulada por item; sem ranking algorítmico fictício;
- permitir curadoria explícita de destaques, com desempate por ID, ou seleção determinística de últimas partidas completas quando não houver curadoria; documentar a regra no serviço e na UI;
- PGN inválido, ausente, parcial ou com FEN/lances ilegais não deve aparecer como partida concluída nem ser executado como ação de jogo. Estado inválido ou não encontrado gera mensagem e log local não sensível.

### 4.3 Estatísticas da aplicação
`LocalPlayerStats`: `slug`, `source_scope`, `run_count`, `tournaments_participated`, `duels_count`, `completed_games`, `wins`, `draws`, `losses`, `incomplete_games`, `head_to_head` (oponente, totais e identificadores da origem), `updated_at`. Números provêm exclusivamente de artefatos locais. Decidir o significado de `duels_count` como **confrontos/sessões distintas**, diferente de partidas com cores invertidas. Desempate e múltiplas tentativas são contadas segundo IDs canônicos, sem somar uma sessão duas vezes. Arquivos corrompidos ou contraditórios precisam ser desconsiderados ou sinalizados, nunca “corrigidos” silenciosamente. Resultado parcial/interrompido não é vitória/empate.

## 5. Serviço e organização modular sugeridos

O backend de `chesssim arena` permanece na mesma origem e em `127.0.0.1`, sem portas adicionais. Endpoints **GET-only** e estritamente limitados a slugs/IDs existentes, sujeitos a decisão na PR de arquitetura:

- `GET /api/history/players`: resumo da lista com 64 identidades + estado de curadoria, imagem ou placeholder.
- `GET /api/history/players/{slug}`: dados biográficos e fontes do perfil.
- `GET /api/history/players/{slug}/games`: até cinco descritores elegíveis, com origem.
- `GET /api/history/games/{id}`: payload de replay somente leitura de jogo allowlisted, validado no servidor.
- `GET /api/history/players/{slug}/stats`: estatísticas locais e cobertura.

Os exemplos acima são contratos **propostos**, não endpoints existentes. Alternativa aceitável: consolidar payloads para evitar múltiplas requisições, desde que separação e segurança sejam mantidas.

Separar módulos de: (i) curadoria e validação, (ii) catálogo de jogos e adaptação de PGN, (iii) agregação estatística, (iv) componentes de visualização. CSS/JS devem ser empacotados e servidos localmente como os ativos da Arena. Não promover componentes internos de `monitor.js` para APIs públicas sem teste de regressão.

## 6. Segurança, performance e direitos

- Sem novos POST/PUT/PATCH, sem caminhos de arquivo fornecidos pelo frontend, sem chamadas UCI, sem rede externa, sem JS/CSS remotos. Preservar `Host`, `Origin`, CSP, `X-Frame-Options`, token de ações e isolamento de `watch`.
- Slugs vindos do roster; game IDs resolvidos por catálogo interno. Validar tamanho dos PGNs, esquema, encoding, legalidade de lances, diretório raiz, symlinks e integridade/hashes dos dados. Escapar via `textContent` e DOM API, não interpolar HTML de PGN ou biografia.
- Retratos devem ter proveniência, licença e crédito exibível. Não incorporar capturas/ilustrações/branding do Play Magnus e não importar imagens de sites sem licença conferida.
- Lazy-loading de fotos, feedback de carregamento/erro, um tabuleiro ativo, sem motor em segundo plano. Sem dependência de serviço pago.
- Testes: Python 3.11–3.13, JS/DOM ou navegador headless para carrossel, replay e navegação; comportamento em desktop e mobile, preferência por movimento reduzido, foco e teclado, `aria-label` / `aria-live`, contraste WCAG AA; homologação Chrome/Debian separada.
- A interface deve permitir uso de mouse, toque e teclado sem perda de contexto. Respeitar idioma PT-BR, títulos de seção e leitores de tela.

## 7. Estados e critérios demonstráveis

1. Selecionar jogadores com clique, rolagem, setas e teclado; ao mudar, nome/foto/texto e CTA sempre pertencem ao mesmo slug.
2. Abrir Garry Kasparov, Viswanathan Anand ou Magnus Carlsen do catálogo não pressupõe disponibilidade de motor, imagem ou conteúdo curado. Placeholder/sem dados é legítimo.
3. Acessar o perfil e retornar à Arena não interrompe duelo nem limpa token ou formulário em edição.
4. Exibir 0, 1, 4 e 5 partidas sem erros; repetir/avançar/voltar lance a lance e resetar ao trocar o jogo.
5. Mostrar dados históricos e dados do simulador em seções separadas com rótulo de origem; nunca mostrar falso Elo, título ou percentual quando não apurado.
6. Ensaio com PGN malformado, ID fora do catálogo, path traversal, symlink, ficha sem foto e manifesto parcial/corrompido.
7. Os tabuleiros de `/viewer` e `watch` continuam 8×8 e legíveis com o contraste de peças aprovado no trabalho `#43`.

## 8. Sequenciamento e gate de implementação

1. **Arquitetura e revisão documental** (issue #45, esta PR): fechar decisões contratuais, UX, licenças e critérios.
2. **Dados e agregação read-only**: curadoria de identidades/fotos, lista de jogos verificáveis, estatísticas derivadas de runs.
3. **Navegação e carrossel**; **perfil biográfico**; **replay dos cinco jogos**; **painel local de estatísticas**.
4. **Testes de ponta a ponta e homologação visual** no Chrome/Debian, sem antecipar aceite de motor real.

**Regra:** não iniciar implementação das tarefas dependentes até a decisão e integração da PR exclusivamente documental. A ausência de ativos históricos licenciados não bloqueia a navegação com placeholders; bloqueia a publicação de alegações factuais não verificadas.
