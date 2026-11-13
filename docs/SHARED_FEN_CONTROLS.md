# Controles negativos em posições compartilhadas — issue #11

Esta função calcula um **contraste descritivo**, não uma certificação de personalidade histórica.

Ela recebe decisões históricas de **dois jogadores** e previsões já obtidas de **dois perfis**,
ambos avaliados nas **mesmas posições FEN completas**. Para cada posição,
calcula as concordâncias dos quatro pares perfil × jogador. Depois compara a média
da atribuição original com a média obtida pela **atribuição invertida** dos perfis.

## Pré-condições

- Só fornecer dados autorizados, não lacrados, com origem e identificadores de partidas verificáveis.
- A confirmação CLI --ack-unsealed é uma declaração do operador, **não** uma verificação de embargo.
- Garantir externamente que conjuntos finais não foram usados em seleção, ajuste, escolha de FENs ou controle.
- Gerar previsões dos perfis com condições comparáveis (engine, perfil, profundidade, livro, FEN).
- Pré-registrar a seleção dos FENs; não escolher posições depois de olhar o resultado.
- min_ply=17 é um filtro de meios-lances **cronológico**, não prova de estar fora do repertório.
- Nomes diferentes de game são IDs distintos, mas não provam partidas estatisticamente independentes.

## Uso por linha de comando

Prepare um arquivo JSON local com estas chaves:

- player_a, player_b: rótulos dos jogadores históricos.
- profile_a, profile_b: nomes dos perfis; distintos dos rótulos de jogadores.
- min_ply: inteiro opcional, padrão 17.
- positions: lista de objetos com player, game, fen (FEN completo canônico), move (UCI legal) e ply.
- predictions: objeto com uma tabela FEN -> lance UCI para **cada** perfil; somente FENs compartilhados.

Exemplo mínimo de posição sintética: o FEN rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 9
permite e2e4 e d2d4, e tem ply=17. Isto **não é uma partida histórica comprovada**.

Execute no diretório do projeto:

    chesssim shared-fen-controls --input controle.json --out resultado.json --ack-unsealed

O comando rejeita entrada simbólica, JSON maior que 8 MiB, campos desconhecidos,
lances ilegais, previsões incompletas e sobrescrita de relatórios existentes.

## Interpretação

- matched_fens: FENs completos presentes nos dois conjuntos históricos.
- accuracy_by_profile_and_player: concordância média nos mesmos FENs, peso uniforme por FEN,
  e proporção de decisões por jogador/FEN entre os identificadores de partidas distintos.
- correct_assignment_score e swapped_assignment_score: médias com perfis associados originalmente
  e após inverter as atribuições, respectivamente.
- contrast: diferença das duas médias; valor positivo **não comprova** identidade histórica.
- distinct_game_ids_by_player: quantidade de IDs distintos; não valida a independência real dos jogos.
- input_evidence_sha256: SHA-256 de registros normalizados, previsões, papéis e filtro;
  **não** é um hash do corpus PGN original.
- input_file_sha256: SHA-256 do arquivo JSON de entrada (somente no CLI).
- limitations.split_provenance=NOT_VERIFIED: segregação de treino, validação e teste final não auditada.
- NO_EXACT_SHARED_FENS: nenhum conjunto de posições compartilhadas, nenhuma inferência de contraste.

Um contraste positivo pode decorrer de diferença de força, abertura, corpus, seleção
de posições, estilo ou vazamento. Posições de uma mesma partida não são unidades
estatisticamente independentes; por isso não calculamos significância ou intervalo
de confiança neste estágio. Não há classificação cega nem porcentagem de fidelidade.

Este módulo é **offline**. Não executa motores, downloads, treinamentos, decisões
de seleção ou abertura de corpora/testes finais. Dados reais e homologação dos
motores permanecem etapas posteriores de #11 e #12.
