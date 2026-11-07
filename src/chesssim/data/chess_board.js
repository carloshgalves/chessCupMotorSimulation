"use strict";
/* Shared visual renderer for watch and the History replay. No game rules or engines. */
(() => {
  const glyph = {
    K:"♚",Q:"♛",R:"♜",B:"♝",N:"♞",P:"♟",
    k:"♚",q:"♛",r:"♜",b:"♝",n:"♞",p:"♟"
  };
  const lastPosition = new WeakMap();
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
    // Fail closed: an invalid replay must never leave a previous game's board visible.
    if (!rows || !container) {
      if (container) {
        container.replaceChildren();
        container.setAttribute("aria-label", "Posição do tabuleiro inválida");
        lastPosition.delete(container);
        pieceSlots.delete(container);
      }
      return false;
    }
    const last = (Array.isArray(position.last) && position.last.length === 2 &&
                  position.last.every(s=>typeof s==="string" && /^[a-h][1-8]$/.test(s)))
      ? position.last : [];
    // Only flash when the displayed position actually changes. Repeated GET
    // polling of the same PGN may repaint the DOM, but must not reanimate.
    const changed = lastPosition.has(container) && lastPosition.get(container) !== position.fen;
    const fragment = document.createDocumentFragment();
    const images = [];
    for (let rank = 0; rank < 8; rank++) {
      for (let file = 0; file < 8; file++) {
        const squareName = String.fromCharCode(97 + file) + String(8 - rank);
        const piece = rows[rank][file];
        const square = document.createElement("div");
        square.className = "square " + ((rank + file) % 2 ? "dark" : "light");
        if (last.includes(squareName)) {
          square.classList.add("last");
          if (changed) square.classList.add("fresh-last");
        }
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
          images.push({label, image, code});
          square.appendChild(label);
        }
        square.setAttribute("aria-label", squareName + " " + (names[piece] || "vazio"));
        fragment.appendChild(square);
      }
    }
    container.replaceChildren(fragment);
    pieceSlots.set(container, images);
    refreshArtwork(container);
    lastPosition.set(container, position.fen);
    container.setAttribute("aria-label", "Tabuleiro após " + ply + " meios-lances");
    return true;
  }
  window.ChessSimBoard = Object.freeze({parseFen, paint, refreshArtwork,
                                      svgSets: SVG_SETS});
})();
