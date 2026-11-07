# Elo ChessSimulation experimental — Issue #60

## Definição e separação de conceitos

O **Elo ChessSimulation** mede apenas resultados de partidas locais verificadas. Os 64 jogadores começam em **2500**, independentemente de força UCI. Esse número inicial **não define força do motor, Elo FIDE, precisão Stockfish, nem fidelidade histórica**. Motor e perfis permanecem intactos durante leitura, cálculo e exportação.

Os contratos são distintos: **competition_rating** é a classificação observada; **engine_strength_config** identifica os hashes de motor e perfis quando certificados no manifesto de torneio; **strength_evaluation** continua não avaliada por falta de benchmark independente; **move_accuracy** permanece não medida.

## Equações, resultados e pontuação

Para cada **partida concluída individualmente**, usar as brancas como referência:

- E_white = 1 / (1 + 10^((R_black - R_white)/400))
- Δ_white = K × (S_white − E_white)
- R_white' = R_white + Δ_white; R_black' = R_black − Δ_white

S_white vale 1, 0,5 e 0 para vitória, empate e derrota. K padrão é 10; opção de exportação permite outro valor decimal positivo até 100, sem converter-se em força UCI. A implementação utiliza Decimal de 38 dígitos e arredonda **somente na apresentação** a uma casa decimal. A atualização por delta simétrico conserva a soma dos ratings entre dois oponentes. Somar uma constante a ambos não altera E.

O placar fictício **3/1/0** (vitória/empate/derrota) é apresentado separadamente. Não é a escala 1/0,5/0 empregada na fórmula Elo.

## Seleção de dados elegíveis

