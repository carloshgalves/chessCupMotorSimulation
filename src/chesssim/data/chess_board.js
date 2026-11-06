"use strict";
/* Shared visual renderer for watch and the History replay. No game rules or engines. */
(() => {
  const glyph = {
    K:"♚",Q:"♛",R:"♜",B:"♝",N:"♞",P:"♟",
    k:"♚",q:"♛",r:"♜",b:"♝",n:"♞",p:"♟"
  };
  const names = {
    K:"rei branco",Q:"dama branca",R:"torre branca",B:"bispo branco",
    N:"cavalo branco",P:"peão branco",
    k:"rei preto",q:"dama preta",r:"torre preta",b:"bispo preto",
    n:"cavalo preto",p:"peão preto"
  };
  function parseFen(fen) {
    if (typeof fen !== "string" || fen.length > 128) return null;
    const parts = fen.split(" ");
    if (parts.length !== 6 || !["w","b"].includes(parts[1])) return null;
    const ranks = parts[0].split("/");
    if (ranks.length !== 8) return null;
    const rows = [];
    for (const rank of ranks) {
      if (!/^[prnbqkPRNBQK1-8]+$/.test(rank)) return null;
      const row = [];
      for (const character of rank) {
        if (/^[1-8]$/.test(character)) {
          for (let i=0; i<Number(character); i++) row.push("");
        } else {
          row.push(character);
        }
      }
      if (row.length !== 8) return null;
      rows.push(row);
    }
    return rows;
  }
  function paint(container, position, ply=0) {
    const rows = position && parseFen(position.fen);
    // Fail closed: an invalid replay must never leave a previous game's board visible.
    if (!rows || !container) {
      if (container) {
        container.replaceChildren();
        container.setAttribute("aria-label", "Posição do tabuleiro inválida");
      }
      return false;
    }
    const last = (Array.isArray(position.last) && position.last.length === 2 &&
                  position.last.every(s=>typeof s==="string" && /^[a-h][1-8]$/.test(s)))
      ? position.last : [];
    const fragment = document.createDocumentFragment();
    for (let rank = 0; rank < 8; rank++) {
      for (let file = 0; file < 8; file++) {
        const squareName = String.fromCharCode(97 + file) + String(8 - rank);
        const piece = rows[rank][file];
        const square = document.createElement("div");
        square.className = "square " + ((rank + file) % 2 ? "dark" : "light");
        if (last.includes(squareName)) square.classList.add("last");
        if (piece) square.classList.add(piece === piece.toUpperCase() ?
                                      "whitepiece" : "blackpiece");
        if (rank === 7) {
          square.classList.add("coordinate");
          square.dataset.coordinate = squareName[0];
        }
        if (piece) {
          const label = document.createElement("span");
          label.className = "piece-glyph";
          label.textContent = glyph[piece];
          label.setAttribute("aria-hidden", "true");
          square.appendChild(label);
        }
        square.setAttribute("aria-label", squareName + " " + (names[piece] || "vazio"));
        fragment.appendChild(square);
      }
    }
    container.replaceChildren(fragment);
    container.setAttribute("aria-label", "Tabuleiro após " + ply + " meios-lances");
    return true;
  }
  window.ChessSimBoard = Object.freeze({parseFen, paint});
})();
