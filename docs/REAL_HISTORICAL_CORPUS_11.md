# Issue #11 — lote piloto de partidas históricas reais

Este pipeline **baixa e lê os PGNs reais** de quatro jogadores históricos,
utilizando um espelho público fixado por commit SHA.
Não depende de dados sintéticos para produzir seu resultado de pesquisa.
Os testes unitários sintéticos servem apenas para cobrir falhas do parser;
o workflow `real-historical-corpus.yml` é um **gate adicional com download
real e pelo menos 6.500 jogos distintos**, não substituído por fixtures.

## Fontes, volumes e cadeia de custódia

Fonte original: [PGN Mentor, Players](https://www.pgnmentor.com/files.html),
com catálogo atualizado em julho de 2026. O download automatizado utiliza
cópias **PGN reais sem compactação ZIP** de [rozim/ChessData](https://github.com/rozim/ChessData),
fixadas no commit imutável `aaadf90e11862bc6f207fed407a70e7017df26bd`.
As contagens do espelho podem diferir das do catálogo atualizado; os bytes
foram processados na CI, e os SHA-256 constam do relatório.

| Arquivo | Jogador histórico | Jogos anunciados no catálogo |
| --- | --- | ---: |
| Fischer.zip / Fischer.pgn | Bobby Fischer | 827 |
| Tal.zip / Tal.pgn | Mikhail Tal | 2.431 |
| Karpov.zip / Karpov.pgn | Anatoly Karpov | 3.529 |
| Kasparov.zip / Kasparov.pgn | Garry Kasparov | 2.128 |
| **Total publicado (não verificado ainda)** | | **8.915** |

O comando calcula SHA-256 dos arquivos efetivamente lidos (PGN direto no
espelho ou ZIP original no modo local), registra
contagem recebida, erros de parse, identidade focal no cabeçalho, trajetórias
duplicadas, jogos sem data completa e divisão por **anos civis inteiros**.
A contagem real pode ser menor que a anunciada após rejeições/deduplicação.

### Executar no Debian

```bash
cd ~/Projetos/GitHub/chessCupMotorSimulation
source .venv/bin/activate
mkdir -p "$HOME/chesssim-homologacao/historico-real"
python -m chesssim.historical_real_corpus --fetch \
  --min-ply 17 \
  --out "$HOME/chesssim-homologacao/historico-real/relatorio-01.json"
```

Ou reutilize os quatro ZIPs que você baixou oficialmente, sem rede:

```bash
python -m chesssim.historical_real_corpus \
  --archive-dir "$HOME/PGNs-historicos" \
  --out "$HOME/chesssim-homologacao/historico-real/relatorio-02.json"
```

Os arquivos locais podem ser `Fischer.pgn`/`Tal.pgn`/`Karpov.pgn`/
`Kasparov.pgn` (modo PGN) ou os respectivos `.zip` (modo ZIP). Não use o mesmo nome de arquivo de relatório
duas vezes: a ferramenta recusa sobregravação.

### Análises efetivamente executadas

- Leitura de partidas completas e legalidade pelo parser `python-chess`.
- Identidade por **posição inicial + sequência de lances UCI**,
  deduplicada dentro de cada coleção.
- Por jogador: contagens, anos, compromissos SHA-256 e divisões de
  treino/validação/holdout por anos inteiros.
- Para todos os 6 pares de jogadores: intersecção das **mesmas FENs completas**
  observadas **nas partidas de treino**, após o meio-lance 16;
  contagens de observações e posições em que o lance mais comum difere.
- Duplicatas de trajetórias entre coleções (ex.: um Fischer × Tal
  listado nas duas fontes), reportadas como advertência de dependência.
- Gate real: >=6.500 jogos únicos dentro das respectivas coleções,
  pelo menos 3.500 jogos em anos de treino, quatro fontes e seis pares.

Os PGNs completos e lances individuais não são incluídos no artefato de
CI nem versionados. O relatório contém fontes e hashes, somente contagens
agregadas e compromissos de partição.

### Interpretação científica — fronteira explícita

**Isso não demonstra fidelidade histórica.** A coleta dá a amostra real
e fornece o primeiro diagnóstico de **sobreposição de posições para testes
pareados**, não predições de Rodent, identidade cega ou inferência estatística.
Embora o código calcule partições temporais, todos os ZIPs públicos foram
lidos para construir o inventário; isso **não é um holdout lacrado**.
Os resultados de FEN usam apenas os anos de treino, mas o protocolo
definitivo de lacre/embargo ainda precisa ser estabelecido (#10).

**Acesso a fontes públicas ≠ autorização automática para redistribuir
os arquivos compilados.** Essa distinção não impede a análise local:
o pipeline baixa e usa os dados, sem copiar os PGNs para o repositório.
Não se afirma licença comercial do acervo PGN Mentor. Para conjuntos
com licença explícita de reutilização, os arquivos gerais
[Lichess Open Database](https://database.lichess.org/) são publicados
como CC0, enquanto os broadcasts são CC BY-SA 4.0.

### Próxima etapa científica

Usar os FENs reais compartilhados e a mesma definição temporal para obter
previsões UCI efetivas de Rodent IV: perfis candidatos, baseline genérico,
configuração/profundidade fixas, alternância dos rótulos e análise cega.
Se o número de FENs idênticos for insuficiente, isso é **resultado
metodológico**: aumentar a amostra ou revisar o desenho *antes* de
abrir qualquer teste final, sem inventar evidências.

## Evidência da primeira execução real (runner Python 3.13)

No snapshot fixado de `rozim/ChessData`, a CI processou **8.914 partidas
únicas por coleção** (Fischer 827; Tal 2.431; Karpov 3.528; Kasparov 2.128),
das quais **5.413** ficaram nos grupos cronológicos de treino.
Sobreposição de FENs completos após o meio-lance 16: Fischer × Tal **138**
(34 com lances modais diferentes); Karpov × Kasparov **380** (96 diferentes).
São **observações históricas reais, não previsões de Rodent**. A contagem
Karpov do espelho é uma menor que o catálogo atual; sem diagnóstico de causa.

O arquivo agregado está disponível no artefato do job real da PR #135,
sem redistribuir os PGNs originais.
