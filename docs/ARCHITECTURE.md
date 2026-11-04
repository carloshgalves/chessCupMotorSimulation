# Arquitetura

O núcleo Python (`src/chesssim`) coordena perfis de motor, corpus PGN, protocolos de avaliação, partidas e geração de artefatos locais. Os processos UCI são executados externamente; o repositório não contém binários de motores.

## Componentes

- **Catálogo e perfis**: registro dos participantes, parâmetros e perfis experimentais.
- **Dados e abertura**: importação de PGN, filtros de proveniência e repertório condicionado à posição.
- **Orquestração**: execução de duelos, alternância de cores, PGNs e resultado.
- **Replay**: visualização local das partidas concluídas.
- **Avaliação**: comparações de lances, estatísticas de estilo e análises externas com Stockfish.

## Princípios

A reprodução do estilo exige evidência em posições equivalentes e avaliação cega, não apenas pareamento de aberturas. Os protocolos históricos preservados no núcleo são compatibilidades técnicas necessárias para manter partições previamente congeladas, não releases publicados do produto.
