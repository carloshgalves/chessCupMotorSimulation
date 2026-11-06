# Packs biográficos da aba História — issue #47

## Objetivo

Uma biografia por jogador do elenco, editada como dados e acessada por um **slug canônico**. O código do carregador é único; acrescentar um jogador consiste em criar `src/chesssim/data/history/<slug>.json` e preencher os mesmos campos. O nome e o grupo **não** são copiados: vêm de `roster.json`.

Nesta primeira entrega existem **21 packs para as personalidades históricas com alias oficial do Rodent IV**. Os demais **43** integrantes do elenco retornam um registro completo e pesquisável com `has_historical_pack=false`, `review_status=pending`, nenhuma conquista e foto ausente. Um jogador sem motor instalado continua pesquisável e pode receber um pack no futuro. O jogador `mequinho` permanece no elenco e pode ganhar um pack independente quando houver curadoria específica.

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
