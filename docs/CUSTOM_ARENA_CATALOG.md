# #63 — Arena: visualização de personagens fictícios locais (Slice 3)

Nesta fatia, a biblioteca da #117 e as revisões da #118 ganham um
**painel de consulta na Arena**, sem ativar novos motores, modificar
partidas, Elo, ligas, copas ou o catálogo histórico dos 64.

## Como iniciar

Criar personagens com `chesssim custom-player-create` e opcionalmente
renomear com `custom-player-rename`. Iniciar a Arena apontando para
uma **biblioteca já existente**:

```bash
chesssim arena \
  --rodent /dados/rodent-iv \
  --out /dados/chesssim-arena \
  --custom-library /dados/criador
```

A flag é opt-in. Sem ela, a Arena opera como antes. O diretório
configurado precisa existir e não pode ser um symlink conhecido, nem
estar sob ancestrais simbólicos. Esta fatia não cria nem corrige perfis
pelo servidor HTTP.

A nova rota `GET /api/arena/custom-players` aceita apenas URL sem
query e responde com `chesssim-custom-arena-catalog-v1`:
`configured`, `players`, `warning`. Cada item traz ID isolado
`user-...`, nome atualmente projetado pelo histórico de renomeações,
status ativo/desativado, revisão e flags explícitas
`installed=false`, `uci_eligible=false`,
`historically_validated=false`.

A interface mostra um painel de consulta e as identidades como
**opções desabilitadas** nos seletores de Duelo. O UUID completo aparece
junto ao nome: dois homônimos continuam distinguíveis visualmente. A fonte de dados da
seleção jogável continua sendo exclusivamente `/api/arena.players`,
que lista os 64 históricos; personagens customizados **não entram**
nesse array. O modo Jogar, Copas e Ligas continua usando apenas as
regras anteriores de elegibilidade UCI do servidor.

## Fronteiras de segurança e integridade

- A rota adicional é somente GET: não recebe caminhos de arquivos,
  comandos, atualização de perfil, binários, PGN ou credenciais.
  A API não aceita POST para esse caminho. Todos os acessos seguem
  a restrição de host loopback do servidor existente.
- `normalize_config` continua aceitando somente slugs dos 64.
  Mesmo que alguém manipule HTML/JS para habilitar um ID `user-...`,
  os endpoints de jogo não recebem elegibilidade nova.
- Nomes são apresentados via `textContent`, não como HTML.
  A interface também recusa dados de uma API fictícia que afirme
  `installed=true` ou `uci_eligible=true` para esses itens.
- O estado da biblioteca personalizada é carregado em uma rota
  **independente**. Dados corrompidos, revisões quebradas ou erros de
  leitura retornam erro 503 apenas nessa consulta e mostram aviso;
  `GET /api/arena` e os modos históricos seguem acessíveis.
  A interface elimina opções antigas na ocorrência de erro, sem
  usar dados personalizados em cache como se fossem confiáveis.
- O catálogo local é lido em polling menos frequente do que a
  posição ao vivo. No navegador, requisições opcionais usam timeout
  de cinco segundos e não bloqueiam a atualização das partidas.
  O backend usa o lock cooperativo da biblioteca
  ao ler, de modo que a primeira consulta pode criar o arquivo
  técnico de lock em uma biblioteca vazia. **Nenhum registro de
  personagem é alterado pela consulta.** Não há alteração de
  credenciais, execução de motor ou conexão de rede externa.
- O SHA-256 dos registros e das revisões apenas detecta
  inconsistências; não autentica a autoria e não é defesa contra
  operadores maliciosos que possam reescrever o diretório.

## O que continua pendente

Criar/instalar personalidades Rodent para customizados, permissões de
PGN, força/estilo mensurável, perfis reais experimentais e integração
jogável com Motor/Carreira continuam pendentes na #63 e #64. QA com
Debian, Rodent e Chrome reais continua **NOT_RUN** (#83).
