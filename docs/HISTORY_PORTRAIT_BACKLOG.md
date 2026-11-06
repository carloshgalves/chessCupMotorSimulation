# Retratos da Copa dos Imortais — estado consolidado (06/11/2026)

**64 perfis biográficos · 64 imagens locais · nenhum placeholder de retrato.**

A PR #55 incorpora 58 retratos de lotes anteriores e seis imagens documentais adicionais: Alekhine, Fischer, Marshall, Nimzowitsch, Réti e Gata Kamsky. O manifesto `src/chesssim/data/history/portrait_candidates.json` tem 64 registros; os perfis apontam para JPEGs de 480×480 px com SHA-256, crédito, URL de origem, indicação de recorte e informação sobre direitos.

## Seis imagens incorporadas nesta rodada

| Slug | Fonte | Fotógrafo/crédito | Estado de direitos indicado pela fonte |
| --- | --- | --- | --- |
| `alekhine` | [Retrato de 1918](https://commons.wikimedia.org/wiki/File:Alexander_Alekhine.jpg) | Desconhecido | Public Domain Mark 1.0; não equivale a CC0 |
| `fischer` | [Anefo/Nationaal Archief, 1971](https://commons.wikimedia.org/wiki/File:Schaker_Bobby_Fisher_aan_bord_(archief),_Bestanddeelnr_924-7362.jpg) | Desconhecido / Anefo | CC0 1.0 |
| `marshall` | [Retrato de Chess Openings, antes de 1904](https://commons.wikimedia.org/wiki/File:Frank_Marshall_(chess_player).jpg) | Oxford and Dilhoff (crédito da publicação) | Public Domain Mark 1.0; não equivale a CC0 |
| `nimzowitsch` | [Retrato histórico anterior a 1930](https://commons.wikimedia.org/wiki/File:Aron_Nimzowitsch2.jpg) | Desconhecido | Public Domain Mark 1.0; não equivale a CC0 |
| `reti` | [Österreichische Nationalbibliothek, década de 1920](https://commons.wikimedia.org/wiki/File:Richard_R%C3%A9ti_(1889%E2%80%931929)_OeNB_9471305.jpg) | Desconhecido / ÖNB | Domínio público indicado para a União Europeia; PDM não equivale a CC0 |
| `kamsky` | [GFHund, Grenke Open de 2016](https://commons.wikimedia.org/wiki/File:Kamsky,Gata_2016_Karlsruhe.jpeg) | GFHund | CC BY-SA 4.0 (atribuição, link e indicação de transformação obrigatórios) |

As seis imagens foram inspecionadas após incorporação: os retratos exibem os jogadores identificados, sem substituição por imagens geradas. É uma coleção de fotografias históricas e material artístico documental, não uma certificação de estilo ou força do motor.

## Situação de direitos

Inventário global: **28 CC0 1.0; 3 CC BY-SA 3.0; 15 CC BY-SA 2.0; 1 CC BY 2.0; 1 CC BY-SA 4.0; 16 Public Domain Mark 1.0** (64). Public Domain Mark é um rótulo descritivo da página da fonte, e **não uma licença mundial**. A avaliação territorial, inclusive para distribuição pública/comercial no Brasil, deve ser documentada separadamente antes desse uso. As fontes antigas da Cleveland rotuladas CNE foram descartadas, não reclassificadas.

O Código GPL não relicencia as imagens CC BY-SA. A interface deve manter autoria, licença, URL da fonte e aviso de recorte acessíveis, sem sugerir endosso dos jogadores.

## Gate de release

- [x] 64/64 perfis com retrato local e SHA-256.
- [x] Seis alternativas documentais selecionadas sem fontes CNE.
- [x] Inspeção das seis imagens adicionais.
- [ ] CI de qualidade e empacotamento no HEAD final.
- [ ] Conferência final de apresentação desktop/mobile da interface.
- [ ] Análise jurídica específica do uso territorial de PDM antes de redistribuição pública ou comercial.

A PR #55 contém o histórico da curadoria; não confundir fontes candidatas ou estados intermediários anteriores com a versão final.