O código reaproveita o verificador de PGNs de history_stats.py (#52). Somente sessões da Arena e tentativas de torneio local validadas, com identidade canônica e resultado terminal comprovado, entram no cálculo. Nenhuma partida histórica documental, em andamento, interrompida, adulterada, ilegítima ou sem resultado demonstrado pontua. Uma tentativa cancelada nunca é convertida automaticamente em empate ou derrota.

IDs idênticos numa retomada entram uma vez. IDs idênticos com conteúdo divergente são erro. PGNs de conteúdo igual com IDs diferentes podem ser partidas independentes; pseudorreplicação de escolhas de estilo é um problema distinto da #69.

O universo padrão é **local-uci-observed-v2**, com ratings independentes por modalidade e sem Elo global que misture ritmos. O preset official do ChessSimulation não é tratado como certificação FIDE. Partidas sem relógio de preset são unclassified. Simulações estatísticas futuras e Carreira precisam de outros universos, com reset e provenance próprios.

## Ratings por relógio: classificação e isolamento

Cada modalidade mantém **seu próprio rating/ledger**, com os 64 jogadores inicialmente em 2500. Resultados não transitam entre classical, rapid, blitz e bullet; partidas sem relógio jogado são isoladas em `unclassified`, sem creditar Elo nas outras quatro modalidades. A fórmula logística e o K não mudam, apenas a escolha do conjunto de partidas. Não há um Elo combinado.

A classificação considera **o tempo inicial por jogador mais 60 incrementos**, sempre em segundos: `T = initial_seconds + 60 * increment_seconds`. Bullet: **T < 180** (convenção do ChessSimulation, não rating FIDE); blitz: **180 ≤ T ≤ 600**; rapid: **600 < T < 3600**; classical: **T ≥ 3600**. A regra é determinística e não infere modalidade do nome arbitrário do preset. Limites gerais de rápido e blitz: https://handbook.fide.com/chapter/e012023.

Exemplos: 1+0 bullet, 3+2 blitz, 10+0 blitz, **10+2 rapid**, 30+5 rapid, 30+20 rapid, 45+0 rapid, **30+30 classical**, 45+30 classical. O preset antigo `official` é 10+2 (rapid), não uma alegação de Elo FIDE. Exceções de torneios com rating FIDE em 2026 não mudam esta classificação local: https://www.fide.com/fide-updates-rating-regulations-to-include-faster-time-controls-for-major-events/.

A Arena exibe **Busca do motor** e **Relógio da partida** separadamente. A profundidade máxima de busca pode coexistir com relógios de partidas (ex.: depth 16 + 1+0), ambos enviados à UCI. Tempo fixo por lance (movetime) não é combinado com relógio para evitar semântica ambígua. O protocolo experimental `official` preserva os limites originais e rejeita qualquer profundidade extra. A Arena oferece relógios personalizados (1–240 minutos iniciais, incremento 0–120 segundos). Os valores persistidos na sessão devem coincidir com `TimeControl` no PGN validado, antes de pontuar. Torneios existentes usam seus `clock` explícitos; no futuro, presets customizados em torneios também serão classificados pelos números e não pelo rótulo. Modos depth e movetime não possuem relógio equivalente, portanto permanecem `unclassified`.

Os contratos foram versionados para `chesssim-competition-elo-v2` e `chesssim-rating-ledger-v2`, distinguindo-os dos snapshots globais anteriores. Qualquer migração de dados antigos deve ser explícita.

## Ordenação e trilha de auditoria

Os eventos são ordenados deterministamente por **created_manifest_utc e game_id**, com datas nulas no final. created_manifest_utc indica a criação do manifesto e **não equivale à data/hora em que cada partida começou ou terminou**. Sem o horário de execução da partida, não é possível alegar uma curva Elo cronológica real. A curva representa apenas o fechamento na ordem versionada.

O esquema de ledger é **chesssim-rating-ledger-v2**. Cada evento registra: game_id, white, black, result, score_white, expected_white, before/after das duas cores, K, origem, modalidade, data do manifesto, **SHA-256 do PGN efetivamente validado**, hashes de configuração quando disponíveis, versão de modelo, previous_checksum e checksum. Para Arena o hash identifica o arquivo individual; para torneios ele identifica o PGN consolidado de duas partidas, conforme o ledger da competição. A cadeia é SHA256 do checksum anterior concatenado à serialização JSON canônica de cada evento; a verificação detecta modificação, exclusão ou reordenação do ledger. A cadeia é prova de *consistência interna de um snapshot*, não assinatura criptográfica de fonte externa.

## API de leitura e exportação explícita

- GET /api/competition/ratings?modality=rapid apresenta somente rápido; valores válidos: classical, rapid, blitz, bullet e unclassified. Sem parâmetro, rapid. Cada relatório apresenta ratings dos 64 e `available_modalities` com contagem por pool, sem expor o ledger.
- GET /api/history/players/fischer/rating?modality=classical apresenta apenas o Elo clássico de Fischer.
- Nenhum GET modifica arquivo, estado UCI ou token. Nenhuma rota HTTP recebe caminho arbitrário de exportação.

Exportação pelo terminal:

    python -m chesssim rating-export --workspace ~/chesssim-sessoes-visual --modality rapid --out ~/elo-rapid.json

Para atualizar o mesmo arquivo após partidas novas, exigir opção explícita:

    python -m chesssim rating-export --workspace ~/chesssim-sessoes-visual --modality rapid --out ~/elo-rapid.json --replace

Para experimento separado com K=20:

    python -m chesssim rating-export --workspace ~/chesssim-sessoes-visual --modality classical --out ~/elo-classical-k20.json --k 20

O `snapshot_checksum` é **obrigatório** para verificar relatórios exportados, não bastando a cadeia de hashes do ledger. É prova de consistência interna, não assinatura digital de uma fonte independente. Se o snapshot é byte a byte igual, não regravar. Se mudou, recusar substituição sem --replace. A exportação usa temporário no mesmo diretório, fsync e os.replace. Arquivos existentes e symlinks exigem validação explícita.

## Provisoriedade, amostra e limitações

- **Sem partidas**: Elo inicial 2500.0, status sem_amostra, sem classificação e sem faixa de score.
- **1 a 29 partidas**: provisional.
- **30 ou mais**: observed_experimental; indica apenas maior amostra, **não calibração demonstrada da força**.
- A faixa de score explora limites de Hoeffding para observações entre zero e um. Requer hipótese de independência frequentemente falsa em confrontos repetidos, portanto **não é um intervalo de confiança de Elo** ou de força real.
- Precisão de lances aparece **Não medida** e strength_evaluation é nula.
- Cobertura parcial, modalidades misturadas e falta de dados são explicitadas na UI. Sem amostra suficiente não há inferência histórica.
- Rating de Carreira deve ser isolado e nunca misturado ao universo observado UCI sem validação própria.

## Testes e homologação

A matriz automatizada cobre aritmética analítica, empates, offset, soma das atualizações, reprocessamento de 100 jogos em ordem permutada, identidade de evento, retomada, entradas incompletas e adulteradas, cadeia de hash, exportação, endpoints GET-only, CSP e DOM com XSS e respostas atrasadas.

A homologação Chrome/Debian precisa comprovar ranking vazio e preenchido de **cada pool**, edição de relógio personalizado, páginas de jogador, responsividade, teclado, e migração de sessões antigas sem relógio. Os testes opcionais externos do motor não devem ser declarados aprovados se estiverem em skip.

Referência de contexto, não incorporação integral da FIDE: https://handbook.fide.com/chapter/B022024.

## Homologação visual do replay (PR #77)

A lista de lances do tabuleiro ao vivo possui **histórico completo**, com linha numerada `1. Brancas | 1... Pretas`, botões clicáveis e rolagem vertical independente. Segue o lance mais recente enquanto o usuário está no final; quando o usuário navega ou rola para cima, a lista preserva a posição. **Voltar ao vivo** reativa o acompanhamento. O tabuleiro mantém os 64 quadrados e SVGs carregados, atualizando só as casas cujo conteúdo mudou e preservando a interface entre polls idênticos.
