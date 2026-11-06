# Packs biográficos da aba História — issue #47

## Objetivo

Uma biografia por jogador do elenco, editada como dados e acessada por um **slug canônico**. O código do carregador é único; acrescentar um jogador consiste em criar `src/chesssim/data/history/<slug>.json` e preencher os mesmos campos. O nome e o grupo **não** são copiados: vêm de `roster.json`.

Nesta primeira entrega existem **22 packs: 21 personalidades com alias oficial do Rodent IV e Mequinho, cujo perfil experimental é tratado separadamente**. Os demais **42** integrantes do elenco retornam um registro completo e pesquisável com `has_historical_pack=false`, `review_status=pending`, nenhuma conquista e foto ausente. Um jogador sem motor instalado continua pesquisável e pode receber um pack no futuro. Mequinho possui um pack próprio por sua importância no projeto, **sem** que isso o transforme em uma das 21 personalidades oficiais do Rodent IV ou valide o estilo do modelo.

## Voz narrativa

`narrative_voice=first_person_editorial` significa **texto original, editorial, inspirado em acontecimentos históricos**, escrito em primeira pessoa: “Conquistei...”, “Disputei...”, “Fui...”. **Não é uma fala documentada, citação, gravação ou autorização do enxadrista.** A interface deve expor este aviso de modo legível; não sugerir que pessoas falecidas ou vivas falaram efetivamente esse texto.

Os campos `short_bio`, `highlights[*].text` e `timeline[*].summary` usam a primeira pessoa e são validados. Os dados não devem ser utilizados como fala real ou para treinamento não autorizado de voz. Os anos e as idades só entram quando apoiados pelas fontes do feito; jamais inferir títulos de GM antes da criação do sistema de títulos da FIDE.

## Contrato instanciável

Cada arquivo JSON tem exatamente os campos:

```json
{
  "schema": "history-profile-v1",
  "slug": "fischer",
  "review_status": "reviewed",
  "narrative_voice": "first_person_editorial",
  "short_bio": "Fui campeão dos Estados Unidos ainda adolescente e, depois, conquistei a coroa mundial.",
  "era_label": "1957–1972",
  "portrait": null,
  "highlights": [
    {
      "id": "achievement-1",
      "year": 1958,
      "text": "Tornei-me grande mestre e me classifiquei para o ciclo de Candidatos aos 15 anos.",
      "source_ids": ["source-1"]
    }
  ],
  "timeline": [
    {
      "year": 1958,
      "title": "Classificação para Candidatos",
      "summary": "Tornei-me grande mestre e me classifiquei para o ciclo de Candidatos aos 15 anos.",
      "source_ids": ["source-1"]
    }
  ],
  "sources": [
    {
      "id": "source-1",
      "title": "Biografia de Bobby Fischer",
      "publisher": "Chess.com",
      "url": "https://www.chess.com/article/view/biography-of-bobby-fischer",
      "accessed_at": "2026-11-05"
    }
  ]
}
```

O exemplo é ilustrativo; consulte `history/fischer.json` para o pack real. A presença de links editoriais não incorpora conteúdo protegido, reproduz apenas **fatos resumidos em redação própria**. `reviewed` denota conferência documental inicial dos feitos publicados e referências, **não** auditoria histórica independente. A PR requer revisão editorial independente antes de tratar essa condição como homologada.

O retrato fica `null` até haver licença válida. Quando aprovado, usar `local_asset` (sob `history/portraits/`), `alt`, `author`, `license`, `source_url` HTTPS e `verified_at`. Nunca utilizar screenshots ou imagens do Play Magnus; nem URLs externas no runtime para preencher campos vazios. O código rejeita caminhos arbitrários.

## API proposta e implantada na Arena local

- `GET /api/history/players` → `{"schema":"history-index-v1","players":[...]}`; contém **64** entradas, status e resumo da biografia.
- `GET /api/history/players/{slug}` → um único pack validado com identidade e indicações explícitas de histórico versus modelo; `404` para slug desconhecido/errado.
- Falhas de leitura ou contrato → `503` genérico, nunca conteúdo enganoso; `?query` e rotas com caminhos arbitrários são rejeitadas.
- Somente GET, sem executar motores, alterar torneios, expor arquivos ou alterar `watch`. A UI de carrossel e perfil é assunto das issues #48–#49.

## Qualidade e evolução

