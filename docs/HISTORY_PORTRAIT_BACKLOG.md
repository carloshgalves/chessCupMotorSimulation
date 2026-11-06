# Situação consolidada dos retratos — ChessSimulation / issue #47

**Revisão: 06/11/2026 · PR #55 (Draft).**

O elenco possui **64/64 packs biográficos** e **58/64 retratos visuais empacotados**. Dos 58, há fotografias de torneios, retratos históricos e reproduções artísticas corretamente descritas nos metadados, incluindo a gravura de Philidor. A expressão "58 fotografias" seria historicamente imprecisa. Os seis registros abaixo permanecem com `portrait: null`, apresentando fallback na interface.

| Jogador | Estado em 06/11/2026 | Fonte analisada e próximo bloqueio |
| --- | --- | --- |
| Alexander Alekhine | **PENDENTE — 0 JPEG** | [Retrato de 1918 identificado como domínio público](https://commons.wikimedia.org/wiki/File:Alexander_Alekhine.jpg) e [acervo fotográfico da FIDE](https://museum.fide.com/thematic_collections/alexander-alekhine-in-photographs). Necessário arquivo por meio permitido e verificação do direito aplicável no Brasil; não usar o site bloqueado como meio indireto. |
| Bobby Fischer | **PENDENTE — 0 JPEG; fonte CC0 verificada** | [Nationaal Archief, foto de 15/07/1971](https://www.nationaalarchief.nl/onderzoeken/fotocollectie/abc6ac60-d0b4-102d-bcf8-003048976d84), fotógrafo desconhecido/Anefo. Página oficial identifica CC0. O serviço de imagem respondeu 404 (derivado) e 406 (original com Accept JPEG); não existe JPG importado. |
| Frank Marshall | **PENDENTE — 0 JPEG; direitos não avaliados** | [Cleveland Public Library, partida Marshall–Réti, Moscou 1925](https://cplorg.contentdm.oclc.org/digital/collection/p4014coll20/id/108). A ficha possui marca **CNE: Copyright Not Evaluated**, e não PDM/CC0; não importar sem base adicional. |
| Aron Nimzowitsch | **PENDENTE — 0 JPEG; direitos não avaliados** | [Cleveland Public Library, retrato de 1932](https://cplorg.contentdm.oclc.org/digital/collection/p4014coll20/id/75). Classificado **CNE**, sem licença de redistribuição demonstrada. |
| Richard Réti | **PENDENTE — 0 JPEG; direitos não avaliados** | [Cleveland Public Library, partida de Moscou 1925](https://cplorg.contentdm.oclc.org/digital/collection/p4014coll20/id/108). Mesma ficha CNE do registro de Marshall; não duplicar imagem nem tratar localização do rosto como verificada sem inspeção. |
| Gata Kamsky | **PENDENTE — 0 JPEG; licença identificada** | [Imagem do Grenke Open 2016, autor GFHund](https://commons.wikimedia.org/wiki/File:Kamsky,Gata_2016_Karlsruhe.jpeg) publicada como **CC BY-SA 4.0**. Obter arquivo licenciado do titular ou canal de download oficialmente permitido; preservar attribution, licença e indicação da transformação. |

## Direitos, nomenclatura e integridade

- **27** imagens CC0 1.0; **3** CC BY-SA 3.0; **15** CC BY-SA 2.0; **1** CC BY 2.0; **12** com **Public Domain Mark 1.0**: total **58 arquivos JPEG locais** com `sha256` e dados de proveniência.
- **Public Domain Mark (PDM) não é licença nem autorização universal**; a Cleveland utiliza a indicação `NoC-US` para parte do acervo. A situação autoral em jurisdição brasileira deve ser verificada antes de eventual redistribuição pública, especialmente na hipótese de comercialização.
- O manifesto `portrait_candidates.json` registra **59 linhas**: 58 imagens incorporadas mais uma única fonte candidata ainda não materializada (**Fischer**). Outros casos sem licença/arquivo não foram falsamente aprovados no manifesto.
- A gravura de **Philidor, 1772**, veio da coleção Open Access do Metropolitan Museum of Art; `alt` e `derivative_note` identificam expressamente o desenho, não uma fotografia em vida.
- Não usar Google Imagens, agregadores de fotos, APIs bloqueadas, material licenciado apenas para uso editorial ou cópias de procedência incerta como alternativa automática.
- Antes do merge, executar testes, inspecionar visualmente **identidade e recorte dos 58 retratos** em Chrome/Debian, confirmar crédito/alt/license na aba História, verificar o wheel e tratar o aceite de direitos territoriais como gate separado.

**Não fechar #47**: além destes seis retratos, a issue acompanha a homologação de direitos e de apresentação da interface. A PR #55 permanece Draft até o gate humano.
