# Fischer — protocolo individual e contrato de competências (#191)

## A decisão de produto

Um histórico por vez: Fischer primeiro. Não substituir automaticamente
Rodent oficial ou Simple provisório. A existência de uma partida legal
não é validação histórica.

Quatro grandezas distintas: rating competitivo (inicial 2500 para todos);
força intrínseca medida em decisões; força relativa do motor em partidas
controladas; preferências/estilo. Nenhuma delas é a outra.

### Carreira e XP (contrato para #64/#63, não jogo implementado)

Vetor versionado `opening, tactics, dynamics, defense, positional, endgames`.
Escala inteira 0–110 proposta pelo mantenedor. Valores iniciais históricos
devem derivar de âncoras empíricas comuns; falta de evidência = null, nunca
zero, média ou número arbitrário. Rating 2500 independe do vetor.

Atributos não são pesos que somam 100; a posição pode testar múltiplos
atributos. XP é saldo de carreira; uma compra altera apenas um atributo em
+1, sem conceder Elo ou vitória artificial. Preço 200 XP é exemplo, não
economia aprovada; limitar tetos, evitar compras negativas e transações
duplicadas; progresso e recibos persistidos e separados da personalidade
histórica imutável. Instâncias de carreira podem existir para o personagem
do usuário e os 64 históricos. Não implantar economia até verificar
causalmente a conexão atributo -> decisões. Ex.: 90->91 deve ser pequeno;
90->97 deve ser mensurável agregado em painel pré-fixado; não obrigar cada
lance/partida a melhorar. Testar curvas monotônicas em média, efeitos
colaterais, força por fase, identidade, legalidade, ablação e platôs.
Se ajuste não responde, atributo não está operacionalmente calibrado.

### Referências (distintas)
- Jeff Sonas, Chessmetrics, pico 2895 em outubro de 1971:
  https://en.chessbase.com/post/the-greatest-che-player-of-all-time-part-ii
  reconstrói ratings a partir de resultados, não avaliação de lances.
- Regan & Haworth, Intrinsic Chess Ratings, AAAI 2011:
  https://ojs.aaai.org/index.php/AAAI/article/view/7951
  calibra qualidade de escolhas contra grupos de força conhecida. Não
  copiamos uma curva ou convertemos CP diretamente em Elo.
- FIDE, https://museum.fide.com/champions/robert-bobby-fischer —
  eventos de carreira, 1970 Interzonal, Candidates 1971, título 1972.
- PGN Mentor https://www.pgnmentor.com/files.html, snapshot PGN Mentor/rozim
  já fixado no projeto. Fontes adicionais precisam de origem/uso autorizado,
  checksum, normalização, deduplicação e auditoria do formato/ritmo.
  O censo de um snapshot não equivale a todos os jogos que existiram.

## Fatia A: censo, estilo e qualidade FIT

Pré-fixar períodos: até 1957; 1958–1962; 1963–1969; 1970–1972
(auge competitivo hipótese); 1973–1991 (hiato/possíveis registros
não oficiais); 1992+ (retorno); data desconhecida isolada. Censo por ano,
período, lado, presença de Elo e controle de tempo, contagens por braço.
Nenhum resultado de jogo excluído precisa ser publicado. Sem afirmar
todos os jogos clássicos: tags de controle de tempo podem estar ausentes.

Reusar a divisão global real85 por trajetória, aproximadamente
82,45% FIT/2,55% TUNE/15% TEST. Computar todos os lances FIT para
contagens comportamentais e repertório até ply40, independente por cor.
Para Stockfish, selecionar uma posição min-hash com mais de um lance legal por jogo e por fase
(prioridade material <=16 pontos não peões para final, senão ply<40
abertura, senão meio-jogo). A amostra mede qualidade condicionada ao
contexto histórico, não competência pura. Medir acerto top1, diferença
assinada em CP na mesma posição raiz, distinguir mates, manter
denominadores e FENs únicas. Lances forçados contam no
corpus/repertório, não entram na amostra de qualidade; publicar também
agregado não-abertura, sem confundi-lo com Elo. Stockfish d12 (Threads=1, Hash=64),
registrar versão e hash do executável; não comparar coortes com hash
diferente. Sensibilidade d16 no futuro. Não há conversão CP-Elo ainda:
o próximo passo é calibrar com grupos de rating e ritmo conhecidos,
posições equivalentes, estimativa de incerteza por jogo e fontes
independentes. Não confundir FEN repetidas com observações independentes.

O FIT é diagnóstico exploratório, NÃO validação independente. O antigo
holdout por anos pode sobrepor real85 FIT; sobreposição de pré-treino
externo é não verificada (#159). O outer TEST real85 já foi inspecionado
em PRs anteriores, portanto um teste confirmatório requer novo protocolo
de fontes/partidas realmente reservadas e autorização #147. Não reabrir
outer TEST nesta fatia. Censo apenas metadados; reparse para conferir
trajetória não significa avaliar lances reservados. Nenhuma trajetória
histórica ou livro derivado é publicada como artefato.

O workflow produz também o residual CPU já implementado (todas decisões
FIT de Fischer, TUNE para diagnóstico, não ajuste), não chama esse
residual de LoRA Maia/DSC. Evidência neural e repertório cruzadas
por hash do mesmo PGN. Sem pesos/arquivos oficiais alterados.

## Fatias seguintes (não realizadas)
1. Âncora de força intrínseca e escalonamento comum, contexto de posição
   e dificuldade; classificação tática/dinâmica/defensiva/posicional
   validada, intervalos por fase, separar estilo preferencial de força.
2. Ablação Fischer-only: Rodent, Simple, Maia/DSC originais, residual
   e LoRA treinável onde viável; orçamento explícito, FIT/TUNE sem
   sobrepor conjunto confirmatório. Usar os dados, não supor a arquitetura
   vencedora. Livro FIT com diagnóstico TUNE e suporte por fase/cor.
3. Ajustar orçamento de busca e avaliação/guardas por fase; medir
   qualidade de decisões e centenas de partidas completas em painel
   novo de raízes FIT, cores e controles graduados, partidas únicas
   e intervalos por raízes/jogos. Não selecionar com TEST.
4. Validar sobre conjunto confirmatório realmente independente e congelado,
   gates #147/#148, segurança tática/finais, reversibilidade, proveniência.
   Arena opt-in só após aprovação. Sem Elo histórico fabricado.
5. Converter dimensões em atributos somente após evidência de calibração
   e curvas de resposta. Carlsen é próximo depois do aceite/limites
   documentados de Fischer, não em paralelo.
