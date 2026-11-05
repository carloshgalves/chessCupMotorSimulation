"use strict";
/* An explicit local token protects motor launches. All dynamic content is text. */
const $ = id => document.getElementById(id);
const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
let roster = [];
let busy = false;
let currentSession = null;
let initialized = false;
let loading = false;
let refreshVersion = 0;

function setFeedback(message, bad=false) {
  const el = $("launch-feedback");
  el.textContent = message;
  el.className = "feedback " + (bad ? "error" : "good");
}
function player(slug) { return roster.find(p => p.slug === slug); }
function fillPlayer(select, preferred) {
  const prior = select.value || preferred;
  select.replaceChildren();
  for (const p of roster) {
    const option = document.createElement("option");
    option.value = p.slug;
    option.disabled = !p.installed;
    option.textContent = p.name + (p.installed ? "" : " · perfil indisponível");
    select.appendChild(option);
  }
  select.value = player(prior) && player(prior).installed ? prior :
    (roster.find(p => p.installed)?.slug || "");
}
function displayProfile(slot, slug) {
  const el = $(slot + "-profile");
  el.replaceChildren();
  const p = player(slug);
  if (!p) {
    el.textContent = "Perfil não selecionado.";
    return;
  }
  const title = document.createElement("strong");
  title.textContent = p.installed ? "Alias e arquivo do perfil encontrados" : "Perfil indisponível";
  const provenance = document.createElement("p");
  provenance.textContent = p.provenance + (p.alias ? " · Alias: " + p.alias : "");
  const windowNote = document.createElement("p");
  windowNote.textContent = p.reference_period ?
    "Período de referência: " + p.reference_period + " (" + p.reference_status + "). " +
    p.reference_note : "Janela de referência: não revisada.";
  const warning = document.createElement("p");
  warning.textContent = "Autenticidade histórica: NÃO VALIDADA";
  el.append(title, provenance, windowNote, warning);
}
function updateForm() {
  const mode = $("mode").value;
  $("depth-wrap").hidden = mode !== "depth";
  $("movetime-wrap").hidden = mode !== "movetime";
  $("preset-wrap").hidden = mode !== "preset";
  const official = mode === "preset" && $("preset").value === "official";
  const opening = $("opening");
  if (official) {
    opening.value = "internal";
    opening.disabled = true;
    $("reverse").checked = true;
    $("reverse").disabled = true;
    $("max-plies").value = "600";
    $("max-plies").disabled = true;
  } else {
    opening.disabled = false;
    $("reverse").disabled = mode === "preset";
    if (mode === "preset") $("reverse").checked = true;
    $("max-plies").disabled = false;
  }
  $("seed-wrap").hidden = opening.value !== "historical";
  $("opening-note").textContent =
    opening.value === "historical" ?
      "Política externa exploratória. A semente altera somente esse repertório, não a força histórica." :
      opening.value === "internal" ?
      "Livro UCI do motor. A semente externa não altera este modo." :
      "Sem livro externo ou interno. Semente desabilitada.";
  displayProfile("white", $("white").value);
  displayProfile("black", $("black").value);
  $("launch").disabled = busy || !token || !player($("white").value)?.installed ||
    !player($("black").value)?.installed || $("white").value === $("black").value;
  $("stop").disabled = !busy || !token;
}
function configFromForm() {
  const mode = $("mode").value;
  const config = {
    white: $("white").value, black: $("black").value,
    mode, reverse:$("reverse").checked, opening_mode:$("opening").value,
    max_plies:Number($("max-plies").value)
  };
  if (mode === "depth") config.depth = Number($("depth").value);
  else if (mode === "movetime") config.movetime = Number($("movetime").value);
  else config.preset = $("preset").value;
  if (config.opening_mode === "historical") config.seed = Number($("seed").value);
  return config;
}
function renderSession() {
  const state = currentSession;
  const config = state && state.config;
  $("session-title").textContent = state ?
    "Sessão " + state.id : "Nenhuma sessão iniciada";
  $("session-status").textContent = !state ? "Aguardando." :
    "Estado: " + state.status + (state.duel ? " · Resultado do motor: " +
      state.duel.status : "") + (state.error ? " · Erro de execução" : "");
  const details = $("session-details");
  details.replaceChildren();
  if (!state || !config) return;
  const pieces = [
    ["Brancas", player(config.white)?.name || config.white],
    ["Pretas", player(config.black)?.name || config.black],
    ["Busca", config.mode === "preset" ? config.preset :
      config.mode === "depth" ? "depth " + config.depth : config.movetime + " s/lance"],
    ["Cores invertidas",config.reverse ? "Sim" : "Não"],
    ["Abertura", config.opening_mode +
      (config.seed !== null ? " · seed " + config.seed : " · sem seed")],
    ["Resultado(s)", state.duel?.results?.join(" / ") || "Ainda sem resultados"],
  ];
  for (const [key, value] of pieces) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = key;
    dd.textContent = String(value);
    details.append(dt, dd);
  }
}
async function refresh() {
  if (loading) return;
  loading = true;
  const thisRun = ++refreshVersion;
  try {
    const response = await fetch("/api/arena", {cache:"no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (thisRun !== refreshVersion) return;
    roster = data.players;
    if (!initialized) {
      initialized = true;
      fillPlayer($("white"), "fischer");
      fillPlayer($("black"), "tal");
      $("opening").querySelector('option[value="historical"]').disabled =
        !data.opening_books_configured;
      if (!data.opening_books_configured && $("opening").value === "historical") {
        $("opening").value = "none";
      }
    }
    currentSession = data.session;
    busy = data.busy;
    $("connection").textContent = "● Arena local conectada";
    $("connection").className = "online";
    updateForm();
    renderSession();
  } catch (err) {
    $("connection").textContent = "● Sem conexão com a arena";
    $("connection").className = "offline";
    setFeedback("O servidor local não respondeu. Confira a aba do terminal.", true);
  } finally {
    loading = false;
  }
}
async function post(path, payload) {
  if (!token || token.length < 32) {
    throw new Error("Token ausente. Abra a URL completa informada pelo comando arena.");
  }
  const response = await fetch(path, {
    method:"POST",
    headers:{"Content-Type":"application/json", "X-Chesssim-Token":token},
    body:JSON.stringify(payload),
    cache:"no-store"
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "A operação foi recusada");
  return result;
}
$("duel-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!$("duel-form").reportValidity()) return;
  $("launch").disabled = true;
  setFeedback("Validando a configuração e iniciando o duelo…");
  try {
    const result = await post("/api/arena/launch", configFromForm());
    busy = true;
    setFeedback("Confronto solicitado: " + result.id + ". O tabuleiro será atualizado.");
    await refresh();
  } catch (error) {
    setFeedback(error.message, true);
    await refresh();
  }
});
$("stop").addEventListener("click", async () => {
  if (!window.confirm("Solicitar parada do duelo após a busca atual do motor?")) return;
  try {
    await post("/api/arena/stop", {});
    setFeedback("Parada solicitada. O motor pode concluir a busca em andamento.");
  } catch (error) {
    setFeedback(error.message, true);
  }
});
$("swap").addEventListener("click", () => {
  const a = $("white").value;
  $("white").value = $("black").value;
  $("black").value = a;
  updateForm();
});
for (const field of ["white","black","mode","preset","opening","reverse","max-plies"]) {
  $(field).addEventListener("change", updateForm);
}
$("launch").disabled = true;
if (!token) setFeedback("Inicie chesssim arena e abra a URL com token informada no terminal.", true);
refresh();
setInterval(refresh, 1500);
