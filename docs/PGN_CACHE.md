# Cache local de PGNs com proveniência e direitos — issue #62

O ChessCup dispõe do comando `pgn-cache` para preparar partidas históricas **já adquiridas legalmente pelo operador**, sem novos downloads e sem chamadas ao motor. O padrão é `dry-run`: nenhum cache ou manifesto será gravado até fornecer `--apply`.

Esta etapa **não** acessa conjuntos lacrados de treino/validação/teste nem habilita automaticamente um repertório histórico para os motores.

## Pré-voo obrigatório

Crie fora do repositório um arquivo `sources.json` com um catálogo de fontes e os PGNs na mesma pasta (ou em subpastas comuns). Use, por exemplo, esta estrutura; **preencha os hashes reais e confirme os direitos antes de executar**:

~~~json
{
  "schema": "chesssim-pgn-rights-v1",
  "sources": [
    {
      "id": "exemplo_broadcast",
      "file": "partidas-autorizadas.pgn",
      "source_url": "https://database.lichess.org/",
      "license_id": "CC-BY-SA-4.0",
      "license_evidence_url": "https://database.lichess.org/",
      "rights_reviewed_on": "2026-11-11",
      "rights_attestation": "operator_reviewed",
      "expected_sha256": "COLOQUE_AQUI_64_CARACTERES_HEX_SHA256",
      "allowed_uses": ["local_cache", "derived_openings"],
      "attribution": "Crédito e autoria conforme a fonte e o arquivo específico"
    }
  ]
}
~~~

O exemplo **não é autorização automática** para utilizar qualquer arquivo de broadcasts. O operador confirma que o arquivo particular corresponde à licença e ao escopo declarados; o programa verifica a **presença e consistência da declaração**, não a validade jurídica da autorização. Fontes sem licença identificável ou com autorização para outro uso são rejeitadas. No catálogo da Lichess, há distinção entre conjuntos de partidas comuns publicados sob CC0 e transmissões sob CC BY-SA 4.0; devem ser verificados o conjunto e a versão exatos.

Para calcular a impressão digital local sem abrir o arquivo:

~~~bash
sha256sum partidas-autorizadas.pgn
~~~

### Dry-run, sem escrita

~~~bash
python -m chesssim pgn-cache \
  --catalog /caminho/local/sources.json \
  --out /caminho/local/cache-pgn \
  --max-games 200 --max-cache-mb 32
~~~

O pré-voo verifica catálogo, atestação, integridade do PGN e dos movimentos, SHA-256 das fontes, duplicatas de linhas principais, variantes e resultados, número de partidas por fonte, jogadores declarados e tamanho previsto. O orçamento de disco contabiliza os bytes do **PGN derivado e do manifesto JSON juntos**, expostos em `estimated_pgn_bytes`, `estimated_manifest_bytes` e `estimated_cache_bytes`. Não inventa datas ausentes, identificação inequívoca de homônimos, licenças de terceiros ou grau de cobertura histórica.

### Persistir somente mediante autorização

~~~bash
python -m chesssim pgn-cache \
  --catalog /caminho/local/sources.json \
  --out /caminho/local/cache-pgn \
  --max-games 200 --max-cache-mb 32 --apply
~~~

O cache contém `games.pgn`, normalizado sem comentários e variantes secundárias, e `manifest.json`, com os hashes, licenças, créditos, URLs de comprovação e a vinculação dos jogos às fontes. O diretório de destino não pode existir: não há substituição silenciosa nem publicação para GitHub. A escrita é preparada em diretório temporário e validada antes de sua disponibilização.

**Quotas deste incremento:** até 12 arquivos de origem, até 8 MiB por PGN, até 1.000 partidas no total e cache de até 512 MiB, com defaults mais conservadores de 500 partidas e 32 MiB. Essa fatia permite ensaiar 100–200 partidas sem obrigar a baixar bancos mensais com dezenas de gigabytes.

## Segurança e validade

- Nenhum acesso a HTTP/HTTPS é realizado: `source_url` e `license_evidence_url` são registros de proveniência, não endereços para download.
- URLs HTTPS de origem e evidência, atestação explícita do operador, data de revisão não futura e SHA-256 exato são obrigatórios.
- Arquivos e diretórios com symlink, travessia de pasta (`../`), extensão diferente de `.pgn`, PGN não UTF-8, formato acima do limite, tokens SAN ignorados pelo parser, variantes não padrão e lances ilegais são rejeitados.
- Jogos de zero lance são excluídos; jogos com resultado desconhecido permanecem como incompletos. Duplicatas entre fontes preservam a lista de origens; resultados contraditórios da mesma linha são bloqueados.
- A normalização remove anotações, mas **não** dá autorização de redistribuição nem transforma dados particulares em domínio público.
- Este cache não entra no arquivo de treino, não modifica a partição lacrada de Mequinho, não gera resultado de engine nem apresenta validação histórica dos perfis.

## Gates pendentes

A issue #62 permanece aberta até demonstrar com arquivos de 100–200 jogos **efetivamente autorizados** que o operador verificou os direitos, as identidades e períodos, executou a ingestão local, comprovou cache sem rede durante lances UCI e homologou a integração opcional ao repertório com embargos de treino respeitados. CI sintética não comprova licenças reais nem uso em partidas.

Referências gerais para revisão humana:
- https://database.lichess.org/
- https://creativecommons.org/faq/
- docs/THIRD_PARTY.md
