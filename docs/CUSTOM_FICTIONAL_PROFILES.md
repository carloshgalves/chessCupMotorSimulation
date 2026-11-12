# #63 — Biblioteca local de personagens fictícios (Slice 1)

Esta fatia implementa um cadastro de identidades fictícias separado do elenco
histórico imutável de 64 jogadores. Trata-se de metadado experimental,
não de perfil UCI jogável. Não contém executável, personalidade Rodent,
PGN, abertura, calibração, rating ou alegação de fidelidade histórica.

## Comandos

    chesssim custom-player-create --library /dados/criador --name "Ava Ravenwood"
    chesssim custom-player-list --library /dados/criador
    chesssim custom-player-export --library /dados/criador --id user-<32hex> --out /dados/portable.json
    chesssim custom-player-import --library /dados/outra --file /dados/portable.json
    chesssim custom-player-retire --library /dados/criador --id user-<32hex>
    chesssim custom-player-restore --library /dados/criador --id user-<32hex>

Os IDs são gerados automaticamente com prefixo user- e UUID aleatório.
Dois nomes idênticos são identidades distintas. Um nome pode coincidir
com um membro do elenco histórico, mas não modifica roster.json.
Cada entrada tem kind fictional-player, identity_basis
OPERATOR_DECLARED_FICTION, status METADATA_ONLY_NOT_PLAYABLE,
uci_eligible=false e historical_fidelity_validated=false.

## Segurança e integridade

- Cada registro portátil contém somente campos autorizados e um checksum
  SHA-256 sobre serialização canônica. Isso detecta corrupção acidental,
  mas **não autentica o autor**: hashes podem ser recalculados.
- Importação exige ID/campos estritos, nome Unicode NFC e pontuação segura,
  data UTC completa, flags de não jogabilidade e checksum correspondente.
  Nunca executa conteúdo nem interpreta dados como caminhos ou comandos UCI.
- Entrada limitada a 4 KiB por registro e biblioteca a 256 identidades.
  Arquivos e ancestrais simbólicos são recusados no momento da validação.
- Publicação com arquivo temporário fsyncado e hardlink exclusivo; sem
  sobrescrita silenciosa. Falha explícita se filesystem não suportar
  esse mecanismo. Diretórios recebem fsync em POSIX.
- Importação idêntica na biblioteca de destino é idempotente.
  Conteúdo diferente com mesmo ID é recusado.
- Desativação cria marcador de estado associado ao SHA do registro.
  Importar de novo na mesma biblioteca não reativa o personagem.
  Restaurar exige uma ação explícita e conserva ID e metadados originais.
- Exportação copia somente a identidade, não o estado local de desativação.
  Importá-la em outra biblioteca não exporta a decisão de desativação.
- Sem transação multioperador: tarefas administrativas simultâneas
  devem ser serializadas pelo operador. A publicação exclusiva protege
  os bytes do registro, mas não há lock de biblioteca interprocessos.
- Nenhuma prova de tolerância a falha física, direitos de PGN ou
  autenticação de quem criou um personagem é feita nesta entrega.

## Fora desta primeira fatia

Cadastro de jogadores reais experimentais, resolução externa de homônimos,
importação e licença de PGN, modelos e repertórios, instalação de Rodent,
integração com seletor de Arena, renomeação com histórico de revisões,
benchmark de força/estilo, sincronização e autenticação multiusuário.
A #63 permanece aberta para essas integrações. QA físico Debian/Chrome/
Rodent permanece NOT_RUN na #83.
