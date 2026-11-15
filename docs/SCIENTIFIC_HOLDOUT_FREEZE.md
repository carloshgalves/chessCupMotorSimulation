# Pré-registro do holdout final — acesso bloqueado por padrão

A partir desta alteração, \`real_policy_experiment.evaluate_policy(..., arm="holdout")\`
**não** aceita apenas \`acknowledge_holdout=True\`. O comando exige também
\`--final-preregistration <arquivo>\`, que deve corresponder exatamente
ao modelo assinado por SHA, manifesto de treino, compromisso das fontes,
seed, orçamento, limite de posições, configuração Rodent, plano cego,
protocolo tático, hipóteses e plano de múltiplas comparações.

O SHA-256 do pré-registro é recomputado e verificado; um arquivo autoassinado
pelo operador **não** libera o acesso. O hash exato precisa constar de
\`TRUSTED_FROZEN_PREREGISTRATIONS\`, uma allowlist do próprio código
preenchida somente por PR revisada. A allowlist começa **vazia**, pois
nenhum modelo do elenco passou pela identificação cega, segurança tática
Stockfish e aprovação independente. O pipeline V7 falhou na personalização;
o benchmark DSC ainda é exploratório.

**Este gate é de segurança por política do repositório, não uma assinatura
assimétrica de pessoa ou organização.** A promoção final é controlada
separadamente por \`scientific_promotion_guard\` e permanece negada. A
execução única do holdout requer ainda um ledger durável com acesso
coordenado, não apenas a recusa de sobrescrita do mesmo caminho de arquivo.

As fixtures de testes exercitam a autorização via monkeypatch em dados
**sintéticos**, jamais o holdout histórico. Não publicar PGNs reais do
braço holdout no repositório ou artefatos. A validação exploratória
\`arm="validation"\` segue intacta. Issue #147 continua aberta até
o pré-registro aprovado, ensaio cego genuíno, auditoria tática real,
ledger de abertura única e revisão final sem findings.
