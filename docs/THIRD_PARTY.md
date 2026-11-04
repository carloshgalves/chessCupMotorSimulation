# Inventário de terceiros e política de distribuição

**Revisão documental:** 2026-11-04  
**Escopo:** dependências declaradas, engines externos, fontes PGN e artefatos mantidos neste repositório.

Este documento registra a situação observável das fontes consultadas. Não é parecer jurídico e não substitui a verificação dos termos aplicáveis à versão exata de cada arquivo ou distribuição.

## Resumo executivo

| Componente ou fonte | Situação identificada | Regra operacional |
|---|---|---|
| Código próprio do ChessCup Motor Simulation | GPL-3.0-or-later (GPLv3 ou posterior) | Arquivos LICENSE, NOTICE.md e metadados PEP 639; cumprir copyleft ao redistribuir a aplicação combinada |
| `chess` / `python-chess` | GPL-3.0-or-later | Dependência instalada separadamente; revisar compatibilidade antes de distribuir o aplicativo |
| Rodent IV | Repositório upstream sob GPL-3.0 | Obter o motor separadamente; não incluir executável |
| Stockfish | GPL-3.0 | Obter o motor separadamente; não incluir executável ou rede NNUE |
| PGN Mentor | Arquivos disponíveis para download; licença de dados/redistribuição não identificada nas páginas consultadas | Uso local de pesquisa; não republicar os arquivos ou o corpus |
| Outros corpora PGN | Dependem da fonte e dos termos de cada conjunto | Registrar proveniência e direitos antes de qualquer publicação |

## 1. Código próprio

O **código próprio** foi licenciado sob **GPL-3.0-or-later**, com texto integral em [LICENSE](../LICENSE), aviso em [NOTICE.md](../NOTICE.md) e expressão SPDX em `project.license` no `pyproject.toml`. O campo `project.license-files` inclui os dois documentos nas distribuições. A decisão e alternativas econômicas constam em [LICENSE_DECISION.md](LICENSE_DECISION.md).

Esse licenciamento vale apenas para o código próprio de que o projeto possui direitos; **não relicencia** `python-chess`, Rodent IV, Stockfish, livros Polyglot, redes NNUE nem bases PGN. Ao redistribuir uma aplicação combinada com `python-chess`, devem ser atendidas as obrigações aplicáveis da GPL. Usar o software internamente ou operar um serviço em rede, sem distribuir cópias, não gera automaticamente as obrigações de disponibilização de código que a AGPL criaria.

## 2. Dependências Python declaradas

O inventário abaixo cobre as dependências diretas declaradas em `pyproject.toml`. As restrições são intervalos declarados, não versões exatas fixadas.

| Camada | Distribuição | Restrição declarada | Finalidade | Licença upstream identificada |
|---|---|---:|---|---|
| Runtime | `chess` (projeto `python-chess`) | `>=1.11,<2` | Regras, geração/validação de lances, PGN e comunicação UCI | GPL-3.0-or-later |
| Build | `setuptools` | `>=77` | Backend de empacotamento | MIT |
| Desenvolvimento | `pytest` | `>=8` | Testes | MIT |
| Desenvolvimento | `build` | `>=1.2` | Geração de wheel e sdist | MIT |
| Desenvolvimento | `ruff` | `>=0.11` | Análise estática | MIT |

Fontes primárias:

