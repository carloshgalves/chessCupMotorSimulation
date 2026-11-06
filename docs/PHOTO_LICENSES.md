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

## Lote 2 — fontes e licenças verificadas (06/11/2026)

A segunda leva foi selecionada por ocorrência documentada em torneios oficiais; a inclusão definitiva depende de conferir a licença corrente no Commons durante a materialização. Fotografias registradas como candidatas ainda não estão necessariamente no repositório.

| Jogador | Evento | Fotógrafo/acervo | Licença | Página-fonte |
| --- | --- | --- | --- | --- |
| euwe | Groningen Chess Tournament (1946-08-12) | Anefo / Nationaal Archief | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:Wereldschaaktoernooi._Links_dr._Max_Euwe,_rechts_Michael_Botwinnik,_resp._tweede,_Bestanddeelnr_934-5060.jpg) |
| smyslov | Amsterdam Interzonal Tournament (1964-05-26) | Harry Pot / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:Vasily_Smyslov_1964.jpg) |
| najdorf | World Chess Tournament Amsterdam (1950-12-10) | Carel L. de Vogel / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:Najdorf1950.jpg) |
| timman | IBM International Chess Tournament, Amsterdam (1978-07-15) | Rob Croes / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:IBM_schaaktoernooi_3e_ronde_Timman_aan_zet,_Bestanddeelnr_929-8190.jpg) |
| portisch | IBM International Chess Tournament, Amsterdam (1969-07-24) | Joost Evers / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:PortischIBM1969.jpg) |
| keres | Hoogovens Chess Tournament (1969-01-14) | Eric Koch / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:Hoogoven_Schaaktoernooi_Keres,_Bestanddeelnr_922-0145.jpg) |
| geller | Hoogovens Chess Tournament (1969-01-28) | Jack de Nijs / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:Hoogoven_Schaaktoernooi_Geller_%28links%29_en_Keres,_Bestanddeelnr_922-0477.jpg) |
| bronstein | Chess Olympiad Amsterdam (1954-09-25) | Joop van Bilsen / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:David_Bronstein_in_1954.jpg) |
| gaprindashvili | Hoogovens Women's Chess Tournament (1963-01-21) | Harry Pot / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:Gaprindashvili1963.jpg) |
| chiburdanidze | OHRA Chess Tournament, Amsterdam (1986-07-22) | Bart Molendijk / Anefo | CC0 1.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:Ohra_schaaktoernooi_Maja_Chiburdanidze_tijdens_haar_partij_met_Pieterse,_Bestanddeelnr_933-7153.jpg) |
| carlsen | Tata Steel Chess Tournament (2013-01) | Stefan64 | CC BY-SA 3.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:MagnusCarlsen13c.jpg) |
| nakamura | Tata Steel Chess Tournament (2013-01) | Stefan64 | CC BY-SA 3.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:HikaruNakamura13b.jpg) |
| caruana | Tata Steel Chess Tournament (2013-01) | Stefan64 | CC BY-SA 3.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:FabianoCaruana13.jpg) |
| hou-yifan | Tata Steel Chess Tournament (2013-01) | Stefan64 | CC BY-SA 3.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:HouYifan13b.jpg) |
| ding-liren | Candidates Tournament 2018 (2018-02-24) | Vladimir Barskij / Chess Federation of Russia | CC BY-SA 3.0 | [Arquivo no Commons](https://commons.wikimedia.org/wiki/File:Ding_Liren_2018_Candidates.jpg) |

**Estado de publicação:** cada pack só passa a referenciar a fotografia depois que o importador obtém o JPEG local, confere a licença no Commons e registra SHA-256, fotógrafo, evento, URL e derivação. Usuários das futuras telas História (#48/#49) devem visualizar os créditos e o vínculo de licença.
