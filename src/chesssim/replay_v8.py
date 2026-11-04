"""Offline HTML chessboard replay for PGNs created by ChessSimulation64.

No external CDNs, scripts, remote requests, servers or chess engines.
The complete state sequence is produced by python-chess before rendering.
"""
from __future__ import annotations

import json
from pathlib import Path


def _json_script(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def export_replay_html(pgn: Path, out: Path) -> dict:
    import chess
    import chess.pgn

    if out.exists():
        raise FileExistsError(out)
    if not pgn.is_file():
        raise FileNotFoundError(pgn)
    games = []
    with pgn.open(encoding="utf-8-sig", errors="replace") as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            if game.errors:
                raise ValueError("PGN contém uma partida inválida; recuse a reprodução")
            board = game.board()
            states = [{"fen": board.fen(), "san": "Início", "last": None}]
            for move in game.mainline_moves():
                if move not in board.legal_moves:
                    raise ValueError("Lance ilegal no PGN")
                san = board.san(move)
                uci = move.uci()
                board.push(move)
                states.append({"fen": board.fen(), "san": san, "last": [uci[:2], uci[2:4]]})
            games.append({"white": game.headers.get("White", "Brancas"),
                          "black": game.headers.get("Black", "Pretas"),
                          "result": game.headers.get("Result", "*"),
                          "event": game.headers.get("Event", "Partida"),
                          "states": states})
    if not games:
        raise ValueError("Nenhuma partida PGN encontrada")

    html = r'''<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChessSimulation64 — Replay offline</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0d171e;color:#eff6f4}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:22px}
main{width:min(1120px,100%)}header{margin-bottom:19px}h1{font-size:1.65rem;margin:0 0 5px}
small,.muted{color:#aec1c6}article{display:grid;grid-template-columns:minmax(300px,640px) minmax(230px,1fr);gap:24px}
@media(max-width:740px){article{grid-template-columns:1fr}}
.board{display:grid;grid-template-columns:repeat(8,1fr);aspect-ratio:1/1;border:4px solid #31505e;border-radius:10px;overflow:hidden}
.sq{display:grid;place-items:center;font-size:clamp(25px,5.4vw,62px);line-height:1;user-select:none;position:relative}
.light{background:#d7e2d5;color:#162635}.dark{background:#72999b;color:#142a35}
.sq.last{box-shadow:inset 0 0 0 4px #dbb858}
.whitepiece{color:#fff;text-shadow:0 2px 2px #000,1px 0 2px #000,-1px 0 2px #000}
.blackpiece{color:#111f2b;text-shadow:0 1px 1px #fff}
.panel{background:#182930;border:1px solid #345158;border-radius:12px;padding:20px;align-self:start}
.panel h2{font-size:1.1rem;margin:16px 0 8px;overflow-wrap:anywhere}
.controls{display:flex;gap:7px;flex-wrap:wrap;margin:15px 0}
button,select{font:inherit;background:#244a59;color:#fff;border:1px solid #507580;border-radius:8px;padding:9px 12px;cursor:pointer}
button:hover{background:#32687a}select{max-width:100%;width:100%}
#move{font-size:1.7rem;font-weight:bold;min-height:45px}input[type=range]{width:100%}
#status{color:#aec1c6;font-size:.92rem}footer{margin-top:15px;font-size:.8rem;color:#aec1c6}
</style></head><body><main><header><h1>♟️ Copa dos Imortais — Replay</h1>
<small>Visualizador local. Os lances já foram calculados; nenhuma partida é gerada nesta página.</small></header>
<article><div><div id="board" class="board" aria-label="Tabuleiro de xadrez"></div></div>
<div class="panel"><label for="games" class="muted">Partida</label><select id="games"></select>
<h2 id="players"></h2><div class="muted" id="result"></div><hr>
<div id="move">Início</div><div class="muted" id="status"></div>
<input type="range" id="seek" min="0" value="0" step="1" aria-label="Lance">
<div class="controls"><button id="start">⏮ Início</button><button id="prev">◀ Anterior</button>
<button id="play">▶ Reproduzir</button><button id="next">Próximo ▶</button><button id="end">Fim ⏭</button></div>
<label for="speed" class="muted">Velocidade</label><select id="speed">
<option value="2000">2 segundos por lance</option><option value="1000" selected>1 segundo por lance</option><option value="500">0,5 segundo por lance</option></select>
</div></article><footer>ChessSimulation64 • v0.8.1 • arquivo HTML independente • perfis experimentais não homologados</footer>
</main>
<script>
"use strict";
const data=__CHESS_DATA__;
const glyph={K:"♔",Q:"♕",R:"♖",B:"♗",N:"♘",P:"♙",k:"♚",q:"♛",r:"♜",b:"♝",n:"♞",p:"♟"};
const sq=document.getElementById("board"),gameSel=document.getElementById("games"),seek=document.getElementById("seek");
const moveEl=document.getElementById("move"),status=document.getElementById("status"),player=document.getElementById("players"),result=document.getElementById("result"), playBtn=document.getElementById("play");
let gameIndex=0,ply=0,timer=null;
function stop(){if(timer!==null){clearInterval(timer);timer=null}playBtn.textContent="▶ Reproduzir"}
function chessSquares(fen){const ranks=fen.split(" ")[0].split("/");const rows=[];for(let r=0;r<8;r++){let row=[];for(const c of ranks[r]){if(/[1-8]/.test(c)){for(let j=0;j<Number(c);j++)row.push("")}else row.push(c)}rows.push(row)}return rows}
function paint(){const game=data[gameIndex],state=game.states[ply];seek.max=String(game.states.length-1);seek.value=String(ply);
  const ranks=chessSquares(state.fen);sq.replaceChildren();for(let r=0;r<8;r++)for(let f=0;f<8;f++){
    const square=String.fromCharCode(97+f)+String(8-r);const piece=ranks[r][f];const box=document.createElement("div");
    box.className="sq "+(((r+f)%2)?"dark":"light")+(state.last&&state.last.includes(square)?" last":"");
    const txt=document.createElement("span");txt.textContent=glyph[piece]||"";txt.className=piece===piece.toUpperCase()?"whitepiece":"blackpiece";box.appendChild(txt);
    box.setAttribute("aria-label",square+" "+(piece||"vazio"));sq.appendChild(box)}
  player.textContent=game.white+" × "+game.black;result.textContent="Resultado: "+game.result+" • "+game.event;
  moveEl.textContent=ply===0?"Posição inicial":(Math.ceil(ply/2)+(ply%2?". ":"... ")+state.san);
  status.textContent="Meio-lance "+ply+" de "+(game.states.length-1);
}
function go(next){ply=Math.max(0,Math.min(data[gameIndex].states.length-1,next));paint()}
function start(){stop();const interval=Number(document.getElementById("speed").value);playBtn.textContent="⏸ Pausar";
 timer=setInterval(()=>{if(ply>=data[gameIndex].states.length-1){stop();return}go(ply+1)},interval)}
for(const [i,g] of data.entries()){const o=document.createElement("option");o.value=String(i);o.textContent="Jogo "+(i+1)+": "+g.white+" × "+g.black;gameSel.append(o)}
gameSel.onchange=()=>{stop();gameIndex=Number(gameSel.value);go(0)};
seek.oninput=()=>{stop();go(Number(seek.value))};
document.getElementById("start").onclick=()=>{stop();go(0)};
document.getElementById("end").onclick=()=>{stop();go(data[gameIndex].states.length-1)};
document.getElementById("prev").onclick=()=>{stop();go(ply-1)};
document.getElementById("next").onclick=()=>{stop();go(ply+1)};
playBtn.onclick=()=>timer!==null?stop():start();
document.getElementById("speed").onchange=()=>{if(timer!==null)start()};
paint();
</script></body></html>'''
    html = html.replace('__CHESS_DATA__', _json_script(games))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return {"games": len(games), "plies": [len(g['states']) - 1 for g in games], "out": str(out)}