- [python-chess no PyPI](https://pypi.org/project/chess/) e [repositório upstream](https://github.com/niklasf/python-chess);
- [setuptools no PyPI](https://pypi.org/project/setuptools/);
- [pytest no PyPI](https://pypi.org/project/pytest/);
- [build no PyPI](https://pypi.org/project/build/);
- [Ruff no PyPI](https://pypi.org/project/ruff/).

A dependência `chess` é obrigatória em runtime. Sua licença não deve ser tratada como um detalhe exclusivo de desenvolvimento. Antes de publicar um aplicativo, instalador ou pacote que a inclua ou combine com o código do projeto, revisar a compatibilidade e as obrigações da licença aplicável. Este inventário não conclui que a licença do código próprio tenha sido automaticamente definida pela licença de uma dependência.

**Inventário resolvido:** esta tabela, isoladamente, não substitui o SBOM. `scripts/generate_sbom.py` resolve e instala as dependências em dois ambientes virtuais novos, valida a correspondência entre pacotes instalados e componentes do CycloneDX 1.6 e salva `runtime.cdx.json`, `development.cdx.json`, `*.packages.json`, `*.inspect.json`, `*.resolved.txt` e `manifest.json` com hashes. O ambiente de desenvolvimento inclui também o backend de build declarado. O SBOM é gerado no CI em Python 3.13 e retido como artefato por 7 dias, sem outro job. Para outras plataformas, outras versões do Python e o release definitivo, executar novamente. Componentes externos (engines, livros, redes, PGNs, bibliotecas do sistema) requerem inventários separados. Não presumir que todos os pacotes possuem metadados de licença completos.

## 3. Engines e recursos externos

### 3.1. Rodent IV

- Projeto upstream: [nescitus/rodent-iv](https://github.com/nescitus/rodent-iv).
- A revisão fixada pelo código em `src/chesssim/rodent.py` é `e8d84c8c8c189a1cf4eb27c578fc573af3b916d2`.
- O arquivo [LICENSE dessa revisão](https://github.com/nescitus/rodent-iv/blob/e8d84c8c8c189a1cf4eb27c578fc573af3b916d2/LICENSE) contém a GNU GPL versão 3.
- O comando `setup` pode obter arquivos de personalidade e livros Polyglot dessa revisão. Ele **não baixa nem instala o executável** do motor.

O usuário deve obter e configurar o executável Rodent IV separadamente. O repositório não deve incorporar o executável.

A licença do repositório upstream não substitui uma verificação de proveniência por arquivo. Em particular, não presumir que cada livro, base ou outro recurso binário possui os mesmos titulares e condições do código-fonte. Perfis e livros obtidos externamente permanecem fora dos commits e releases até que sua origem e autorização de redistribuição estejam verificadas.

### 3.2. Stockfish

- Projeto upstream: [official-stockfish/Stockfish](https://github.com/official-stockfish/Stockfish).
- O commit upstream consultado nesta revisão é `ff7cee4244ec96ea488c402669e3674c0574dce6`.
- O arquivo [Copying.txt dessa revisão](https://github.com/official-stockfish/Stockfish/blob/ff7cee4244ec96ea488c402669e3674c0574dce6/Copying.txt) contém a GNU GPL versão 3.
- O manual de uso deste projeto solicita uma instalação externa e usa o executável indicado pelo usuário para análises independentes.

Não incluir executáveis Stockfish, bibliotecas compiladas ou arquivos de rede NNUE nos commits ou releases. Se uma distribuição futura passar a incluir Stockfish ou componentes derivados, revisar a versão exata, os avisos, o código-fonte correspondente e as demais obrigações da GPL antes da publicação.

## 4. Corpora e fontes de partidas

### 4.1. PGN Mentor

- Índice consultado pelo código: [PGN Mentor — Download PGN Files](https://www.pgnmentor.com/files.html).
- A página informa que os arquivos estão disponíveis para download gratuitamente.
- O rodapé exibe `Copyright © 2025 64 Squares`; a página não apresenta uma licença aberta dos dados nem autorização expressa para republicação ou redistribuição.

Disponibilidade para download não será interpretada como domínio público ou autorização de redistribuição. A política do projeto é manter esses arquivos apenas no ambiente local de pesquisa e não incorporá-los ao repositório, a wheels, a releases ou a serviços de download do projeto.

O fluxo `corpus-fetch` registra URL de origem e hashes do arquivo compactado e do PGN baixado em um arquivo `.source.json`. O fluxo `corpus-build` registra hashes e arquivos-fonte no manifesto do corpus. Esses registros auxiliam a auditoria, mas **não concedem direitos de uso ou redistribuição**.

### 4.2. Outras fontes PGN

Cada fonte adicional deve ser analisada individualmente. Antes de utilizar ou publicar um corpus, registrar:

1. nome e URL da fonte;
2. data de obtenção;
3. licença, termos de uso ou autorização aplicável;
4. restrições de uso, modificação e redistribuição;
5. hash do arquivo original e do corpus processado;
6. transformações realizadas e vínculo entre registros e fontes.

Quando a licença estiver ausente, ambígua ou não cobrir redistribuição, manter o conjunto local e não publicá-lo. Não inferir autorização a partir de acesso público, gratuidade, formato PGN ou possibilidade técnica de download.

## 5. Política de artefatos do repositório

Não versionar, salvo decisão documentada e autorização verificável:

- executáveis ou bibliotecas de engines (Rodent IV, Stockfish ou alternativas);
- redes NNUE, livros Polyglot, bases de abertura e outros recursos binários de terceiros;
- arquivos PGN, arquivos compactados de corpora ou bancos históricos locais;
- perfis calibrados/derivados e outros artefatos locais de execução;
- relatórios de pesquisa que contenham dados cuja publicação não esteja autorizada;
- credenciais, tokens, chaves ou arquivos de configuração com segredos.

Os diretórios de datasets, calibrações, partidas, auditorias, benchmarks, relatórios e execuções permanecem locais e ignorados pelo Git. Extensões comuns de executáveis, bibliotecas, redes, arquivos compactados e PGNs também são ignoradas. Uma regra de ignore não substitui a revisão do diff: arquivos não autorizados não devem ser adicionados com `git add -f`.

## 6. Checklist antes de uma distribuição

- [ ] Confirmar que o release publica a GPL-3.0-or-later, LICENSE, NOTICE.md e o código-fonte correspondente.
- [ ] A compatibilidade da licença escolhida com `python-chess` e demais dependências foi revisada.
- [ ] O SBOM CycloneDX foi regenerado para as dependências realmente resolvidas da plataforma e versão Python do release; licenças desconhecidas revisadas.
- [ ] A versão e o hash de cada engine externa utilizada foram registrados.
- [ ] Perfis, livros, redes e outros recursos externos foram verificados individualmente.
- [ ] Cada corpus possui fonte, data, hash e situação de direitos documentados.
- [ ] Nenhum executável, corpus ou artefato sem autorização entrou no commit, wheel, sdist ou release.
- [ ] As obrigações de código-fonte e avisos das licenças aplicáveis foram cumpridas quando houver redistribuição.

## 7. Referências e limites da auditoria

A revisão considerou o `pyproject.toml`, o código que identifica fontes externas, a árvore de arquivos versionados e as páginas/licenças upstream listadas acima, em 2026-11-04; a geração dinâmica de SBOM foi acrescentada na mesma data. Ela não inspeciona instalações locais, arquivos PGN que o usuário tenha obtido, engines presentes fora do repositório, nem todas as dependências transitivas de um ambiente específico.

Revalidar este inventário antes de cada release e sempre que uma dependência, engine, fonte PGN ou política de distribuição mudar.
