# Copas e Eliminatórias — Issue #58 (contratos, regras e aceite)

## Escopo e referência visual

A interface aprovada possui duas telas: **Copas / Chaveamento** e **Criar Copa / Configuração**. Mantém proporções, identidade escura e densidade de Ligas: lista de Copas à esquerda, área principal de confrontos, lateral de progresso/ações, formulário compacto de criação inferior. Os retratos vêm **somente** dos packs locais da História previamente validados quanto a proveniência e licença; são apresentados com o nome do enxadrista, era, resumo biográfico revisado e **desempenho nesta Copa**, nunca como medição de fidelidade histórica. Não há botão Limpar.

Presets:
- **Standard:** elenco curado de 64 perfis como possibilidade de inscrição (não garantia de instalação); chave eliminatória para 2–64 participantes e byes explícitos; nenhum resultado inventado de quem recebeu bye.
- **Campeões Mundiais:** elegibilidade por título, incluindo a coroa clássica e campeões FIDE da cisão 1993–2006 com etiqueta distinta. A lista versionada em \`cup_rules.CLASSICAL/SPLIT_FIDE/SPLIT_CLASSICAL\` contém os **18 campeões presentes no elenco atual**, incluindo Topalov. *Não* foram adicionados ao catálogo novos perfis (ex.: Gukesh, Khalifman, Ponomariov, Kasimdzhanov); nenhum histórico é inventado. Não equiparar conquista FIDE na cisão a reinado clássico sem identificar a categoria.
- **Feminino:** as **cinco** mulheres do catálogo atual (\`menchik\`, \`gaprindashvili\`, \`chiburdanidze\`, \`judit-polgar\`, \`hou-yifan\`) disputam **turno e returno**, dez rodadas, dois jogos + um bye explícito por rodada, vinte jogos no total. Essa lista é de perfis *femininos* do catálogo, não de campeãs mundiais femininas exclusivamente; Judit Polgár não deve ser apresentada como campeã mundial feminina.
- **Personalizada:** 2–64 participantes do elenco versionado, sem afirmar elegibilidade histórica ou força fiel. O filtro por país no formulário Personalizada começa com **Brasil (1 perfil)**, Henrique Mecking, verificado na [FIDE](https://ratings.fide.com/profile/2100010). Ele mostra explicitamente que o país tem menos de 2 participantes para uma Copa e bloqueia a criação. Outros países dependem de curadoria específica futura; nenhuma nacionalidade foi atribuída por palpite ou inferida do nome.

As sementes, participantes, chave, modalidade de execução e parâmetros Armagedom são **congelados ao criar a Copa**, protegidos por hash de configuração e validados novamente em cada consulta.

## Regulamento FIDE World Cup 2025

Fonte primária: [FIDE World Cup 2025 — Regulamento oficial](https://calendar.fide.com/event_files/2644/WorldCup2025Regulations.pdf), seções **4.7.1 a 4.7.5**.

Para cada confronto mata-mata:
1. **Duas partidas clássicas** com cores invertidas: 90 minutos para os primeiros 40 lances, acréscimo de 30 minutos após o 40º lance de cada lado; 30 segundos de incremento por lance desde o primeiro.
A cor inicial da série clássica e de cada nova fase de desempate é sorteada de maneira **independente por etapa**, com derivação SHA-256 da semente da Copa, ID do confronto e fase; as cores são invertidas no segundo jogo. Esse sorteio reproduz a exigência de novo sorteio da FIDE de forma determinística e verificável, sem afirmar que ocorreu um sorteio físico externo.

2. Se empatado, **duas partidas em 15+10**.
3. Se empatado, **duas partidas em 10+10**.
4. Se empatado, **duas partidas em 5+3**.
5. Se empatado, **duas partidas em 3+2**.
6. Se ainda empatado, **um Armagedom**: brancas têm **4 minutos + 2 segundos por lance**; pretas recebem o tempo de sua oferta de menor duração, também com 2 segundos de incremento, e ganham o confronto em caso de empate no tabuleiro. Se as ofertas forem iguais, sorteio determinístico e registrado decide quem recebe pretas. Para o usuário, os limites de tempo e incremento são editáveis *antes da criação*.

**Importante:** uma partida incompleta, tentativa de motor que falhou ou PGN ausente não equivale a empate. Somente jogos concluídos e comprovados avançam a série. **Bullet não é uma etapa do preset oficial de 2025.**

### Ofertas sintéticas auditáveis — contrato técnico

UCI não dispõe de comando para dois motores históricos ofertarem secretamente tempo para jogar de pretas. Por isso existe uma **política explícita do simulador** \`chesssim-deterministic-sealed-bid-v1\`: cada participante recebe um número pseudoaleatório determinístico dentro dos limites definidos pelo usuário, derivado da **semente da Copa, slug e ID do confronto**. O commit SHA-256 é calculado sobre slug, segundos, nonce e ID; o recibo registra compromisso e revelação para verificação posterior. O menor lance obtém as pretas; em igualdade de lances, um sorteio determinístico derivado da mesma semente decide as cores. O resultado registra tempo de pretas, política, participantes, compromisso, revelação, sorteio (quando houver) e o papel do empate.

**Essas ofertas não são decisões dos perfis UCI nem uma propriedade inferida de Fischer/Tal/etc.** O resultado depende da semente e parâmetros, não de calibração histórica. A reprodução pública do commit/reveal não torna a oferta verdadeiramente secreta perante quem lê o código; oferece **determinismo e trilha de auditoria**, apenas.

## Execução e persistência

Dois métodos mutuamente exclusivos:
- \`engine_full\`: partidas realmente jogadas por dois motores Rodent IV instalados, com profundidade UCI, relógios reais, tags PGN, hashes do executável e dois perfis, estado de terminação e prova pelo tabuleiro/queda de bandeira. Se um perfil estiver ausente, a execução é recusada, sem troca silenciosa por um substituto.
- \`fast_statistical\`: **projeção sintética determinística** de placar 1–0/0–1/½–½ para testar chaveamento e UI. Não inventa movimentos, não cria PGN, não computa Elo, não representa força de um grande mestre e informa a fonte nos relatórios.

Arquivos em \`<workspace>/cups/cup-<id>/\`:
- \`cup.json\`: regras congeladas, participantes, estado e snapshot de recursos.
- \`receipts/<game-id>.json\`: um recibo com SHA-256, resultado, fonte, clocks, fase, hash de PGN (quando real), lances de tempo sintéticos (quando aplicável).
- \`games/<game-id>.pgn\`: somente jogos UCI realmente concluídos e validados.

O checkpoint escreve PGN antes de recibo, ambos por substituição atômica. Um PGN órfão de queda anterior **não** é contado: ao repetir a tentativa, fica arquivado com sufixo \`orphan\`. Recibos duplicados são recusados. A chave/resultado é reconstruída de recibos verificados, nunca de um escore mutável. GETs não gravam dados; na reinicialização, um estado RUNNING é convertido em PAUSED e exige retomada explícita. Fechar o navegador não para o servidor. O botão **Pausar** solicita parada cooperativa; partidas incompletas não avançam resultados.

Uma execução de Copa é mutuamente exclusiva com Duelo, Jogar e Liga no workspace da Arena. A CPU executa apenas uma partida por vez. Os endpoints de escrita exigem Host loopback, Origin local, token de capacidade no fragmento da URL, JSON estrito e IDs com regex; frontend usa nós DOM/textContent, não HTML arbitrário.

## API local

| Método | Rota | Resposta |
| --- | --- | --- |
| GET | \`/api/cups/presets\` | Tipos, elencos, taxonomia |
| GET | \`/api/cups\` | Catálogo de Copas |
| GET | \`/api/cups/cup-<id>\` | Chave, próximos confrontos, desempenho e estados |
| GET | \`/api/cups/cup-<id>/games/<game-id>/replay\` | FENs/lances de PGN real verificado |
| GET | \`/api/cups/cup-<id>/export/json\` | Estado auditado + recibos |
| GET | \`/api/cups/cup-<id>/export/pgn\` | Somente PGNs UCI reais verificados |
| POST | \`/api/cups/create\` | Configuração imutável |
| POST | \`/api/cups/cup-<id>/run\` | Próxima partida/fase |
| POST | \`/api/cups/cup-<id>/pause\` | Solicitar parada cooperativa |

## Validação prática no Debian 13

1. Clone/atualize a branch \`feat/cups-world-champions-armageddon-58\`; instale \`python -m pip install -e '.[dev]'\`; execute \`python -m pytest -q -ra\` e \`python -m ruff check src tests\`.
2. Inicie a Arena com um Rodent IV instalado: \`python -m chesssim arena --rodent "$HOME/Projetos/Engines/rodent-iv" --out "$HOME/chesssim-pr80-evidence" --port 8766\`; use a URL com token do terminal.
3. Entre em **Copas**; confira os painéis, tamanhos, retratos e responsividade contra as duas imagens aprovadas. Abra a configuração e verifique presets/explicações; crie uma Copa **real de dois perfis instalados**.
4. Execute partidas reais, comparando PGN, clocks, perfil/hashes, pontuação e avançar de fase. Confira o replay e os botões de primeira/última posição. Interrompa e retome uma partida; resultados anteriores devem continuar idênticos.
5. Em uma **Copa separada** com \`fast_statistical\`, teste bracket 2, 16 e 64 sem instalar todos os perfis. Os resultados devem ser marcados como sintéticos, a exportação PGN deve ser recusada, o Elo não pode ser modificado.
6. Confira a lista de **18 elegíveis** de campeões *do elenco* e a distinção da cisão. Verifique **Feminino** com 5 jogadoras, 10 rodadas, 20 jogos e 2 folgas por jogadora, com classificação.
7. Confira em um Armagedom a prova e as ofertas sintéticas no JSON e no detalhe do confronto. Ajuste tempos em uma nova Copa e comprove que uma edição já criada **não** altera seus parâmetros.
8. Teste navegação Copas↔Ligas↔Arena↔História, visual mobile, recarregar navegador e reiniciar servidor. Decisão de merge sempre humana.

Os testes CI usam motores sintéticos e contratos de UI, mas **não substituem** o gate de um Rodent IV real no Chrome/Debian do mantenedor.


## Revisão de aceite visual e integração de estatísticas (8/11/2026)

- **Fotografias removidas exclusivamente da interface de Copas**, incluindo seleção de participantes, painel do confronto e cartão compacto de desempenho. Os packs e créditos da aba História permanecem intactos para eventual reutilização futura.
- **Rodadas 1, 2 e 3** agora têm cabeçalhos numerados, contagem de confrontos, diferenciação de fundo e separadores visíveis, da mesma forma que as rodadas posteriores. A chave mantém rolagem nos dois eixos.
- O **Calendário** funciona como lista expansível por rodada: clicar na rodada mostra todos os confrontos, os estados e os placares; partidas UCI concluídas oferecem replay. Jogar uma projeção abre apenas os resultados, nunca um tabuleiro fictício.
- **Desempenho do jogador** é clicável e leva diretamente ao perfil correto na aba História, onde estão as estatísticas e o Elo do simulador.
- O perfil em História considera as Copas reais que estejam no **mesmo workspace** usado em \`--out\`. Cada jogo UCI da Copa entra uma única vez nos dados de confronto, vitória, empate, derrota e torneio. Sua trilha PGN, cabeçalhos e hash são validados novamente sem modificar arquivos.
- Jogos UCI clássicos, rápidos e blitz da Copa entram nos **pools experimentais de Elo correspondentes**. **Armagedom** tem relógios desiguais, então entra no histórico real de partidas, mas **não** altera o Elo. Resultados \`fast_statistical\` permanecem apenas nas telas da Copa, **não** contam em estatísticas de partidas verificadas nem em Elo.
- O feedback informa quando a última partida terminou, foi interrompida ou falhou; não deve continuar exibindo "Executando..." após o retorno do estado concluído ou pausado.

Os dados observados do simulador não descrevem as vitórias de Lasker, Steinitz ou quaisquer outros na carreira real, nem atestam a força/fidelidade de seu perfil UCI. O uso de um diretório \`--out\` diferente gera outra amostra estatística, e não migra resultados automaticamente.
