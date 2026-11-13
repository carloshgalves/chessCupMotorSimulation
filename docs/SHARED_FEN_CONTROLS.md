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
- declared_assignment_score e swapped_assignment_score: médias com perfis associados originalmente
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


## Fatia da issue #11: perfil genérico de controle (opcional)

Pode-se declarar um terceiro perfil de comparação no **mesmo conjunto de FENs**.
Acrescente o campo `generic_profile` ao JSON, por exemplo
`"generic_profile": "rodent-default"`, e uma tabela correspondente em
`predictions` para todos os FENs compartilhados. Sem esse campo, o contrato
de dois perfis e os relatórios anteriores permanecem inalterados.

O relatório acrescenta `accuracy_by_profile_and_player` para o controle e
`generic_control` com concordância por jogador, diferença da concordância
da atribuição declarada em relação ao controle para cada jogador e média
descritiva dessas diferenças. Se não houver FENs compartilhados, as métricas
são `null`. O controle usa os mesmos FENs, pesos e decisões históricas. A
declaração do controle altera o hash da evidência normalizada.

**Importante:** `generic_profile` é um nome declarado pelo operador,
não uma certificação de que esse perfil seja de fato genérico, imparcial
ou tenha parâmetros equivalentes aos candidatos. O campo
`limitations.generic_control = DECLARED_NOT_VERIFIED` registra essa
limitação. Registre externamente SHA-256 de executável e dos arquivos
de perfil, parâmetros UCI, profundidade, política de livros e recortes
dos corpora. Compare apenas experimentos com condições equivalentes,
sem utilizar conjuntos finais lacrados.

A comparação não mede Elo, força independentemente, nem identidade
ou fidelidade histórica. Não substitui validação cega ou controles
negativos com dados experimentais reais.

## Fatia da issue #11: excluir FENs catalogados no repertório

Este filtro opcional **exclui somente posições presentes em um catálogo declarado**.
Ele não certifica que todas as outras posições estejam fora de repertório.
Escolha, catalogue e congele o conjunto antes de examinar os resultados,
sem recorrer a partidas do teste final lacrado ou decisões do motor avaliado.

Acrescente ao JSON de entrada um objeto com este formato:

```json
{
  "opening_exclusion": {
    "fens": ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 9"],
    "declared_source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
}
```

O SHA-256 é fictício neste exemplo. Informe o hash real da fonte declarada.
A ferramenta verifica o formato do hash, **não os bytes da fonte externa**;
a proveniência permanece DECLARED_NOT_VERIFIED.

Somente os FENs compartilhados que **não** constam no catálogo são
utilizados no contraste. Os perfis devem fornecer previsões **apenas**
para esses FENs restantes. Previsões para FENs excluídos, ausentes ou
não compartilhados causam rejeição. O relatório preserva hashes e contagens
agregadas, mas não expõe os FENs ou os lances individuais.

Campos adicionais:

- opening_exclusion.method = EXACT_FULL_FEN_CATALOG.
- opening_exclusion.declared_source_sha256 = hash de fonte declarado, não verificado.
- opening_exclusion.matched_fens_before_exclusion = número original compartilhado.
- opening_exclusion.matched_fens_excluded = posições compartilhadas excluídas.
- opening_exclusion.remaining_unlisted_fens = FENs compartilhados avaliados.
- limitations.opening_exclusion = UNLISTED_IS_NOT_PROOF_OF_OUT_OF_REPERTOIRE.

Se todas as posições compartilhadas forem excluídas, a situação será
NO_SHARED_FENS_AFTER_OPENING_EXCLUSION e os escores continuarão nulos.
Se o catálogo não combinar com nenhuma posição, a saída indicará zero
exclusões; **não** se deve concluir que o estudo esteja fora do repertório.

Importante: o catálogo usa **FEN completo**, com relógios e número de lance.
Posições visualmente idênticas com contadores diferentes não são iguais
neste filtro. É um controle experimental exploratório, não um classificador
de abertura nem uma verificação de embargo de treino/validação/teste.
A elaboração de um catálogo proveniente de fontes licenciadas e o controle
das partições continuam na issue #10; a validação cega, na #12.
