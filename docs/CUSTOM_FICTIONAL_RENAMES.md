# #63 — Renomeação auditável de personagens fictícios (Slice 2)

Este incremento prolonga a biblioteca local da #117 com **identidade
permanente e nome mutável por histórico**. Não cria personalidade jogável,
não altera os 64 perfis canônicos, não consulta motores nem redes.

## Operação

Cada identidade continua identificada pelo mesmo `user-<UUID>`. O arquivo
base `user-....json` não é reescrito. Alterar o nome cria um arquivo
imutável numerado `user-....rename.000001.json` (até 64), vinculado
aos hashes da identidade e da revisão anterior. Datas de revisão precisam
ser UTC, não futuras, não anteriores à criação e não regressivas.

Para renomear, consulte o valor `revision_sha256` atual:

```bash
chesssim custom-player-history --library /dados/criador --id user-IDENTIFICADOR
chesssim custom-player-rename \
  --library /dados/criador \
  --id user-IDENTIFICADOR \
  --name "Novo Nome Fictício" \
  --expected-head SHA256_DA_ULTIMA_REVISAO
```

O parâmetro `--expected-head` é **obrigatório** e atua como
comparação otimista: se outra operação alterar o histórico, o comando
falha sem anexar evento. Renomeação nula não gera revisão. Um personagem
desativado precisa ser restaurado explicitamente antes de renomear.
A listagem agora expõe `display_name` projetado, `revision`,
`revision_sha256` e `profile_sha256` do registro original.
`custom-player-history` expõe os nomes anteriores, datas e hashes.

## Exportar/importar

- Sem revisões, exportação continua sendo o arquivo de identidade v1,
  **byte a byte**, preservando consumidores antigos.
- Com revisões, exporta um pacote `chesssim-custom-fictional-portable-v2`,
  contendo identidade original + histórico completo e checksum do
  conjunto. O checksum protege integridade acidental e detecção de
  divergências; **não autentica autor ou fonte**.
- Importação válida verifica **todo** o pacote antes de publicar,
  aceita o mesmo prefixo já instalado, anexa apenas o sufixo novo e
  recusa bifurcações de mesma identidade. Importar versão mais antiga
  não desfaz revisões locais existentes. Não promove o cadastro a UCI.
- Falha durante importação de múltiplos eventos pode publicar um
  **prefixo íntegro, porém incompleto**: não é transação multi-arquivo.
  Reimportar o mesmo pacote pode continuar do prefixo. Não há
  substituição nem rollback silencioso do histórico publicado.
- A decisão local de desativação **não é parte do pacote portátil**;
  importá-lo em uma segunda biblioteca não transfere esse marcador.
  Ao importar na mesma biblioteca, o marcador continua válido porque
  vincula-se ao arquivo original, não ao nome atual.

A escrita utiliza o lock cooperativo da biblioteca e publicação
exclusiva já introduzidos na Slice 1. Arquivos com numeração inesperada,
lacunas, symlinks ou histórico corrupto são rejeitados antes de
mostrar nomes, importar sufixos ou renomear.

## Limitações

A cadeia SHA-256 é verificável, **mas não é assinada**: um atacante
com permissão de escrever no diretório pode reescrever os eventos e
recalcular os hashes. O lock também não é autorização, isolamento de
processos ou defesa contra escritores fora da aplicação. Não foi
homologada a durabilidade física perante queda de energia.

Esta Slice 2 não produz perfil de pessoa real, pesquisa por nome,
integração Arena, calibração Rodent ou fidelidade histórica.
A #63 permanece aberta para esses incrementos; ensaios de arquivos e
filesystem reais continuam `NOT_RUN` na #83.
