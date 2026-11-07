"use strict";
/* Shared visual renderer for watch and the History replay. No game rules or engines. */
(() => {
  const glyph = {
    K:"♚",Q:"♛",R:"♜",B:"♝",N:"♞",P:"♟",
    k:"♚",q:"♛",r:"♜",b:"♝",n:"♞",p:"♟"
  };
  // Preserve the 64 squares and loaded SVG artwork across PGN polls.
  const boardCache = new WeakMap();
  const pieceSlots = new WeakMap();
  const SVG_SETS = Object.freeze([
    "cburnett","merida","chessnut","fantasy","spatial","celtic","rhosgfx"
  ]);
  function refreshArtwork(container) {
    if (!container) return;
    const selected = container.getAttribute?.("data-piece-style") ??
                     container.attributes?.["data-piece-style"] ?? "";
    const hasSVG = SVG_SETS.includes(selected);
    container.setAttribute("data-piece-kind", hasSVG ? "svg" : "glyph");
    for (const {label, image, code} of pieceSlots.get(container) || []) {
      if (!hasSVG) continue; // loaded art remains cached, hidden by board CSS
      const path = "/piece-assets/" + selected + "/" + code + ".svg";
      if (image.getAttribute?.("src") === path ||
          image.attributes?.src === path) continue;
      label.setAttribute("data-art-ready", "0");
      image.setAttribute("src", path);
    }
  }
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
    // Malformed replay data must not retain a stale board from another game.
    if (!rows || !container) {
      if (container) {
        container.replaceChildren();
        container.setAttribute("aria-label", "Posição do tabuleiro inválida");
        boardCache.delete(container);
        pieceSlots.delete(container);
      }
      return false;
    }
    const last = (Array.isArray(position.last) && position.last.length === 2 &&
                  position.last.every(x => typeof x === "string" && /^[a-h][1-8]$/.test(x)))
      ? position.last : [];
    const lastKey = last.join("|");
    // The History tab may clear the board itself when changing games.
    // A detached cache must be discarded before attempting a new paint.
    let old = boardCache.get(container);
    if (old && container.children.length === 0) {
      old = null;
      boardCache.delete(container);
      pieceSlots.delete(container);
    }
    if (old && old.fen === position.fen && old.ply === ply && old.last === lastKey) {
      // Polls of an unchanged position preserve SVG nodes while retiring the
      // one-frame motion class (the existing reduced-motion contract).
      if (old.fresh) {
        for (const cell of old.cells) {
          cell.className = cell.className.replace(" fresh-last", "");
        }
        old.fresh = false;
      }
      return true;
    }
    const changed = !!old && old.fen !== position.fen;
    let cells = old && old.cells;
    if (!cells) {
      cells = [];
      const fragment = document.createDocumentFragment();
      for (let rank = 0; rank < 8; rank++) {
        for (let file = 0; file < 8; file++) {
          const square = document.createElement("div");
          if (rank === 7) {
            square.dataset.coordinate = String.fromCharCode(97 + file);
          }
          cells.push(square);
          fragment.appendChild(square);
        }
      }
      container.replaceChildren(fragment);
    }
    const codes = [];
    const slots = [];
    for (let rank = 0; rank < 8; rank++) {
      for (let file = 0; file < 8; file++) {
        const i = rank * 8 + file;
        const square = cells[i];
        const squareName = String.fromCharCode(97 + file) + String(8 - rank);
        const piece = rows[rank][file];
        const prior = old && old.codes[i];
        let slot = old && old.slots[i];
        // Only affected squares receive a new glyph/SVG node after a move.
        if (!old || prior !== piece) {
          square.replaceChildren();
          slot = null;
          if (piece) {
            const label = document.createElement("span");
            label.className = "piece-glyph";
            label.textContent = glyph[piece];
            label.setAttribute("aria-hidden", "true");
            label.setAttribute("data-art-ready", "0");
            const image = document.createElement("img");
            image.className = "piece-svg";
            image.setAttribute("alt", "");
            image.setAttribute("aria-hidden", "true");
            image.setAttribute("decoding", "async");
            image.setAttribute("draggable", "false");
            const code = (piece === piece.toUpperCase() ? "w" : "b") +
                         piece.toUpperCase();
            image.addEventListener("load", () =>
              label.setAttribute("data-art-ready", "1"));
            image.addEventListener("error", () =>
              label.setAttribute("data-art-ready", "0"));
            label.appendChild(image);
            square.appendChild(label);
            slot = {label, image, code};
          }
        }
        const pieceKind = !piece ? "" :
          piece === piece.toUpperCase() ? " whitepiece" : " blackpiece";
        const marked = last.includes(squareName);
        square.className = "square " + ((rank + file) % 2 ? "dark" : "light") +
          (rank === 7 ? " coordinate" : "") + pieceKind +
          (marked ? " last" : "") + (marked && changed ? " fresh-last" : "");
        square.setAttribute("aria-label", squareName + " " + (names[piece] || "vazio"));
        codes.push(piece);
        slots.push(slot);
      }
    }
    boardCache.set(container, {cells,codes,slots,fen:position.fen,ply,last:lastKey,
                              fresh:changed});
    pieceSlots.set(container, slots.filter(Boolean));
    refreshArtwork(container);
    container.setAttribute("aria-label", "Tabuleiro após " + ply + " meios-lances");
    return true;
  }
  window.ChessSimBoard = Object.freeze({parseFen, paint, refreshArtwork,
                                      svgSets: SVG_SETS});
})();
