# Porta de segurança científica: teste final cego e integração (#147, #148)

**Situação: BLOQUEADA / DENIED.** A PR #144 obteve resultado negativo
para o ranker personalizado (105/256 vs baseline Maia79 142/256).
Ninguém pode transformar esse treino em "jogador histórico validado"
ou introduzi-lo silenciosamente nas partidas oficiais.

## Pré-registro necessário ANTES do teste final

1. Congelar um commit de código, protocolo, conjunto de candidatos,
   checkpoints/revisões/SHA-256 e fontes de PGN. Em particular, evitar
   parâmetros escolhidos retrospectivamente no braço VALIDATION.
2. Publicar hipótese primária de previsões de lances, intervalo e
   risco de múltiplos contrastes; estratificar por jogador, época,
   cor, fase e número de **partidas independentes**. Lotes underpowered
   não são classificados como sucessos nem platôs.
3. Desenhar estudo duplo cego de identidade com o perfil correto,
   perfil genérico e nomes permutados, FENs compartilhadas e nomeação
   pseudônima que só é revelada após congelar todos os resultados.
4. Predefinir análise de segurança tática independente com Stockfish
   e limites explícitos de tempo/profundidade/CPU/CP/mate; a métrica
   de acerto top-1 não substitui segurança nem Elo.
5. Registrar a evidência de não sobreposição de treino/validação/holdout
   por partida/posição canônica; riscos não auditáveis do pretraining
   de terceiros devem constar das limitações.
6. Aprovar a abertura do holdout final uma única vez após congelar os
   passos anteriores, publicar o relatório completo **incluindo
   falhas e resultados nulos**, concluir revisão adversarial.

**Este documento é um PLANO, não é um teste final executado nem um
pré-registro irrevogável de candidato/estimandos.** O hash exato de
candidato e a versão final do protocolo dependem dos resultados das
PRs #144, #145 e #149. Holdout ainda não deve ser lido.

## Porta para integração

O verificador \`scientific_promotion_guard\` possui allowlist de
relatórios finais revisados **vazia**, de propósito. Um JSON fornecido
pelo usuário pode ter SHA-256 íntegro e todos os campos booleanos
marcados como aprovados, mas continua recusado: SHA NÃO é
assinatura de autoridade. Somente novo commit de revisão externa
modificando a allowlist pode autorizar um relatório final
específico, após checar sua evidência de origem e aprovação.

Este módulo **ainda não está integrado ao executor de partidas**;
a issue #148 permanece aberta até conectar a porta aos modos
experimentais, preservar Rodent como default, provar fallback,
trilha por lance, rollback e homologação UCI física. Sem modelos
aprovados, a configuração deve permanecer sem possibilidade de
promoção por dados externos.

Nunca confundir "pipeline criado" com aprovação científica ou
testes independentes efetivamente executados.
