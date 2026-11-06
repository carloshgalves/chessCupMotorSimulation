# Fotografias históricas — licença, procedência e reprodução

**Política:** somente retratos autênticos produzidos em eventos de xadrez oficiais. Nunca presumir que uma foto de assessoria ou publicada por uma federação seja automaticamente reutilizável. Cada arquivo é associado a uma licença livre explícita na página de origem do Wikimedia Commons e é usado como **registro histórico**, sem alegação de patrocínio ou endosso do enxadrista.

## Lote 1 — fontes auditadas

| Jogador | Evento documentado | Fotógrafo/acervo | Licença livre | Fonte original |
| --- | --- | --- | --- | --- |
| Henrique Mecking | Hoogovens, 1978 | Koen Suyk / Anefo, Nationaal Archief | CC0 1.0 | https://commons.wikimedia.org/wiki/File:Henrique_Mecking_1978.jpg |
| Bent Larsen | Hoogovens, 1961 | Harry Pot / Anefo, Nationaal Archief | CC0 1.0 | https://commons.wikimedia.org/wiki/File:BentLarsen.jpg |
| Boris Spassky | Interpolis, Tilburg, 1980 | Hans van Dijk / Anefo, Nationaal Archief | CC0 1.0 | https://commons.wikimedia.org/wiki/File:Interpolis_schaaktoernooi_in_Tilburg,_B_Spassky,_Bestanddeelnr_931-0359.jpg |
| Tigran Petrosian | IBM International, Amsterdã, 1973 | Rob Croes / Anefo, Nationaal Archief | CC0 1.0 | https://commons.wikimedia.org/wiki/File:Eerste_ronde_IBM-schaaktoernooi,_Petrosjan,_Bestanddeelnr_926-5520.jpg |
| Anatoly Karpov | OHRA, Amsterdã, 1985 | Rob Bogaerts / Anefo, Nationaal Archief | CC0 1.0 | https://commons.wikimedia.org/wiki/File:Ohra_schaaktoernooi_Karpov,_Bestanddeelnr_933-3781.jpg |
| Viswanathan Anand | Corus, Wijk aan Zee, 2010 | Stefan64 | CC BY-SA 3.0 | https://commons.wikimedia.org/wiki/File:VAnand10.jpg |
| Veselin Topalov | Tata Steel, Wijk aan Zee, 2012 | Stefan64 | CC BY-SA 3.0 | https://commons.wikimedia.org/wiki/File:VeselinTopalov12a.jpg |

**Textos integrais das licenças:** [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) e [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).

Os JPEGs são **derivados fotográficos com recorte quadrado e redução a 480×480 px**, executados por script exclusivamente de importação. Cada pack traz `source_url`, `author`, `rights_holder`, `event`, `event_date`, `license`, `license_url`, `derivative_note`, `verified_at`, `approval_status` e `sha256`. Os arquivos derivados de CC BY-SA 3.0 permanecem sob a mesma CC BY-SA 3.0. A GPL do código-fonte **não relicencia as fotografias**.

**Atribuição na interface:** ao exibir fotos, oferecer crédito visível ou acessível (autor, fonte, licença/link e indicação do recorte) sem sugerir endosso. Para CC0, crédito é preservado como prática de procedência. O usuário não deve copiar a foto isolada e omitir os metadados CC BY-SA.

## Materialização reprodutível

- Metadados auditados: `src/chesssim/data/history/portrait_candidates.json`.
- Importador offline por padrão (sem `--apply`): `python scripts/materialize_history_portraits.py`.
- Importação explícita em máquina conectada: instalar `Pillow>=11,<13` e executar `python scripts/materialize_history_portraits.py --apply`.
- Antes de baixar, o importador consulta os dados atuais de licença do Commons, verifica se permanecem compatíveis e rejeita host de imagem inesperado. Faz download e recorte **somente após** validação da fonte. O runtime ChessSim não acessa a internet.
- **Endpoint de imagem no serviço Arena:** `GET /api/history/portraits/{slug}.jpg`, disponível somente após o arquivo local validado existir; não aceita caminho externo.
- Os outros jogadores continuam sem foto, utilizando placeholder até identificação de imagem liberada.

**Direito de imagem e contexto:** mesmo material CC0 não autoriza sugerir que o jogador endossou o programa. Os packs são recriações biográficas em primeira pessoa e não são depoimentos ou dublagens reais. Para contextos promocionais ou não editoriais, realizar avaliação jurídica própria. Imagens são removíveis por slug; recurso sem hash/licença válida deve ser rejeitado.