O contrato exige metadados e referências para cada feito, cronologia ordenada, esquema conhecido e URLs HTTPS. Testes verificam os 64 slugs, 21 packs preenchidos e o fallback dos demais, segurança de dados, primeira pessoa e os endpoints. Preservar `historically_validated_model=false` independente de relatos sobre a carreira real.

Para inserir novo personagem: criar JSON no padrão, manter `slug` no elenco, preencher fontes verificáveis, validar com `pytest tests/test_history_profiles.py`, e submeter à revisão. Nenhum retrato pode ser incorporado antes da auditoria de licença.


## Nota de verificação — Henrique Mecking

A cronologia foi conferida antes da publicação: campeão brasileiro aos **13 anos** (1965), requisitos de grande mestre aos **19** (janeiro de 1972; formalização pela FIDE posteriormente), vencedor do Interzonal de Petrópolis aos **21** (1973) e participante dos Candidatos aos **22** (1974); venceu também Manila em 1976. A narrativa não usa 15/16/21 para os três primeiros marcos, pois essas idades misturariam eventos distintos. O pack referencia CEX, Xadrez M.A.T. e Chess.com; a voz permanece editorial.


## Correções documentais da revisão da PR #54

- **Fischer:** o torneio de 1957/58 terminou em **10 de janeiro de 1958** (Fischer aos 14 anos); `year=1958`.
- **Alekhine:** reinados separados por Euwe: **1927–1935 e 1937–1946** (FIDE Chess Museum).
- **Botvinnik:** reinados **1948–1957, 1958–1960, 1961–1963** (FIDE Chess Museum).
- **Anand:** distinguir campeão mundial FIDE **2000–2002** do título unificado **2007–2013**.
- **Nimzowitsch:** *Mein System* teve fascículos em **1925–1926** e edição reunida em **1927** (Edward Winter/Chess Notes).
- **Mequinho:** brasileiro absoluto de **1965** confirmado por tabela de evento (BrasilBase), título de GM conquistado aos **19 anos** em 13/01/1972, com reconhecimento FIDE posterior (Folha). O Chess.com menciona 1966/1967, registro conflitante que **não** elimina o campeonato de 1965.

A presença de `review_status=reviewed` representa revisão de coerência de fontes e dados históricos do pack, **não** afirma autenticação de voz, endosso do atleta ou fidelidade da personalidade de motor. A trilha documental adicional está em `docs/HISTORY_FACT_CHECK.md`.

## Fotografias com licenças livres

O primeiro lote seleciona sete fotos feitas em competições oficiais, cada uma sob CC0 1.0 ou CC BY-SA 3.0. Fontes, fotógrafos, licenças e condições de crédito constam em `docs/PHOTO_LICENSES.md`. A materialização ocorre em passo explícito, com verificação da licença e hash local; as fotos não são baixadas pelo ChessSim durante a execução. Os demais perfis permanecem com `portrait=null`.

Endpoint para o carrossel futuro: `GET /api/history/portraits/{slug}.jpg`, apenas para arquivo local validado.

## Segundo lote de packs (issue #47)

Foi adicionada uma camada de **42 novos packs** (total de 64 slugs canônicos com ficha), cada um seguindo o contrato `history-profile-v1` e com no mínimo dois marcos datados e fontes específicas. Eles são publicados inicialmente como `review_status=pending`, porque a verificação editorial independente de cada assertiva permanece necessária. Isso não impede leitura pela API, mas exige indicativo de curadoria não finalizada na interface. Os 22 packs da entrega inicial conservam seu status de revisão.

Para as fotografias, a segunda leva possui **15 candidatas** de competições documentadas e licenças livres. Informações de autoria, torneio, data e URLs estão em `docs/PHOTO_LICENSES.md`; os arquivos só são efetivamente liberados quando o importador verifica as licenças e os hashes. A ampliação não introduz novas dependências de runtime, endpoints ou mudanças de schema.

## Estado consolidado da PR #55

**64/64 packs** em primeira pessoa editorial: 22 revisados na entrega inicial e 42 novos identificados como `pending` para revisão factual. **13/64 retratos** registrados e integralmente distribuíveis; outros **51** ainda sem foto. Desses, nove têm fonte candidata rastreada no Commons, mas foram bloqueados para download pelo provedor. Mais informações no registro `docs/PHOTO_LICENSES.md`.

A integridade é validada para cada `portrait` presente, enquanto os demais usam fallback nulo. A issue #47 permanece aberta para auditoria histórica, conclusão das fotos e atribuição visual nas #48/#49.
