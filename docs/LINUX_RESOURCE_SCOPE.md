# #27 — Escopo opt-in de recursos no Debian (fatia inicial)

O ChessCup agora dispõe de um **lançador externo opcional**: `scripts/run_limited_debian.py`. Seu papel é pedir ao **systemd do usuário** que execute a **árvore inteira** do processo Python+UCI num escopo `cgroup`, com propriedades `MemoryMax` (limite da árvore), `CPUQuota` (taxa de CPU) e `TasksMax` (tarefas). **Não altera motores, perfis, código de xadrez nem defaults da CLI.**

```bash
# Apenas verifica os argumentos; NÃO demonstra enforcement.
python scripts/run_limited_debian.py \
  --memory-mib 1024 --cpu-percent 200 --max-tasks 64 --dry-run -- \
  tournament-run --run /PASTA_AUTORIZADA/TORNEIO --limit-pairs 1

# Execução real SOMENTE após configurar e verificar a pasta/engine no Debian.
python scripts/run_limited_debian.py \
  --memory-mib 1024 --cpu-percent 200 --max-tasks 64 -- \
  tournament-run --run /PASTA_AUTORIZADA/TORNEIO --limit-pairs 1
```

O parâmetro de CPU é uma **taxa agregada** (100% corresponde aproximadamente a 1 CPU lógica); não é total de segundos nem timeout. O watchdog por tempo de partida de #98 continua tendo contrato próprio. `MemoryMax` não prevê qualidade do encerramento da partida após OOM; uma falha poderá deixar tentativa incompleta, e #103 exige retomada/aceite explícito. **Atenção:** o sistema inteiro precisa de memória; o usuário deve ajustar valores à RAM disponível (não assumir que 1 GiB serve para todas as operações).

**Sem fallback inseguro:** sistema não Linux, ausência de `systemd-run` ou falha do gerenciador de usuário produz saída não zero. Nenhuma tentativa subsequente roda `chesssim` diretamente sem quotas. O programa não solicita privilégios de root nem altera grupos, perfis UCI ou serviços permanentes. `--dry-run` só descreve a linha de comando e retorna `PLAN_ONLY_NOT_ENFORCED`. Uso com CLI arbitrária é rejeitado: somente operações UCI autorizadas.

## Aceite pendente no Debian (#83)

- Confirmar `systemd-run --user` operacional, controle cgroup e propriedades efetivamente aplicadas à unidade transitória; ver `systemctl --user show <scope> -p MemoryMax -p CPUQuotaPerSecUSec -p TasksMax` durante execução.
- Repetir duelos sintéticos e Rodent real com e sem limites, registrando processo/árvore, consumo, estado terminal e PGN; **sem simular PASS apenas porque o comando retornou 0**.
- Simular OOM e CPU throttle apenas em ambiente descartável e com autorização, sem prejudicar outros processos da máquina; verificar `INCOMPLETE`, retorno não zero, ausência de órfãos e `tournament-recovery-preview`.
- Ensaios `SIGKILL`/queda de energia e confirmação de durabilidade `fsync` continuam **pendentes**, e esta fatia não encerra #27.

## Referência

`systemd-run --scope` aceita `--property=` para uma unidade transitória e **não** combina com `--wait`; `--collect` recolhe unidades encerradas. Consulte o manual de systemd-run e systemd.resource-control da versão efetiva do Debian. Não se promete suporte Windows nem de sistemas sem cgroup v2 e user manager adequado.
