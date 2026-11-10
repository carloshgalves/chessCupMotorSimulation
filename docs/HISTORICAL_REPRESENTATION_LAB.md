# Laboratório de Representação Histórica — primeira entrega

Esta tela implementa a proposta **Perfis Históricos / Representação** sem atribuir ao Rodent IV uma precisão ou um Elo histórico que não foi medido.

## Como usar no Debian
1. Inicie a Arena no workspace normal com \`python -m chesssim arena --rodent "$RODENT_IV_HOME" --out "$HOME/chesssim-pr81-evidence" --port 8766\`.
2. Abra **História → perfil de um enxadrista → Representação histórica · laboratório UCI**.
3. Se deseja executar comparação de lances, crie \`$HOME/chesssim-pr81-evidence/representation_sources\` e **copie manualmente** um PGN de procedência/licença verificada para \`<slug>.pgn\`, por exemplo \`fischer.pgn\`. **Não** são feitos downloads nem acesso externo. O arquivo é tratado como entrada do usuário, nunca publicado no repositório.
4. Selecione o jogador e confira a janela de referência. Informe o nome exato que aparece nos cabeçalhos do PGN, anos, número de posições, semente e profundidade. Clique **Testes → Executar teste de representação**.
5. É necessário um conjunto de **ao menos 15 partidas independentes**, com decisões válidas no período, para separar treino, validação e teste lacrado. Os grupos são separados por partida; posições transpostas entre grupos são eliminadas, e o teste final não é avaliado no painel.
6. Consulte o resultado: \`acertos top-1 / decisões de validação\`, número de jogos, profundidade e hashes do PGN/motor/perfil/partição. O relatório fica em \`<workspace>/representation_results/<slug>/test-<id>.json\`. O endpoint GET somente consulta; relatórios existentes não são sobrescritos.

## Semântica científica
- O percentual **top-1** mede concordância das decisões do motor com lances históricos nesse conjunto de validação; não mede fidelidade geral, Elo, força, habilidade tática, estilo certificado ou precisão do Chess.com.
- Um gráfico histórico de Elo só poderá ser desenhado com uma fonte documental/versionada; esta entrega exibe "Curva indisponível". O valor 2500 inicial do rating experimental da simulação não aparece como rating histórico.
- Profundidade UCI não é um percentual de força. A UI apresenta profundidade em unidades reais (1–16), não o slider fictício 92% do exemplo.
- Sem PGN local válido, a execução fica indisponível; sem perfil Rodent IV, o sistema não cria um motor fictício.
- O corpus histórico, caso fornecido pelo usuário, permanece privado e é lido somente no workspace; nenhum PGN é incorporado ao pacote.
- Esta versão testa decisões UCI para um perfil existente, mas **não recalibra** nem escreve novos perfis, livros de abertura ou parâmetros de força. Isso depende das issues #10–#13 e da validação independente #20.
- O motor em teste desabilita livro de aberturas para que sua qualidade não seja artificialmente confundida com repertório. A opção "usar livro da época" fica desabilitada até existir fonte homologada.

## Limites e aceite
- 5 MiB por arquivo PGN, até 100 relatórios por jogador, validação de slug/nomes/datas/profundidade/semente, proibição de symlink nos diretórios monitorados.
- Testes sincronizados no backend, com apenas um worker de comparação de cada vez; ações mutáveis exigem token e origem locais, assim como as demais APIs da Arena.
- A tela do laboratório não substitui a aba História já aprovada: é uma navegação separada, com botão de volta e sem modificar as biografias e seus direitos autorais.
- Revisão visual no Chrome/Debian ainda é gate humano antes do merge; os valores de exemplo da imagem aprovada não são evidência de resultados reais.
