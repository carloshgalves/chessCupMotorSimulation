# Auditoria de higiene Git — issue #7

**Objetivo:** impedir a inclusão de artefatos locais, dados privados e credenciais reconhecíveis no repositório; oferecer uma auditoria retroativa do histórico Git com evidências redigidas e reproduzíveis.

## Gate preventivo

O job **git-hygiene** executa em pushes e pull requests:

~~~bash
python3 scripts/audit_git_artifacts.py --scope tracked
~~~

Ele inspeciona os **blobs do índice Git** (git ls-files --stage + git cat-file --batch), independentemente de alterações posteriores na cópia de trabalho. Falha com status 1 para caminhos de PGNs/PDFs/arquivos compactados/executáveis, .env exceto .env.example, diretórios locais de execução, identidades SSH, symlinks versionados, formatos binários suspeitos, blobs acima de 3 MiB e assinaturas reconhecidas de credenciais.

Os 64 retratos JPEG existentes são permitidos apenas em src/chesssim/data/history/portraits e com cabeçalho JPEG. **Essa exceção não comprova direitos de imagem ou licença comercial:** o gate jurídico das issues #47 e #53 continua separado.

## Auditoria retroativa de objetos alcançáveis

A execução completa é opt-in:

~~~bash
git fetch --all --tags
python3 scripts/audit_git_artifacts.py --scope history --output "$HOME/git-history-audit.json"
~~~

A ferramenta percorre todos os objetos de git rev-list --objects --all e nomes históricos de git log --all, registrando quantos commits, blobs e paths examinou. A saída JSON contém somente **código da regra, hash de identificação do path e prefixo de OID**, jamais o conteúdo de uma credencial nem o nome original do arquivo. Lista até 120 achados, preservando a contagem total e a sinalização de truncamento.

No GitHub, depois de mesclada a PR, o mantenedor pode executar **Actions → Python quality gates → Run workflow**. O job history-hygiene usa checkout com fetch-depth 0 e publica um relatório JSON redigido com retenção de sete dias, inclusive em caso de findings. Como é uma verificação retroativa, é **separada do gate preventivo** de cada PR.

### Limites de cobertura

A ferramenta identifica objetos ainda alcançáveis pelas refs do clone. Não inspeciona objetos já descartados pelo Git, reflogs externos, forks, arquivos de releases, caches de provedores ou conteúdo de outra máquina. Uma CI verde da versão atual não significa que todo o histórico foi auditado. O scanner por assinaturas é conservador e não detecta todas as formas de segredo, dados privados e licenças restritas; não substitui revisão humana nem ferramentas especializadas de detecção.

Na presença de finding, **não** apagar ou reescrever o histórico automaticamente. Primeiro validar o achado em ambiente autorizado e tomar uma decisão explícita sobre rotação de credenciais, remediação e comunicação.

## Códigos do relatório

| Código | Interpretação |
| --- | --- |
| environment_secret_file, local_identity_file | Arquivos locais de credenciais |
| prohibited_payload_extension, local_artifact_dir | Dataset/PGN, motor, executável, relatório de execução |
| private_key, github_token, github_fine_grained_token, aws_access_key, google_api_key, openai_api_key, slack_token | Assinatura reconhecível de segredo |
| absolute_home_path, personal_directory_path | Caminhos privados a examinar |
| unexpected_binary_payload, portrait_not_jpeg, oversized_blob, tracked_symlink | Conteúdo incompatível com a política |

## Evidência dos testes

Testes adversariais usam repos Git locais descartáveis e cobrem conteúdo diferente entre índice e working tree, credenciais em blobs deletados, redaction do relatório, extensão proibida, conteúdo binário disfarçado, JPG inválido, symlink e falha fechada no caso de índice com conflito ou repositório indisponível.

**Estado desta entrega:** o gate preventivo e a ferramenta histórica podem ser validados pela CI. A issue #7 permanece aberta até que a auditoria de todas as refs relevantes seja realmente executada, seus achados triados e a decisão de remediação registrada.
