# Decisão de licença e racional econômico

**Projeto:** ChessCup Motor Simulation  
**Decisão:** `GPL-3.0-or-later` para o **código próprio**, a partir da integração desta alteração.  
**Data:** 2026-11-04.

## Premissa técnica

O pacote Python declara `chess>=1.11,<2` como dependência obrigatória. O projeto `python-chess` licencia essa biblioteca sob GPL versão 3 ou posterior: https://pypi.org/project/chess/ .

O código do ChessCup integra essa biblioteca no mesmo processo Python. A distribuição de uma aplicação combinada deve respeitar as condições aplicáveis da GPL. Licenciar apenas nosso código sob MIT ou Apache-2.0 não elimina essas condições. O fato de Rodent IV e Stockfish serem normalmente executados como **programas separados** deve ser avaliado de acordo com a arquitetura e a distribuição efetiva; esta decisão não os relicencia.

## Alternativas comparadas

| Licença do código próprio | Vantagens | Limitações neste projeto | Taxa de licença |
|---|---|---|---|
| **GPL-3.0-or-later** (selecionada) | Compatibilidade direta com a dependência obrigatória; mesma família de licenças; permite serviços e distribuição comercial; versões futuras da GPL opcionais | Ao redistribuir a aplicação combinada, cumprir obrigações de fonte, avisos e copyleft | **R$ 0** |
| GPL-3.0-only | Compatibilidade direta, condições mais estáveis | Menos flexibilidade futura que a opção `-or-later` | R$ 0 |
| MIT | Simplicidade e reutilização permissiva de **nosso** código | A aplicação combinada distribuída com `python-chess` ainda segue as obrigações GPL; sinalização isolada pode confundir | R$ 0 |
| Apache-2.0 | Permissiva e inclui cláusulas explícitas de patentes | Obrigações de avisos adicionais; a distribuição combinada continua sujeita à GPL | R$ 0 |
| LGPL-3.0-or-later | Permite mais formas de reutilização da nossa parte como biblioteca | Não torna o conjunto combinado com `python-chess` LGPL nem proprietário | R$ 0 |
| AGPL-3.0-or-later | Exige oferta de código em determinadas interações pela rede, quando aplicável | Acrescenta obrigações a um eventual serviço online; combinação com GPL precisa de cuidado específico | R$ 0 |
| Proprietária | Restringe a concessão de direitos ao código próprio | Exige outro desenho ou outra biblioteca com direitos compatíveis antes de distribuir o programa combinado como proprietário | Variável; provável custo adicional |

**Critério de menor custo:** nenhuma dessas licenças cobra royalties pelo mero uso. A GPL-3.0-or-later evita adaptações arquiteturais e minimiza ambiguidades administrativas para a distribuição atual. Isso não torna a conformidade gratuita: distribuir cópias exige atender às condições das licenças aplicáveis.

## Limites

- O código próprio é licenciado sob a GPLv3 ou qualquer versão posterior, conforme aviso do projeto e arquivo `LICENSE`.
- Arquivos de terceiros **não são relicenciados**. Os termos dos respectivos detentores continuam valendo para `python-chess`, engines, perfis, livros, redes e corpora.
- A execução interna e a oferta de funcionalidades remotas não são, por si só, distribuição de cópias nos termos da GPL. A AGPL estabelece obrigações de rede distintas.
- É possível comercializar software GPL (por exemplo, venda de cópias, suporte, hospedagem). Não é possível prometer exclusividade sobre direitos GPL já concedidos a destinatários anteriores.
- Se houver contribuições de terceiros sem autorização para relicenciamento, é necessário verificar seus direitos antes de declarar todo o código como próprio.
- Esta decisão não é parecer jurídico; distribuições comerciais específicas merecem avaliação de conformidade.

## Fontes

- [Licença e informações do python-chess](https://pypi.org/project/chess/)
- [Perguntas frequentes da GNU sobre GPL e combinações](https://www.gnu.org/licenses/gpl-faq.html)
- [Licenças e compatibilidades GNU](https://www.gnu.org/licenses/license-list.html)
- [Orientação GNU sobre escolha da licença](https://www.gnu.org/licenses/license-recommendations.html)
- [Migração de metadados PEP 639 no setuptools](https://setuptools.pypa.io/en/stable/userguide/license_migration.html)
