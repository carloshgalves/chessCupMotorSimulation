"""Interface de linha de comando do ChessCup Motor Simulation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .bracket import generate_draw
from .catalog import GROUP_LABELS, find_player, load_roster
from .rodent import profile_status, setup_official
from .protocol import OFFICIAL


def rodent_root(ns: argparse.Namespace) -> Path:
    folder = ns.rodent or os.getenv("RODENT_IV_HOME")
    if not folder:
        raise ValueError("Informe --rodent CAMINHO ou defina RODENT_IV_HOME")
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Pasta do Rodent não encontrada: {root}")
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chesssim", description="Copa dos Imortais — ferramenta local")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("roster", help="Lista os 64 jogadores da primeira edição")
    check = sub.add_parser("check", help="Audita os perfis reconhecíveis na pasta do Rodent")
    check.add_argument("--rodent", type=Path)

    setup = sub.add_parser("setup", help="Prevê/instala 21 perfis oficiais ausentes e registra aliases")
    setup.add_argument("--rodent", type=Path)
    setup.add_argument("--apply", action="store_true", help="Autoriza mudanças locais após backup")
    setup.add_argument("--with-books", action="store_true", help="Baixa também livros mencionados pelos perfis")

    draw = sub.add_parser("draw", help="Sorteia 32 confrontos sem exigência de perfis prontos")
    draw.add_argument("--seed", type=int, required=True)
    draw.add_argument("--out", type=Path, help="Salva o sorteio JSON neste caminho")

    tournament_init = sub.add_parser("tournament-init", help="Cria chave eliminatória auditável para perfis disponíveis")
    tournament_init.add_argument("--rodent", type=Path)
    tournament_init.add_argument("--players", nargs="+", required=True, help="2, 4, 8, 16, 32 ou 64 slugs")
    tournament_init.add_argument("--out", type=Path, required=True, help="Nova pasta exclusiva do torneio")
    tournament_init.add_argument("--seed", type=int, default=2026)
    tournament_init.add_argument("--preset", choices=["official", "classical", "rapid", "blitz", "bullet"],
                                 default="official")
    tournament_init.add_argument("--max-tiebreak-pairs", type=int, default=3,
                                 help="Máximo de pares de jogos antes de arbitragem explícita")
    tournament_init.add_argument("--max-plies", type=int, default=600)
    tournament_init.add_argument("--max-game-wall-seconds", type=float,
                                 help="Limite externo de duração da partida; não é derrota por tempo")

    tournament_run = sub.add_parser("tournament-run", help="Executa/retoma partidas sem duplicar confrontos")
    tournament_run.add_argument("--rodent", type=Path)
    tournament_run.add_argument("--run", type=Path, required=True)
    tournament_run.add_argument("--limit-pairs", type=int, help="Executa no máximo N pares nesta chamada")
    tournament_run.add_argument("--retry-incomplete", action="store_true",
                                help="Autoriza nova tentativa quando o jogo anterior foi interrompido")
    tournament_run.add_argument("--max-run-seconds", type=float,
                                help="Pausa graciosa ao atingir o orçamento desta execução")

    tournament_status = sub.add_parser("tournament-status", help="Mostra progresso e pendências do chaveamento")
    tournament_status.add_argument("--run", type=Path, required=True)

    recovery = sub.add_parser(
        "tournament-recovery-preview",
        help="Pré-visualiza retomada e tentativas interrompidas sem alterar arquivos ou iniciar motores")
    recovery.add_argument("--run", type=Path, required=True,
                          help="Diretório de torneio persistente a ser auditado")

    tournament_stop = sub.add_parser("tournament-stop",
                                     help="Solicita parada graciosa sem aguardar o lock da partida")
    tournament_stop.add_argument("--run", type=Path, required=True)
    tournament_stop.add_argument("--reason", default="Solicitado pelo operador")

    tournament_clear = sub.add_parser("tournament-clear-stop",
                                      help="Libera pedido de parada com exclusão de execução concorrente")
    tournament_clear.add_argument("--run", type=Path, required=True)

    tournament_resolve = sub.add_parser("tournament-resolve", help="Registra arbitragem externa para empate esgotado")
    tournament_resolve.add_argument("--run", type=Path, required=True)
    tournament_resolve.add_argument("--match", required=True, help="Identificador rNN-mNN")
    tournament_resolve.add_argument("--winner", required=True)
    tournament_resolve.add_argument("--reason", required=True, help="Justificativa formal registrada no manifesto")

    custom_create = sub.add_parser(
        "custom-player-create", help="Registra identidade fictícia local; NÃO instala perfil UCI")
    custom_create.add_argument("--library", type=Path, required=True)
    custom_create.add_argument("--name", required=True,
                               help="Nome fictício original declarado pelo operador")

    custom_list = sub.add_parser(
        "custom-player-list", help="Lista identidades fictícias separadas do elenco histórico")
    custom_list.add_argument("--library", type=Path, required=True)

    custom_export = sub.add_parser(
        "custom-player-export", help="Exporta cópia portátil verificada do registro fictício")
    custom_export.add_argument("--library", type=Path, required=True)
    custom_export.add_argument("--id", required=True)
    custom_export.add_argument("--out", type=Path, required=True)

    custom_import = sub.add_parser(
        "custom-player-import", help="Importa registro fictício, sem executar arquivos ou motores")
    custom_import.add_argument("--library", type=Path, required=True)
    custom_import.add_argument("--file", type=Path, required=True)

    custom_retire = sub.add_parser(
        "custom-player-retire", help="Desativa cadastro mantendo registro original")
    custom_retire.add_argument("--library", type=Path, required=True)
    custom_retire.add_argument("--id", required=True)

    custom_restore = sub.add_parser(
        "custom-player-restore", help="Reativa cadastro local desativado sem trocar identidade")
    custom_restore.add_argument("--library", type=Path, required=True)
    custom_restore.add_argument("--id", required=True)

    custom_rename = sub.add_parser(
        "custom-player-rename", help="Acrescenta revisão de nome sem reescrever identidade")
    custom_rename.add_argument("--library", type=Path, required=True)
    custom_rename.add_argument("--id", required=True)
    custom_rename.add_argument("--name", required=True, help="Novo nome fictício validado")
    custom_rename.add_argument("--expected-head", required=True,
                               help="revision_sha256 atual obtido em list/history; impede edição obsoleta")

    custom_history = sub.add_parser(
        "custom-player-history", help="Consulta a cadeia auditável de renomeações locais")
    custom_history.add_argument("--library", type=Path, required=True)
    custom_history.add_argument("--id", required=True)

    diversity = sub.add_parser(
        "diversity-report", help="Compara sequências PGN reais de múltiplos duelos, sem alterar motores")
    diversity.add_argument("--runs", type=Path, nargs="+", required=True,
                           help="Diretórios locais contendo duelos com summary.json e jogo_N.pgn")
    diversity.add_argument("--out", type=Path, required=True,
                           help="Novo arquivo JSON; não sobrescreve resultados existentes")
    diversity.add_argument("--opening-plies", type=int, default=12,
                           help="Posição de abertura comparada após N meios-lances (2..40)")

    diversity_plan = sub.add_parser(
        "diversity-plan", help="Planeja comparação pareada de seeds, sem abrir motor nem PGN")
    diversity_plan.add_argument("--players", nargs=2, required=True,
                                help="Dois slugs canônicos distintos")
    diversity_plan.add_argument("--seed", type=int, default=2029)
    diversity_plan.add_argument("--trials", type=int, default=6)
    diversity_plan.add_argument("--external-repertoire", action="store_true",
                                help="Declara hipótese de repertório externo ativo; não verifica instalação")
    diversity_plan.add_argument("--out", type=Path, required=True,
                                help="Arquivo JSON novo, nunca sobrescrito")

    diversity_run = sub.add_parser(
        "diversity-run", help="Executa blocos A1/A2/B pré-registrados em duelos UCI locais")
    diversity_run.add_argument("--plan", type=Path, required=True,
                               help="Plano gerado por diversity-plan, sem adulterações")
    diversity_run.add_argument("--rodent", type=Path)
    diversity_run.add_argument("--out", type=Path, required=True,
                               help="Diretório novo e exclusivo; nunca retoma implicitamente")
    diversity_run.add_argument("--white-book", type=Path,
                               help="Livro do primeiro participante (não é cor fixa no replay)")
    diversity_run.add_argument("--black-book", type=Path,
                               help="Livro do segundo participante (não é cor fixa no replay)")
    diversity_run.add_argument("--blocks", type=int, default=1,
                               help="Executa os primeiros N blocos (três braços por bloco)")
    diversity_run.add_argument("--depth", type=int, default=8)
    diversity_run.add_argument("--max-plies", type=int, default=400)
    diversity_run.add_argument("--max-game-wall-seconds", type=float, default=600.0)

    age_cohort = sub.add_parser(
        "age-cohort", help="Extrai recorte etário de cache PGN local com holdout separado")
    age_cohort.add_argument("--cache", type=Path, required=True,
                            help="Pasta gerada por pgn-cache --apply (manifest.json + games.pgn)")
    age_cohort.add_argument("--pgn-name", required=True,
                            help="Nome completo EXATO presente no PGN; identidade declarada pelo operador")
    age_cohort.add_argument("--birth-date", required=True,
                            help="Nascimento completo AAAA-MM-DD, verificado pelo operador")
    age_cohort.add_argument("--age", type=int, required=True,
                            help="Idade inteira já completada na data da partida")
    age_cohort.add_argument("--min-train", type=int, default=100,
                            help="Limiar descritivo de jogos únicos de treino (padrão 100)")
    age_cohort.add_argument("--out", type=Path, required=True,
                            help="Nova pasta exclusiva para train.pgn e cohort.json")

    fast_audit = sub.add_parser(
        "fast-audit", help="Avalia probabilidades fixas da temporada rápida contra partidas UCI verificadas")
    fast_audit.add_argument("--workspace", type=Path, required=True,
                            help="Diretório local da Arena contendo leagues/ e recibos UCI")
    fast_audit.add_argument("--out", type=Path, required=True,
                            help="Novo arquivo JSON; jamais sobrescreve partidas ou relatórios")

    rating = sub.add_parser("rating-export",
                            help="Exporta ledger Elo experimental de jogos locais verificados (não altera motores)")
    rating.add_argument("--workspace", type=Path, required=True,
                        help="Diretório local de sessões/torneios já concluídos")
    rating.add_argument("--out", type=Path, required=True,
                        help="Arquivo JSON de ledger com checksum e rankings")
    rating.add_argument("--k", default="10",
                        help="K experimental positivo entre 0 e 100; padrão 10")
    rating.add_argument("--replace", action="store_true",
                        help="Autoriza substituir snapshot quando novos jogos forem descobertos")
    rating.add_argument("--modality", choices=["classical", "rapid", "blitz", "bullet", "unclassified"],
                        default="rapid", help="Pool isolado de Elo (padrão: rapid)")

    arena = sub.add_parser("arena", help="Arena local opt-in para lançar e acompanhar duelos UCI")
    arena.add_argument("--rodent", type=Path,
                       help="Raiz local da instalação Rodent IV")
    arena.add_argument("--out", type=Path, required=True,
                       help="Diretório dedicado a sessões da arena")
    arena.add_argument("--port", type=int, default=8766,
                       help="Porta local (padrão 8766)")
    arena.add_argument("--opening-books", type=Path,
                       help="Pasta de repertórios JSON pré-aprovados por slug")
    arena.add_argument("--max-game-wall-seconds", type=float, default=3600,
                       help="Limite de duração operacional por partida")

    watch = sub.add_parser("watch", help="Tabuleiro local ao vivo para torneio ou duelo")
    watch.add_argument("--run", type=Path, required=True,
                       help="Diretório com tournament.json ou summary.json")
    watch.add_argument("--quality-report", type=Path,
                       help="JSON analyze-quality para um único PGN final com hash exato")
    watch.add_argument("--port", type=int, default=8765,
                       help="Porta local (padrão 8765; 0 escolhe uma livre)")

    play = sub.add_parser("play", help="Executa duelo real entre dois perfis oficiais")
    play.add_argument("--rodent", type=Path)
    play.add_argument("white", help="Slug ou nome completo da personalidade")
    play.add_argument("black", help="Slug ou nome completo da personalidade")
    play.add_argument("--reverse", action="store_true", help="Segunda partida com cores invertidas")
    play.add_argument("--no-books", action="store_true", help="Desabilita livro UCI em duelo de desenvolvimento/smoke")
    speed = play.add_mutually_exclusive_group()
    speed.add_argument("--depth", type=int, help="Profundidade por lance (padrão: 6)")
    speed.add_argument("--movetime", type=float, help="Segundos por lance, alternativa ao depth")
    play.add_argument("--preset", choices=["official", "classical", "rapid", "blitz", "bullet"], help="Relógios fixos: official legado 10+2; outros são presets v0.6 (não força histórica)")
    play.add_argument("--max-plies", type=int, default=None)
    play.add_argument("--out", type=Path, default=Path("partidas_chesssim"))


    sub.add_parser("protocol", help="Exibe o protocolo congelado da Copa e da calibração")
    audit = sub.add_parser("audit", help="Testa todos os 21 perfis, ambos os lados; --full completa jogos")
    audit.add_argument("--rodent", type=Path)
    audit.add_argument("--full", action="store_true", help="Exige partida terminada por regra")
    audit.add_argument("--one-color", action="store_true", help="Somente perfil de brancas")
    audit.add_argument("--depth", type=int, default=4)
    audit.add_argument("--max-plies", type=int, default=600)
    audit.add_argument("--books", action="store_true", help="Habilita livros no teste rápido; no completo já são usados por padrão")
    audit.add_argument("--out", type=Path, help="Diretório novo para relatórios")

    fetch = sub.add_parser("fetch-mecking", help="Baixa partidas PGN de Mequinho do PGN Mentor")
    fetch.add_argument("--out", type=Path, default=Path("datasets/Mecking.pgn"))

    cal = sub.add_parser("calibrate", help="treino / validação / teste final lacrado")
    cal.add_argument("player", help="Slug cadastrado no elenco")
    cal.add_argument("--match-name", help="Nome do jogador como aparece nos cabeçalhos PGN")
    cal.add_argument("--base-file", default="karpov.txt", help="Perfil inicial oficial no Rodent")
    cal.add_argument("--from-year", type=int)
    cal.add_argument("--to-year", type=int)
    cal.add_argument("--rodent", type=Path)
    cal.add_argument("--pgn", type=Path, default=Path("datasets/Mecking.pgn"),
                     help="PGN histórico; obrigatório ajustar para outros jogadores")
    cal.add_argument("--out", type=Path, help="Saída nova da calibração")
    cal.add_argument("--seed", type=int, default=2026)
    cal.add_argument("--depth", type=int, default=5)
    cal.add_argument("--trials", type=int, default=24)
    cal.add_argument("--train-positions", type=int, default=60)
    cal.add_argument("--val-positions", type=int, default=30)
    cal.add_argument("--min-holdout-games", type=int, default=5, help="Legado; a v0.3 exige 3 partições")
    cal.add_argument("--test-positions", type=int, default=48)
    cal.add_argument("--shortlist", type=int, default=4)
    cal.add_argument("--split-by", choices=["game", "event"], default="game",
                     help="game padrão; event evita treino e teste no mesmo evento/ano")

    v4 = sub.add_parser("calibrate-v4", help="busca direcionada, CV por partidas e quatro perfis-base")
    v4.add_argument("player", help="Slug cadastrado no elenco, ex.: mequinho")
    v4.add_argument("--match-name", help="Nome histórico no PGN")
    v4.add_argument("--rodent", type=Path)
    v4.add_argument("--pgn", type=Path, default=Path("datasets/Mecking.pgn"))
    v4.add_argument("--from-year", type=int, default=1973)
    v4.add_argument("--to-year", type=int, default=1978)
    v4.add_argument("--base-files", nargs="+", default=["karpov.txt", "petrosian.txt", "fischer.txt", "botvinnik.txt"])
    v4.add_argument("--partition-seed", type=int, default=2027, help="FIXO: mesma semente da v0.3")
    v4.add_argument("--search-seed", type=int, default=31415, help="Somente CV e amostragem de busca")
    v4.add_argument("--split-by", choices=["game", "event"], default="game")
    v4.add_argument("--train-positions", type=int, default=120)
    v4.add_argument("--val-positions", type=int, default=36)
    v4.add_argument("--test-positions", type=int, default=30, help="FIXO: 30 na v0.3 de Mequinho")
    v4.add_argument("--depth", type=int, default=4)
    v4.add_argument("--trials", type=int, default=8)
    v4.add_argument("--shortlist", type=int, default=3)
    v4.add_argument("--folds", type=int, default=3)
    v4.add_argument("--cv-trials", type=int, default=3)
    v4.add_argument("--delta", type=int, default=20)
    v4.add_argument("--probe-positions", type=int, default=20)
    v4.add_argument("--probe-params", nargs="+", default=None)
    v4.add_argument("--divergence-penalty", type=float, default=0.08)
    v4.add_argument("--sealed-reference", type=Path, help="Diretório da calibração v0.3 com teste ainda lacrado")
    v4.add_argument("--ledger", type=Path, help="Registro imutável da partição do conjunto histórico")
    v4.add_argument("--out", type=Path, help="Diretório NOVO para pesquisa")

    corpus_catalog = sub.add_parser("corpus-sources", help="Lista cobertura oficial disponível no PGN Mentor para 64 jogadores")
    corpus_catalog.add_argument("--out", type=Path, help="Relatório JSON opcional")

    corpus_fetch = sub.add_parser("corpus-fetch", help="Obtém PGNs públicos por jogador, sem alegar completude")
    corpus_fetch.add_argument("players", nargs="+", help="Slugs do elenco ou 'all' para os 64")
    corpus_fetch.add_argument("--out", type=Path, default=Path("datasets/pgnmentor"))
    corpus_fetch.add_argument("--dry-run", action="store_true")
    corpus_fetch.add_argument("--delay", type=float, default=2.0)

    corpus_build = sub.add_parser("corpus-build", help="Monta corpus deduplicado de PGNs locais com proveniência")
    corpus_build.add_argument("player", help="Slug cadastrado; usado para nomear corpus")
    corpus_build.add_argument("--match-name", help="Sobrenome identificável nos cabeçalhos PGN")
    corpus_build.add_argument("--sources", type=Path, nargs="+", required=True)
    corpus_build.add_argument("--out", type=Path)

    v5 = sub.add_parser("calibrate-v5", help="CV repetida e corpus ampliado; validação lacrada por padrão")
    v5.add_argument("player")
    v5.add_argument("--match-name")
    v5.add_argument("--rodent", type=Path)
    v5.add_argument("--pgn", type=Path, required=True)
    v5.add_argument("--legacy-v3", type=Path, help="Obrigatório para Mequinho: run v0.3 com teste lacrado")
    v5.add_argument("--legacy-pgn", type=Path, help="PGN original (Mecking.pgn) da v0.3, sem modificar")
    v5.add_argument("--base-files", nargs="+", default=["karpov.txt","botvinnik.txt","petrosian.txt","fischer.txt"])
    v5.add_argument("--from-year", type=int, default=1600)
    v5.add_argument("--to-year", type=int, default=2100)
    v5.add_argument("--partition-seed", type=int, default=2028)
    v5.add_argument("--search-seed", type=int, default=31415)
    v5.add_argument("--train-positions", type=int, default=240)
    v5.add_argument("--val-positions", type=int, default=60)
    v5.add_argument("--test-positions", type=int, default=45)
    v5.add_argument("--depth", type=int, default=4)
    v5.add_argument("--folds", type=int, default=3)
    v5.add_argument("--repeats", type=int, default=3)
    v5.add_argument("--cv-trials", type=int, default=2)
    v5.add_argument("--trials", type=int, default=6)
    v5.add_argument("--shortlist", type=int, default=3)
    v5.add_argument("--probe-positions", type=int, default=60)
    v5.add_argument("--probe-params", nargs="+", default=None)
    v5.add_argument("--delta", type=int, default=20)
    v5.add_argument("--divergence-penalty", type=float, default=.08)
    v5.add_argument("--select", action="store_true", help="UMA seleção explícita na validação; bloqueia futuras seleções para o mesmo cohort")
    v5.add_argument("--cohort-ledger", type=Path)
    v5.add_argument("--validation-ledger", type=Path)
    v5.add_argument("--out", type=Path)

    sens = sub.add_parser("sensitivity", help="Análise de sensibilidade apenas no TREINO; não abre teste")
    sens.add_argument("--rodent", type=Path)
    sens.add_argument("--pgn", type=Path, default=Path("datasets/Mecking.pgn"))
    sens.add_argument("--match-name", default="Mecking")
    sens.add_argument("--base-file", default="karpov.txt")
    sens.add_argument("--seed", type=int, default=2026)
    sens.add_argument("--depth", type=int, default=4)
    sens.add_argument("--positions", type=int, default=32)
    sens.add_argument("--delta", type=int, default=20)
    sens.add_argument("--from-year", type=int, default=1973)
    sens.add_argument("--to-year", type=int, default=1978)
    sens.add_argument("--split-by", choices=["game", "event"], default="game")
    sens.add_argument("--out", type=Path, default=Path("calibracoes/sensitivity.json"))

    finale = sub.add_parser("final-eval", help="Abre uma única vez o teste final de uma calibração v0.3")
    finale.add_argument("--run", required=True, type=Path, help="Diretório contendo calibration.json v0.3")
    finale.add_argument("--rodent", type=Path)
    finale.add_argument("--stockfish", type=Path, help="Stockfish instalado localmente (opcional)")
    finale.add_argument("--stockfish-depth", type=int, default=10)
    finale.add_argument("--compare", action="append", default=[], metavar="PROFILE.TXT",
                        help="Perfil oficial adicional: e.g. tal.txt; até 6")

    bench = sub.add_parser("benchmark", help="Confrontos pareados sem livros; força observável, NÃO Elo")
    bench.add_argument("--rodent", type=Path)
    bench.add_argument("--profile", required=True, type=Path,
                       help="TXT de candidato experimental (não modifica basic.ini)")
    bench.add_argument("--opponent", default="karpov.txt")
    bench.add_argument("--depth", type=int, default=5)
    bench.add_argument("--openings", type=int, default=3)
    bench.add_argument("--max-plies", type=int, default=450)
    bench.add_argument("--out", type=Path,
                       help="Diretório para benchmark JSON e PGN")

    peak = sub.add_parser("peak-audit", help="audita janela de auge e modalidades sem avaliar motor")
    peak.add_argument("player")
    peak.add_argument("--pgn", type=Path, required=True)
    peak.add_argument("--match-name")
    peak.add_argument("--peak-registry", type=Path, default=Path("data/peak_windows_v06.json"))
    peak.add_argument("--allow-provisional", action="store_true")
    peak.add_argument("--annotations", type=Path)
    peak.add_argument("--out", type=Path)

    annot = sub.add_parser("peak-annotations-init", help="Gera planilha JSON de verificação documental da modalidade por partida")
    annot.add_argument("player")
    annot.add_argument("--pgn", type=Path, required=True)
    annot.add_argument("--match-name")
    annot.add_argument("--peak-registry", type=Path, default=Path("data/peak_windows_v06.json"))
    annot.add_argument("--allow-provisional", action="store_true")
    annot.add_argument("--out", type=Path, required=True)

    peaks = sub.add_parser("peak-windows-init", help="Gera cadastro inicial dos 64 picos históricos sem inventar datas")
    peaks.add_argument("--out", type=Path, default=Path("data/peak_windows_v06.json"))

    v6 = sub.add_parser("calibrate-v6", help="CV no auge/ritmo, sem consultar validação e teste final")
    v6.add_argument("player")
    v6.add_argument("--pgn", type=Path, required=True)
    v6.add_argument("--match-name")
    v6.add_argument("--rodent", type=Path)
    v6.add_argument("--peak-registry", type=Path, default=Path("data/peak_windows_v06.json"))
    v6.add_argument("--allow-provisional", action="store_true")
    v6.add_argument("--annotations", type=Path)
    v6.add_argument("--mode", choices=["classical","rapid","blitz","bullet"], default="classical")
    v6.add_argument("--include-unknown", action="store_true",
                    help="Opt-in para usar registros sem modalidade no modelo exploratório clássico")
    v6.add_argument("--v5-report", type=Path, help="Obrigatório para Mequinho: calibração v5 lacrada")
    v6.add_argument("--legacy-v3", type=Path)
    v6.add_argument("--legacy-pgn", type=Path)
    v6.add_argument("--cohort-ledger", type=Path)
    v6.add_argument("--fresh-cohort-ack", action="store_true",
                    help="Somente jogadores sem partições anteriormente reservadas")
    v6.add_argument("--partition-seed", type=int, default=2028)
    v6.add_argument("--search-seed", type=int, default=31415)
    v6.add_argument("--depth", type=int, default=3)
    v6.add_argument("--positions", type=int, default=90)
    v6.add_argument("--folds", type=int, default=3)
    v6.add_argument("--repeats", type=int, default=2)
    v6.add_argument("--cv-trials", type=int, default=1)
    v6.add_argument("--trials", type=int, default=2)
    v6.add_argument("--probe-positions", type=int, default=20)
    v6.add_argument("--delta", type=int, default=20)
    v6.add_argument("--divergence-penalty", type=float, default=0.08)
    v6.add_argument("--base-files", nargs="+", default=["karpov.txt","botvinnik.txt","petrosian.txt","fischer.txt"])
    v6.add_argument("--probe-params", nargs="+")
    v6.add_argument("--out", type=Path)

    # v0.7 — source verification, embargo-preserving annex staging and paired era study.
    tm = sub.add_parser('temporal-compare', help='paired career vs peak on same heldout peak folds; TRAIN-only')
    tm.add_argument('player')
    tm.add_argument('--pgn',type=Path,required=True)
    tm.add_argument('--match-name')
    tm.add_argument('--rodent',type=Path)
    tm.add_argument('--peak-registry',type=Path,default=Path('data/peak_windows_v06.json'))
    tm.add_argument('--allow-provisional',action='store_true')
    tm.add_argument('--include-unknown',action='store_true')
    tm.add_argument('--annotations',type=Path)
    tm.add_argument('--v5-report',type=Path,required=True)
    tm.add_argument('--legacy-v3',type=Path,required=True)
    tm.add_argument('--legacy-pgn',type=Path,required=True)
    tm.add_argument('--cohort-ledger',type=Path)
    tm.add_argument('--positions',type=int,default=60)
    tm.add_argument('--train-budget',type=int,default=90)
    tm.add_argument('--depth',type=int,default=3)
    tm.add_argument('--folds',type=int,default=2)
    tm.add_argument('--repeats',type=int,default=2)
    tm.add_argument('--trials',type=int,default=0)
    tm.add_argument('--probe-positions',type=int,default=15)
    tm.add_argument('--search-seed',type=int,default=31415)
    tm.add_argument('--divergence-penalty',type=float,default=.08)
    tm.add_argument('--base-files',nargs='+',default=['karpov.txt','botvinnik.txt','petrosian.txt','fischer.txt'])
    tm.add_argument('--out',type=Path,required=True)

    ev = sub.add_parser('tournament-evidence',help='match documentary time control against exact PGN mainlines')
    ev.add_argument('--sources',action='store_true',help='Show researched tournament sources; no files modified')
    ev.add_argument('--source-id')
    ev.add_argument('--source-pgn',type=Path)
    ev.add_argument('--pgn',type=Path)
    ev.add_argument('--annotations',type=Path)
    ev.add_argument('--match-name',default='Mecking')
    ev.add_argument('--out',type=Path)

    ax = sub.add_parser('corpus-annex-plan',help='frozen staging plan for extra PGNs; does NOT train or open tests')
    ax.add_argument('player')
    ax.add_argument('--pgn',type=Path,required=True)
    ax.add_argument('--sources',type=Path,nargs='+',required=True)
    ax.add_argument('--match-name')
    ax.add_argument('--v5-report',type=Path,required=True)
    ax.add_argument('--legacy-v3',type=Path,required=True)
    ax.add_argument('--legacy-pgn',type=Path,required=True)
    ax.add_argument('--cohort-ledger',type=Path)
    ax.add_argument('--seed',type=int,default=2029)
    ax.add_argument('--out',type=Path,required=True)

    # v0.8 — controlled temporal mixes, descriptive style signatures and provisional playable profiles.
    tc8 = sub.add_parser('temporal-controlled', help='compare peak/mixed/career arms with explicit caps on games and positions per game')
    tc8.add_argument('player')
    tc8.add_argument('--pgn', type=Path, required=True)
    tc8.add_argument('--match-name')
    tc8.add_argument('--rodent', type=Path)
    tc8.add_argument('--peak-registry', type=Path, default=Path('data/peak_windows_v06.json'))
    tc8.add_argument('--allow-provisional', action='store_true')
    tc8.add_argument('--include-unknown', action='store_true')
    tc8.add_argument('--annotations', type=Path)
    tc8.add_argument('--v5-report', type=Path, required=True)
    tc8.add_argument('--legacy-v3', type=Path, required=True)
    tc8.add_argument('--legacy-pgn', type=Path, required=True)
    tc8.add_argument('--cohort-ledger', type=Path)
    tc8.add_argument('--positions', type=int, default=120)
    tc8.add_argument('--train-budget', type=int, default=120)
    tc8.add_argument('--depth', type=int, default=3)
    tc8.add_argument('--folds', type=int, default=3)
    tc8.add_argument('--repeats', type=int, default=3)
    tc8.add_argument('--trials', type=int, default=0)
    tc8.add_argument('--probe-positions', type=int, default=15)
    tc8.add_argument('--search-seed', type=int, default=31415)
    tc8.add_argument('--divergence-penalty', type=float, default=.08)
    tc8.add_argument('--max-train-games', type=int, default=24)
    tc8.add_argument('--max-positions-per-game', type=int, default=8)
    tc8.add_argument('--peak-fractions', nargs='+', type=float, default=[1.0, 0.75, 0.5, 0.25, 0.0])
    tc8.add_argument('--base-files', nargs='+', default=['karpov.txt','botvinnik.txt','petrosian.txt','fischer.txt'])
    tc8.add_argument('--out', type=Path, required=True)

    sg8 = sub.add_parser('style-signature', help='descriptive style signatures for career, peak and outside-peak cohorts')
    sg8.add_argument('player')
    sg8.add_argument('--pgn', type=Path, required=True)
    sg8.add_argument('--match-name')
    sg8.add_argument('--peak-registry', type=Path, default=Path('data/peak_windows_v06.json'))
    sg8.add_argument('--allow-provisional', action='store_true')
    sg8.add_argument('--out', type=Path, required=True)

    pb8 = sub.add_parser('build-provisional', help='build a provisional playable Rodent personality from a chosen temporal mix')
    pb8.add_argument('player')
    pb8.add_argument('--pgn', type=Path, required=True)
    pb8.add_argument('--match-name')
    pb8.add_argument('--rodent', type=Path)
    pb8.add_argument('--peak-registry', type=Path, default=Path('data/peak_windows_v06.json'))
    pb8.add_argument('--allow-provisional', action='store_true')
    pb8.add_argument('--include-unknown', action='store_true')
    pb8.add_argument('--annotations', type=Path)
    pb8.add_argument('--v5-report', type=Path, required=True)
    pb8.add_argument('--legacy-v3', type=Path, required=True)
    pb8.add_argument('--legacy-pgn', type=Path, required=True)
    pb8.add_argument('--cohort-ledger', type=Path)
    pb8.add_argument('--peak-fraction', type=float, default=0.75)
    pb8.add_argument('--train-budget', type=int, default=160)
    pb8.add_argument('--depth', type=int, default=3)
    pb8.add_argument('--trials', type=int, default=4)
    pb8.add_argument('--probe-positions', type=int, default=20)
    pb8.add_argument('--search-seed', type=int, default=31415)
    pb8.add_argument('--divergence-penalty', type=float, default=.08)
    pb8.add_argument('--max-train-games', type=int, default=24)
    pb8.add_argument('--max-positions-per-game', type=int, default=8)
    pb8.add_argument('--base-files', nargs='+', default=['karpov.txt','botvinnik.txt','petrosian.txt','fischer.txt'])
    pb8.add_argument('--out', type=Path, required=True)

    pin = sub.add_parser('provisional-install', help='register MequinhoV08 as a separate Rodent experimental alias')
    pin.add_argument('--rodent', type=Path)
    pin.add_argument('--profile', type=Path, required=True)
    pin.add_argument('--apply', action='store_true', help='Explicitly authorize copying and registering the alias')

    pp = sub.add_parser('play-provisional', help='play provisional Mequinho against an official Rodent profile')
    pp.add_argument('opponent', help='Slug of an official player (e.g. fischer, tal)')
    pp.add_argument('--rodent', type=Path)
    pp.add_argument('--reverse', action='store_true')
    plimit = pp.add_mutually_exclusive_group()
    plimit.add_argument('--depth', type=int)
    plimit.add_argument('--movetime', type=float)
    plimit.add_argument('--preset', choices=['classical','rapid','blitz','bullet'])
    pp.add_argument('--max-plies', type=int, default=400)
    pp.add_argument('--out', type=Path, default=Path('partidas_chesssim_v08'))


    replay = sub.add_parser('replay-html', help='offline interactive chessboard replay for existing PGN games')
    replay.add_argument('--pgn', type=Path, required=True)
    replay.add_argument('--out', type=Path, required=True)
    cache = sub.add_parser("pgn-cache", help="Pré-voo de direitos e cache PGN local sem rede/engine")
    cache.add_argument("--catalog", required=True, type=Path,
                       help="JSON de fontes, direitos declarados e SHA-256 dos arquivos locais")
    cache.add_argument("--out", required=True, type=Path,
                       help="Novo diretório de cache; não sobrescreve")
    cache.add_argument("--apply", action="store_true",
                       help="Autoriza gravar dados após pré-voo; padrão é dry-run sem escrita")
    cache.add_argument("--max-games", type=int, default=500)
    cache.add_argument("--max-cache-mb", type=int, default=32)

    ob9 = sub.add_parser('opening-build', help='make a per-player historical opening tree, Mequinho training embargo enforced')
    ob9.add_argument('player')
    ob9.add_argument('--pgn', type=Path, required=True)
    ob9.add_argument('--match-name')
    ob9.add_argument('--max-plies', type=int, default=16)
    ob9.add_argument('--from-year', type=int)
    ob9.add_argument('--to-year', type=int)
    ob9.add_argument('--v5-report', type=Path)
    ob9.add_argument('--legacy-v3', type=Path)
    ob9.add_argument('--legacy-pgn', type=Path)
    ob9.add_argument('--cohort-ledger', type=Path)
    ob9.add_argument('--ack-unpartitioned', action='store_true', help='Explicitly authorize exploratory non-Mequinho PGNs without validation split')
    ob9.add_argument('--out', type=Path, required=True)

    duel9 = sub.add_parser('play-styles', help='high-depth provisional/offical duels with optional independent historical opening books')
    duel9.add_argument('white', help='mequinho-v08 or a player slug with registered Rodent profile')
    duel9.add_argument('black', help='Official Rodent player slug')
    duel9.add_argument('--rodent', type=Path)
    duel9.add_argument('--white-book', type=Path)
    duel9.add_argument('--black-book', type=Path)
    ll9 = duel9.add_mutually_exclusive_group()
    ll9.add_argument('--depth', type=int)
    ll9.add_argument('--movetime', type=float)
    duel9.add_argument('--rounds', type=int, default=2, help='Number of two-game, color-reversed pairs')
    duel9.add_argument('--opening-min-count', type=int, default=1)
    duel9.add_argument('--seed', type=int, default=2029)
    duel9.add_argument('--max-plies', type=int, default=400)
    duel9.add_argument('--out', type=Path, default=Path('partidas_chesssim'))

    qa9 = sub.add_parser('analyze-games', help='independently check match quality with Stockfish UCI')
    qa9.add_argument('--pgn', type=Path, required=True)
    qa9.add_argument('--stockfish', type=Path, required=True)
    qa9.add_argument('--depth', type=int, default=12)
    qa9.add_argument('--max-positions', type=int, default=120)
    qa9.add_argument('--min-fullmove', type=int, default=9)
    qa9.add_argument('--out', type=Path, required=True)
    qa91 = sub.add_parser('analyze-quality', aliases=['analyze-games-v091'], help='balanced game/color Stockfish analysis with PV and optional critical-depth refinement')
    qa91.add_argument('--pgn', type=Path, required=True)
    qa91.add_argument('--stockfish', type=Path, required=True)
    qa91.add_argument('--depth', type=int, default=18)
    qa91.add_argument('--max-positions', type=int, default=120)
    qa91.add_argument('--min-fullmove', type=int, default=9)
    qa91.add_argument('--sample-mode', choices=['balanced', 'spread'], default='balanced')
    qa91.add_argument('--keep-duplicate-games', action='store_true',
                       help='Evaluate duplicate positions for diagnosis only; exclude them from aggregate estimates')
    qa91.add_argument('--pv-plies', type=int, default=10)
    qa91.add_argument('--refine-depth', type=int)
    qa91.add_argument('--refine-threshold', type=int, default=100)
    qa91.add_argument('--refine-max-positions', type=int, default=20)
    qa91.add_argument('--out', type=Path, required=True)

    dq91 = sub.add_parser('compare-quality', help='audit identical PGN decisions across two or more Stockfish depths without overwriting prior evaluations')
    dq91.add_argument('--reports', type=Path, nargs='+', required=True)
    dq91.add_argument('--out', type=Path, required=True)

    paired = sub.add_parser('paired-evidence', help='compare game-clustered quality, phase-style, outcomes and exact-position choices')
    paired.add_argument('--pgn', type=Path, required=True)
    paired.add_argument('--quality', type=Path, required=True,
                        help='JSON from analyze-quality for the exact PGN')
    paired.add_argument('--out', type=Path, required=True)
    paired.add_argument('--html', type=Path,
                        help='Optional standalone offline HTML dashboard')
    paired.add_argument('--opening-plies', type=int, default=16)
    paired.add_argument('--bootstrap', type=int, default=1000)
    paired.add_argument('--seed', type=int, default=2026)

    sa9 = sub.add_parser('style-audit', help='postgame style fingerprints (opening/middlegame, by player)')
    sa9.add_argument('--pgn', type=Path, required=True)
    sa9.add_argument('--opening-plies', type=int, default=16)
    sa9.add_argument('--out', type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command in {
            "custom-player-create", "custom-player-list",
            "custom-player-export", "custom-player-import", "custom-player-retire",
            "custom-player-restore", "custom-player-rename", "custom-player-history",
        }:
            from .custom_profiles import (
                create_fictional, list_fictional, export_fictional,
                import_fictional, retire_fictional, restore_fictional,
            )
            if args.command == "custom-player-rename":
                from .custom_profile_history import rename_fictional
                result = rename_fictional(
                    args.library, args.id, args.name, args.expected_head)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            if args.command == "custom-player-history":
                from .custom_profile_history import history_fictional
                result = history_fictional(args.library, args.id)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            if args.command == "custom-player-create":
                result = create_fictional(args.library, args.name)
            elif args.command == "custom-player-list":
                result = {
                    "profiles": list_fictional(args.library),
                    "source": "LOCAL_METADATA_ONLY_NOT_ROSTER",
                }
            elif args.command == "custom-player-export":
                result = export_fictional(args.library, args.id, args.out)
            elif args.command == "custom-player-import":
                result = import_fictional(args.library, args.file)
            elif args.command == "custom-player-retire":
                result = retire_fictional(args.library, args.id)
            else:
                result = restore_fictional(args.library, args.id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "diversity-plan":
            from .diversity_experiment import export_plan
            plan = export_plan(args.players, args.out, seed=args.seed,
                               trials=args.trials,
                               declared_external_repertoire=args.external_repertoire)
            print(json.dumps({"out": str(args.out), "status": plan["status"],
                              "trials": plan["trials"],
                              "design_sha256": plan["design_sha256"],
                              "seed_effectiveness": plan["seed_effectiveness"]},
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == "diversity-run":
            from .diversity_execution import execute_plan
            result = execute_plan(
                rodent_root(args), args.plan, args.out,
                white_book=args.white_book, black_book=args.black_book,
                blocks=args.blocks, depth=args.depth, max_plies=args.max_plies,
                max_game_wall_seconds=args.max_game_wall_seconds)
            print(json.dumps({
                "out": str(args.out),
                "status": result["status"], "arms_completed": result["arms_completed"],
                "arms_planned": result["arms_planned"],
                "design_sha256": result["design_sha256"],
                "warning": result["warning"],
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "diversity-report":
            from .diversity_report import export_diversity
            result = export_diversity(args.runs, args.out, opening_plies=args.opening_plies)
            print(json.dumps({
                "out": str(args.out), "games": result["games_total"],
                "distinct_mainlines": result["distinct_mainlines"],
                "exact_duplicate_games": result["exact_duplicate_games"],
                "transposed_opening_position_groups":
                    result["transposed_opening_position_groups"],
                "warning": "Diagnóstico descritivo; nenhuma jogada do motor foi alterada.",
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "pgn-cache":
            from .licensed_pgn_cache import prepare_cache
            report = prepare_cache(args.catalog, out=args.out, apply=args.apply,
                                   max_games=args.max_games,
                                   max_cache_mb=args.max_cache_mb)
            print(json.dumps({
                "status": report["status"], "operation": report["operation"],
                "rights_status": report["rights_status"],
                "sources": report["sources"], "games_examined": report["games_examined"],
                "games_distinct": report["games_distinct"],
                "games_duplicates": report["games_duplicates"],
                "games_excluded": report["games_excluded"],
                "games_with_declared_result": report["games_with_declared_result"],
                "games_with_unknown_result": report["games_with_unknown_result"],
                "estimated_pgn_bytes": report["estimated_pgn_bytes"],
                "estimated_manifest_bytes": report["estimated_manifest_bytes"],
                "estimated_cache_bytes": report["estimated_cache_bytes"],
                "max_disk_bytes": report["max_disk_bytes"],
                "cache_pgn_sha256": report["cache_pgn_sha256"],
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "age-cohort":
            from .age_cohort import build_age_cohort
            report = build_age_cohort(
                args.cache, args.out, pgn_name=args.pgn_name,
                birth_date=args.birth_date, age=args.age,
                min_train=args.min_train)
            print(json.dumps({
                "out": str(args.out), "status": report["status"],
                "age": report["age_completed_years"],
                "eligible_games": report["eligible_exact_age_unique_games"],
                "train_games": report["partitions"]["train"]["games"],
                "validation_games": report["partitions"]["validation"]["games"],
                "holdout_games": report["partitions"]["holdout"]["games"],
                "profile_available": report["profile_available"],
                "train_pgn_sha256": report["train_pgn_sha256"],
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "fast-audit":
            from .fast_statistical_audit import export_audit
            info = export_audit(args.workspace, args.out)
            print(json.dumps({
                "out": str(args.out), "status": info["status"],
                "verified_uci_games": info["verified_uci_games"],
                "verified_uci_seasons": info["verified_uci_seasons"],
                "rejected_seasons": info["rejected_seasons"],
                "excluded_synthetic_projections": info["excluded_synthetic_projections"],
                "model_status": info["model_status"],
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "rating-export":
            from .competition_rating import export_rating_ledger
            info = export_rating_ledger(args.workspace, args.out, k=args.k,
                                        replace=args.replace,
                                        modality=args.modality)
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'opening-build':
            from .opening_books import build_opening_book
            player = find_player(args.player)
            name = args.match_name or ('Mecking' if player.slug == 'mequinho' else player.name.split()[-1])
            result = build_opening_book(args.pgn, args.out, slug=player.slug, match_name=name,
                max_plies=args.max_plies, from_year=args.from_year, to_year=args.to_year,
                v5_report=args.v5_report, legacy_v3=args.legacy_v3, legacy_pgn=args.legacy_pgn,
                cohort_ledger=args.cohort_ledger, ack_unpartitioned=args.ack_unpartitioned)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == 'play-styles':
            from .duel_styles import play_styles
            depth = 10 if args.movetime is None and args.depth is None else args.depth
            result = play_styles(rodent_root(args), args.out, white_name=args.white,
                black_name=args.black, white_book=args.white_book, black_book=args.black_book,
                depth=depth, movetime=args.movetime, rounds=args.rounds, seed=args.seed,
                opening_min_count=args.opening_min_count, max_plies=args.max_plies)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == 'analyze-games':
            from .quality_baseline import analyze_games
            result = analyze_games(args.pgn, args.stockfish, args.out, depth=args.depth,
                max_positions=args.max_positions, min_fullmove=args.min_fullmove)
            print(json.dumps({'out': str(args.out), 'status': result['status'],
                'summary': result['summary']}, ensure_ascii=False, indent=2))
        elif args.command in ('analyze-quality', 'analyze-games-v091'):
            from .quality_audit import analyze_games_v91
            result = analyze_games_v91(args.pgn, args.stockfish, args.out, depth=args.depth,
                max_positions=args.max_positions, min_fullmove=args.min_fullmove,
                sample_mode=args.sample_mode, dedupe_games=not args.keep_duplicate_games,
                pv_plies=args.pv_plies, refine_depth=args.refine_depth,
                refine_threshold=args.refine_threshold, refine_max_positions=args.refine_max_positions)
            print(json.dumps({'out': str(args.out), 'status': result['status'],
                'sampled_positions': result['inputs']['sampled_positions'],
                'excluded_duplicate_games': len(result['excluded_duplicate_games']),
                'diversity': result['diversity'],
                'independent_decisions_sampled': result['independent_decisions_sampled'],
                'summary': result['summary']}, ensure_ascii=False, indent=2))
        elif args.command == 'compare-quality':
            from .quality_audit import compare_quality_reports
            result = compare_quality_reports(args.reports, args.out)
            print(json.dumps({'out': str(args.out), 'status': result['status'],
                'depths': result['depths'], 'aligned_decisions': result['aligned_decisions'],
                'diagnostic_duplicate_aligned': result['diagnostic_duplicate_aligned'],
                'classification_changes': result['classification_changes'],
                'classification_changes_at_any_step': result['classification_changes_at_any_step'],
                'classification_change_rate': result['classification_change_rate'],
                'mean_cp_loss_range': result['mean_cp_loss_range'],
                'largest_changes': result['largest_changes'][:5]}, ensure_ascii=False, indent=2))
        elif args.command == 'paired-evidence':
            from .paired_evidence import paired_evidence
            result = paired_evidence(args.pgn, args.quality, args.out,
                                     html=args.html, opening_plies=args.opening_plies,
                                     bootstrap=args.bootstrap, seed=args.seed)
            print(json.dumps({
                'out': str(args.out), 'html': str(args.html) if args.html else None,
                'status': result['status'],
                'distinct_games': result['accounting']['distinct_nonempty_games'],
                'duplicate_games_excluded': result['accounting']['duplicate_games_excluded'],
                'pairwise': [{
                    'a': item['a'], 'b': item['b'],
                    'paired_strength_games': item['strength_paired_by_game']['games'],
                    'mean_cp_loss_difference': item['strength_paired_by_game']['mean'],
                    'exact_fen_positions': item['same_position_choices']['exact_fen_positions']
                } for item in result['pairwise']]
            }, ensure_ascii=False, indent=2))
        elif args.command == 'style-audit':
            from .style_postgame import fingerprint
            result = fingerprint(args.pgn, args.out, opening_plies=args.opening_plies)
            print(json.dumps({'out': str(args.out), 'games': result['games'],
                'distinct_games': result['distinct_games'],
                'duplicate_games': result['duplicate_games'],
                'players': result['players'], 'pairwise': result['pairwise']}, ensure_ascii=False, indent=2))
        elif args.command == 'temporal-compare':
            from .temporal_paired import temporal_compare
            player=find_player(args.player)
            match_name=args.match_name or ('Mecking' if player.slug=='mequinho' else None)
            if not match_name:raise ValueError('Pass --match-name')
            doc=temporal_compare(rodent_root(args),args.pgn,args.peak_registry,args.out,
                slug=player.slug,match_name=match_name,v5_report=args.v5_report,
                legacy_v3=args.legacy_v3,legacy_pgn=args.legacy_pgn,
                allow_provisional=args.allow_provisional,include_unknown=args.include_unknown,
                annotations=args.annotations,cohort_ledger=args.cohort_ledger,
                positions=args.positions,train_budget=args.train_budget,depth=args.depth,
                folds=args.folds,repeats=args.repeats,trials=args.trials,
                probe_positions=args.probe_positions,seed=args.search_seed,
                penalty=args.divergence_penalty,base_files=tuple(args.base_files))
            print(f"Paired delta peak minus career: {doc['summary']['mean_delta_pp']:+.2f} pp; {args.out}")
            print('Selection and ALL historic tests remain SEALED.')
        elif args.command == 'tournament-evidence':
            from .time_control_evidence import SOURCES,match_tournament_evidence
            if args.sources:
                print(json.dumps(SOURCES,ensure_ascii=False,indent=2))
            else:
                if not (args.source_id and args.source_pgn and args.pgn and args.out):
                    raise ValueError('Require --source-id --source-pgn --pgn --out, or --sources')
                result=match_tournament_evidence(args.pgn,args.source_pgn,args.out,
                   source_id=args.source_id,match_name=args.match_name,annotations=args.annotations)
                print(json.dumps(result,ensure_ascii=False,indent=2))
        elif args.command == 'corpus-annex-plan':
            from .corpus_annex import plan_annex
            player=find_player(args.player)
            name=args.match_name or ('Mecking' if player.slug=='mequinho' else None)
            if not name:raise ValueError('Pass --match-name')
            report=plan_annex(args.pgn,args.sources,args.out,match_name=name,
                v5_report=args.v5_report,legacy_v3=args.legacy_v3,
                legacy_pgn=args.legacy_pgn,slug=player.slug,
                cohort_ledger=args.cohort_ledger,seed=args.seed)
            print(json.dumps(report,ensure_ascii=False,indent=2))
        elif args.command == 'provisional-install':
            from .provisional_profiles import provisional_install
            report = provisional_install(rodent_root(args), args.profile, apply=args.apply)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if not args.apply:
                print('Prévia apenas; para registrar de verdade, acrescente --apply.')
        elif args.command == 'replay-html':
            from .replay_html import export_replay_html
            print(json.dumps(export_replay_html(args.pgn, args.out), ensure_ascii=False, indent=2))
        elif args.command == 'play-provisional':
            from .provisional_profiles import play_provisional
            depth = 5 if args.movetime is None and args.preset is None and args.depth is None else args.depth
            folder = play_provisional(rodent_root(args), args.out, opponent=args.opponent, reverse=args.reverse,
                                      depth=depth, movetime=args.movetime, preset=args.preset,
                                      max_plies=args.max_plies)
            print(f'Duelo provisório concluído: {folder}')
        elif args.command == 'temporal-controlled':
            from .temporal_controlled import controlled_temporal_compare
            player=find_player(args.player)
            match_name=args.match_name or ('Mecking' if player.slug=='mequinho' else None)
            if not match_name: raise ValueError('Pass --match-name')
            doc=controlled_temporal_compare(rodent_root(args), args.pgn, args.peak_registry, args.out,
                slug=player.slug, match_name=match_name, v5_report=args.v5_report,
                legacy_v3=args.legacy_v3, legacy_pgn=args.legacy_pgn,
                allow_provisional=args.allow_provisional, include_unknown=args.include_unknown,
                annotations=args.annotations, cohort_ledger=args.cohort_ledger,
                positions=args.positions, train_budget=args.train_budget, depth=args.depth,
                folds=args.folds, repeats=args.repeats, trials=args.trials,
                probe_positions=args.probe_positions, seed=args.search_seed,
                penalty=args.divergence_penalty, max_train_games=args.max_train_games,
                max_positions_per_game=args.max_positions_per_game,
                peak_fractions=tuple(args.peak_fractions), base_files=tuple(args.base_files))
            best=doc['recommendation']['best_arm']
            acc=doc['recommendation']['mean_accuracy_pct']
            print(f"Melhor braço exploratório: {best} | média={acc:.2f}% | relatório: {args.out}")
            print('Perfis e testes históricos finais continuam LACRADOS.')
        elif args.command == 'style-signature':
            from .style_signature import style_signature_report
            player=find_player(args.player)
            match_name=args.match_name or ('Mecking' if player.slug=='mequinho' else None)
            if not match_name: raise ValueError('Pass --match-name')
            report=style_signature_report(args.pgn, args.peak_registry, args.out, slug=player.slug,
                                          match_name=match_name, allow_provisional=args.allow_provisional)
            peak_games=report['signatures']['peak']['games']
            career_games=report['signatures']['career']['games']
            print(f"Assinaturas geradas | carreira={career_games} jogos | auge={peak_games} jogos | {args.out}")
        elif args.command == 'build-provisional':
            from .temporal_controlled import build_provisional_profile_v8
            player=find_player(args.player)
            match_name=args.match_name or ('Mecking' if player.slug=='mequinho' else None)
            if not match_name: raise ValueError('Pass --match-name')
            report=build_provisional_profile_v8(rodent_root(args), args.pgn, args.peak_registry, args.out,
                slug=player.slug, match_name=match_name, v5_report=args.v5_report,
                legacy_v3=args.legacy_v3, legacy_pgn=args.legacy_pgn,
                allow_provisional=args.allow_provisional, include_unknown=args.include_unknown,
                annotations=args.annotations, cohort_ledger=args.cohort_ledger,
                peak_fraction=args.peak_fraction, train_budget=args.train_budget, depth=args.depth,
                trials=args.trials, probe_positions=args.probe_positions, seed=args.search_seed,
                penalty=args.divergence_penalty, max_train_games=args.max_train_games,
                max_positions_per_game=args.max_positions_per_game, base_files=tuple(args.base_files))
            print(f"Perfil provisório gerado: {args.out / report['profile_file']}")
            print('Esse perfil já é jogável, mas ainda NÃO é validado historicamente.')
        elif args.command == "protocol":
            from .research_sealed import PROTOCOL_V3
            from .research_search import PROTOCOL_V4
            from .research_crossvalidation import PROTOCOL_V5
            from .research_peak import PROTOCOL_V6
            from .temporal_paired import PROTOCOL as PROTOCOL_V7
            from .temporal_controlled import PROTOCOL as PROTOCOL_V8
            from .style_signature import PROTOCOL as SIGNATURE_V8
            from .opening_books import SCHEMA as OPENING_V9
            print(json.dumps({"official": OFFICIAL.json(), "calibration_v3": PROTOCOL_V3, "calibration_v4": PROTOCOL_V4, "calibration_v5": PROTOCOL_V5, "calibration_v6": PROTOCOL_V6, "temporal_paired": PROTOCOL_V7, "temporal_controlled": PROTOCOL_V8, "style_signature": SIGNATURE_V8, "opening_v9": OPENING_V9},
                             indent=2, ensure_ascii=False))
        elif args.command == "peak-annotations-init":
            from .research_peak import write_annotation_template
            player=find_player(args.player)
            name=args.match_name or ("Mecking" if player.slug=="mequinho" else player.name.split()[-1])
            print(json.dumps(write_annotation_template(args.pgn,args.peak_registry,args.out,
                 slug=player.slug,match_name=name,allow_provisional=args.allow_provisional),
                 ensure_ascii=False,indent=2))
        elif args.command == "peak-windows-init":
            from .peak_windows import default_peak_registry
            if args.out.exists():
                raise FileExistsError(f"Arquivo já existe; nunca sobrescrever janelas históricas: {args.out}")
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(json.dumps(default_peak_registry(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            print(f"64 jogadores registrados: {args.out} (4 PROVISIONAL; restantes UNREVIEWED)")
        elif args.command == "peak-audit":
            from .research_peak import peak_audit
            player=find_player(args.player)
            name=args.match_name or ("Mecking" if player.slug=="mequinho" else player.name.split()[-1])
            report=peak_audit(args.pgn,args.peak_registry,slug=player.slug,match_name=name,
                              allow_provisional=args.allow_provisional,annotations=args.annotations)
            if args.out:
                if args.out.exists(): raise FileExistsError(args.out)
                args.out.parent.mkdir(parents=True,exist_ok=True)
                args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            print(json.dumps(report,ensure_ascii=False,indent=2))
        elif args.command == "calibrate-v6":
            from .research_peak import calibrate_v6
            from .research_search import DEFAULT_PROBES
            from datetime import datetime, timezone
            player=find_player(args.player)
            name=args.match_name or ("Mecking" if player.slug=="mequinho" else player.name.split()[-1])
            out=args.out or Path("calibracoes")/(player.slug+"-v06-"+args.mode+"-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            report=calibrate_v6(rodent_root(args),args.pgn,out,args.peak_registry,
                 slug=player.slug,match_name=name,mode=args.mode,
                 allow_provisional=args.allow_provisional,include_unknown=args.include_unknown,
                 annotations=args.annotations,v5_report=args.v5_report,
                 legacy_v3=args.legacy_v3,legacy_pgn=args.legacy_pgn,
                 cohort_ledger=args.cohort_ledger,fresh_cohort_ack=args.fresh_cohort_ack,
                 partition_seed=args.partition_seed,search_seed=args.search_seed,
                 depth=args.depth,positions=args.positions,folds=args.folds,repeats=args.repeats,
                 cv_trials=args.cv_trials,trials=args.trials,probe_positions=args.probe_positions,
                 delta=args.delta,divergence_penalty=args.divergence_penalty,
                 base_files=tuple(args.base_files),
                 probe_params=tuple(args.probe_params) if args.probe_params else DEFAULT_PROBES)
            cv=report["cv_summary"]
            print(f"v0.6: {report['status']}; {args.mode}; CV={cv['mean_gain_pp']:+.2f} pp; "
                  f"base={report['origin']}")
            print(f"Relatório: {out/'calibration.json'}")
            print("Seleção/TESTE FINAL não consultados. Perfil NÃO homologado.")
        elif args.command == "fetch-mecking":
            from .historical import import_mecking
            print(json.dumps(import_mecking(args.out), indent=2, ensure_ascii=False))
        elif args.command == "audit":
            from .audit import audit_roster
            from datetime import datetime, timezone
            out = args.out or Path("auditorias") / (("full-" if args.full else "quick-") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            report = audit_roster(rodent_root(args), out, depth=args.depth,
                                  full=args.full, both_colors=not args.one_color,
                                  max_plies=args.max_plies, use_books=(args.full or args.books))
            print(f"Resultado: {report['passed']}/21 | {out}/audit.json")
            return 0 if report["passed"] == 21 else 3
        elif args.command == "corpus-sources":
            from .corpus_collection import source_catalog, roster_sources
            catalog = source_catalog()
            rows = roster_sources(catalog)
            report={"source":catalog["index_url"],"indexed_at":catalog["retrieved_utc"],
                    "index_sha256":catalog["index_sha256"],"available":sum(x["available"] for x in rows),
                    "roster_size":len(rows),"players":rows}
            print(json.dumps(report,ensure_ascii=False,indent=2))
            if args.out:
                args.out.parent.mkdir(parents=True,exist_ok=True)
                args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        elif args.command == "corpus-fetch":
            from .corpus_collection import fetch_profiles
            report = fetch_profiles(args.out,slugs=args.players,dry_run=args.dry_run,delay=args.delay)
            print(json.dumps(report,ensure_ascii=False,indent=2))
        elif args.command == "corpus-build":
            from .corpus_collection import make_corpus
            player=find_player(args.player)
            match_name=args.match_name or ("Mecking" if player.slug=="mequinho" else None)
            if not match_name:
                raise ValueError("Forneça --match-name (identificador do PGN)")
            out=args.out or Path("datasets/corpus")/(player.slug+"-all-available.pgn")
            print(json.dumps(make_corpus(args.sources,out,match_name=match_name),ensure_ascii=False,indent=2))
        elif args.command == "calibrate-v5":
            from .research_crossvalidation import calibrate_v5
            from .research_search import DEFAULT_PROBES
            from datetime import datetime, timezone
            player=find_player(args.player)
            match_name=args.match_name or ("Mecking" if player.slug=="mequinho" else None)
            if not match_name:
                raise ValueError("Forneça --match-name (identificador do PGN)")
            out=args.out or Path("calibracoes")/(player.slug+"-v05-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            report=calibrate_v5(rodent_root(args),args.pgn,out,target=player.name,target_slug=player.slug,
                match_name=match_name,base_files=tuple(args.base_files),legacy_v3=args.legacy_v3,
                legacy_pgn=args.legacy_pgn,from_year=args.from_year,to_year=args.to_year,
                partition_seed=args.partition_seed,search_seed=args.search_seed,
                train_positions=args.train_positions,val_positions=args.val_positions,test_positions=args.test_positions,
                depth=args.depth,folds=args.folds,repeats=args.repeats,cv_trials=args.cv_trials,
                trials=args.trials,shortlist=args.shortlist,probe_positions=args.probe_positions,
                probe_params=tuple(args.probe_params) if args.probe_params else DEFAULT_PROBES,
                delta=args.delta,divergence_penalty=args.divergence_penalty,select=args.select,
                cohort_ledger=args.cohort_ledger,validation_ledger=args.validation_ledger)
            cv=report["cv_summary"]
            print(f"v0.5: {report['status']}; CV média={cv['mean_gain_pp']:+.2f} pp; "
                  f"positivas={cv['positive_repeats']}/{cv['repeats']}; "
                  f"base de treino={report['training_origin']}")
            print(f"Relatório: {out / 'calibration.json'}")
            print("TESTE FINAL permanece LACRADO. Não executar final-eval.")
        elif args.command == "calibrate-v4":
            from .research_search import calibrate_v4, DEFAULT_PROBES
            from datetime import datetime, timezone
            player = find_player(args.player)
            name = args.match_name or ("Mecking" if player.slug == "mecking" else None)
            if not name:
                raise ValueError("Informe --match-name conforme o PGN")
            out = args.out or Path("calibracoes") / (player.slug + "-v04-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            report = calibrate_v4(rodent_root(args), args.pgn, out,
                target=player.name, target_slug=player.slug, match_name=name,
                base_files=tuple(args.base_files), partition_seed=args.partition_seed,
                search_seed=args.search_seed, from_year=args.from_year,
                to_year=args.to_year, train_positions=args.train_positions,
                val_positions=args.val_positions, test_positions=args.test_positions,
                split_by=args.split_by, depth=args.depth, trials=args.trials,
                shortlist=args.shortlist, folds=args.folds, cv_trials=args.cv_trials,
                delta=args.delta, probe_positions=args.probe_positions,
                probe_params=tuple(args.probe_params) if args.probe_params else DEFAULT_PROBES,
                divergence_penalty=args.divergence_penalty,
                sealed_reference=args.sealed_reference, ledger=args.ledger)
            selection = report["selection"]
            print(f"v0.4: {report['status']}; base de treino={selection['starting_base_train_only']}; "
                  f"melhor base na validação={selection['strongest_baseline_validation']}; "
                  f"seleção={selection['selected_trial']}")
            print(f"Relatório: {out / 'calibration.json'}")
            print("TESTE FINAL permanece LACRADO. NÃO executar final-eval neste run.")
        elif args.command == "sensitivity":
            from .research_sealed import sensitivity_v3
            report = sensitivity_v3(rodent_root(args), args.pgn, args.out,
                                    match_name=args.match_name, base_file=args.base_file,
                                    seed=args.seed, depth=args.depth, positions=args.positions,
                                    delta=args.delta, from_year=args.from_year,
                                    to_year=args.to_year, split_by=args.split_by)
            print(f"Sensibilidade treino-only: {len(report['sensitivity'])} perturbações | {args.out}")
        elif args.command == "final-eval":
            from .final_validation import final_evaluate
            report = final_evaluate(rodent_root(args), args.run,
                                    stockfish=args.stockfish,
                                    stockfish_depth=args.stockfish_depth,
                                    comparisons=args.compare)
            print(f"TESTE FINAL ABERTO UMA VEZ: baseline={report['baseline']['top1']:.1%} "
                  f"selected={report['selected']['top1']:.1%}; "
                  f"gain={report['gain']:+.1%} | {args.run / 'final_test.json'}")
        elif args.command == "benchmark":
            from datetime import datetime, timezone
            from .strength_benchmark import run_benchmark
            out = args.out or Path("benchmarks") / ("pair-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            report = run_benchmark(rodent_root(args), args.profile, out,
                                   opponent=args.opponent, depth=args.depth,
                                   openings=args.openings, max_plies=args.max_plies)
            print(f"Benchmark {report['completed']}/{report['scheduled']} concluídos | "
                  f"{report['candidate_points']} pontos | {out}")
        elif args.command == "calibrate":
            from .research_sealed import calibrate_v3
            from datetime import datetime, timezone
            player = find_player(args.player)
            match_name = args.match_name or ("Mecking" if player.slug == "mecking" else None)
            if not match_name:
                raise ValueError("Forneça --match-name como escrito nos PGNs")
            from_year = args.from_year if args.from_year is not None else (1973 if player.slug == "mecking" else 1800)
            to_year = args.to_year if args.to_year is not None else (1978 if player.slug == "mecking" else 2026)
            out = args.out or Path("calibracoes") / (player.slug + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            report = calibrate_v3(rodent_root(args), args.pgn, out, target=player.name,
                     target_slug=player.slug, match_name=match_name, base_file=args.base_file,
                     from_year=from_year, to_year=to_year, seed=args.seed,
                     depth=args.depth, trials=args.trials, train_positions=args.train_positions,
                     val_positions=args.val_positions, test_positions=args.test_positions,
                     shortlist=args.shortlist, split_by=args.split_by)
            select = report['selection']
            print(f"Seleção v0.3: tentativa {select['selected_trial']} | "
                  f"validação {select['selected_correct']}/{report['split']['validation']['positions']} "
                  f"(base: {select['baseline_correct']})")
            print(f"Candidato preservado: {report['selected_profile']}")
            print(f"TESTE FINAL LACRADO — não foi consultado. Para abri-lo UMA VEZ: "
                  f"python3 -m chesssim final-eval --run {out}")
        elif args.command == "roster":
            for p in load_roster():
                status = f"Rodent IV: {p.rodent_alias}" if p.rodent_alias else "PENDENTE: calibrar perfil"
                print(f"{p.number:02d}. {p.name:<29} {GROUP_LABELS[p.group]:<38} {status}")
            print("\n64 selecionados | 21 perfis oficiais identificados | 43 a construir")
        elif args.command == "check":
            rows = profile_status(rodent_root(args))
            for name, alias, state in rows:
                print(f"{name:<29} {alias:<12} {state}")
            print("\n", len([r for r in rows if r[2] == "PRONTO_PARA_TESTE"]), "/21 perfis existentes e registrados", sep="")
        elif args.command == "setup":
            report = setup_official(rodent_root(args), apply=args.apply, books=args.with_books)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            print("\n" + ("Instalação aplicada; confira com 'check'." if args.apply else "Prévia apenas: nada foi modificado. Para aplicar: setup --apply --with-books"))
        elif args.command == "draw":
            result = generate_draw(args.seed)
            by_slug = {p.slug: p.name for p in load_roster()}
            for match in result["round_1"]:
                print(f"{match['match']:02d}: {by_slug[match['player_a']]}  vs  {by_slug[match['player_b']]}")
            if args.out:
                if args.out.exists():
                    raise FileExistsError(f"Recusado sobrescrever sorteio existente: {args.out}")
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"Sorteio salvo em {args.out}")
        elif args.command == "arena":
            from .arena import serve
            serve(rodent_root(args), args.out, port=args.port,
                  opening_books=args.opening_books,
                  max_game_wall_seconds=args.max_game_wall_seconds)
        elif args.command == "watch":
            from .live_board import serve
            serve(args.run, port=args.port, quality_report=args.quality_report)
        elif args.command == "tournament-init":
            from .tournament import create_tournament, tournament_status
            create_tournament(rodent_root(args), args.out, participants=args.players,
                              seed=args.seed, preset=args.preset,
                              max_tiebreak_pairs=args.max_tiebreak_pairs,
                              max_plies=args.max_plies,
                              max_game_wall_seconds=args.max_game_wall_seconds)
            print(json.dumps(tournament_status(args.out), indent=2, ensure_ascii=False))
        elif args.command == "tournament-run":
            from .tournament import run_tournament
            result = run_tournament(rodent_root(args), args.run,
                                    limit_pairs=args.limit_pairs,
                                    retry_incomplete=args.retry_incomplete,
                                    max_run_seconds=args.max_run_seconds)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["status"] in {"PAUSED", "COMPLETED"} else 3
        elif args.command == "tournament-status":
            from .tournament import tournament_status
            print(json.dumps(tournament_status(args.run), indent=2, ensure_ascii=False))
        elif args.command == "tournament-recovery-preview":
            from .tournament import tournament_recovery_preview
            print(json.dumps(tournament_recovery_preview(args.run),
                             indent=2, ensure_ascii=False))
        elif args.command == "tournament-stop":
            from .tournament import request_stop
            print(json.dumps(request_stop(args.run, reason=args.reason),
                             indent=2, ensure_ascii=False))
        elif args.command == "tournament-clear-stop":
            from .tournament import clear_stop
            print(json.dumps(clear_stop(args.run), indent=2, ensure_ascii=False))
        elif args.command == "tournament-resolve":
            from .tournament import resolve_tiebreak
            print(json.dumps(resolve_tiebreak(args.run, args.match, args.winner,
                                               args.reason), indent=2, ensure_ascii=False))
        elif args.command == "play":
            a, b = find_player(args.white), find_player(args.black)
            if a.slug == b.slug:
                raise ValueError("Não é permitido jogar consigo mesmo neste estágio")
            from .matches import play_duel
            if args.no_books and args.preset == "official":
                raise ValueError("--no-books é incompatível com o preset official, que exige livros")
            if args.preset and (args.depth is not None or args.movetime is not None):
                raise ValueError("Não misture --preset official e limites de desenvolvimento")
            if args.preset and not args.reverse:
                raise ValueError("Confronto de preset requer --reverse (ambas as cores)")
            from .peak_windows import CLOCK_PRESETS
            clock = (600, 2) if args.preset == "official" else CLOCK_PRESETS.get(args.preset)
            depth = None if args.preset or args.movetime is not None else (args.depth if args.depth is not None else 6)
            play_duel(rodent_root(args), args.out, a, b, depth=depth,
                      movetime=args.movetime, reverse=args.reverse, max_plies=args.max_plies or (600 if args.preset else 400),
                      clock=clock, use_books=(False if args.no_books else
                                             True if args.preset == "official" else
                                             False if args.preset else None))
        return 0
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError, OSError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
