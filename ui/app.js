// RAHBAR chat UI. Plain JavaScript, no framework and no build step.
//
// It talks to ONE endpoint: POST /api/ask on the same host. The Ingress sends /api to the
// api Service and everything else to this nginx container.
//   request:  {"question": "..."}                       (3 to 500 characters)
//   response: AskResponse (api/app/schemas.py): answer, citations[], answered_from_context,
//             confidence, retrieved[], provider, request_id
//   header:   X-Cache: hit | miss | error | off          (the Redis semantic cache)
//
// SAFETY: answers and document text are untrusted. Every piece of text is put on the page
// with textContent and createElement, never innerHTML, so HTML or script inside an answer
// is shown as plain text and never runs. The Content-Security-Policy (index.html and
// nginx.conf) also blocks any inline script.
"use strict";

const API_URL = "/api/ask";
const TIMEOUT_MS = 170_000;        // the Ingress allows 180 s; give up a little earlier
const SLOW_NOTE_MS = 12_000;       // after this, explain why it can be slow
const MAX_LEN = 500;
const MIN_LEN = 3;
const SVG_NS = "http://www.w3.org/2000/svg";

const thread = document.getElementById("thread");
const welcome = document.getElementById("welcome");
const form = document.getElementById("ask");
const input = document.getElementById("question");
const send = document.getElementById("send");
const counter = document.getElementById("counter");
const mascotTemplate = document.getElementById("mascot-template");

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const finePointer = window.matchMedia("(pointer: fine)");
let busy = false;                  // one question at a time

// ---------- small helpers ----------

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function icon(pathData, viewBox = "0 0 24 24") {
  const node = svg("svg", { viewBox, "aria-hidden": "true", focusable: "false" });
  node.append(svg("path", { d: pathData }));
  return node;
}

// Rahbar, cloned from the <template>. Moods: idle, thinking, happy, unsure, error.
function mascot(mood) {
  const node = mascotTemplate.content.firstElementChild.cloneNode(true);
  setMood(node, mood);
  return node;
}

function setMood(node, mood) {
  node.classList.remove("is-idle", "is-thinking", "is-happy", "is-unsure", "is-error");
  node.classList.add(`is-${mood}`);
}

function scrollToEnd(node) {
  node.scrollIntoView({ behavior: reducedMotion.matches ? "auto" : "smooth", block: "end" });
}

function seconds(ms) {
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

function pageLabel(page) {
  return page === null || page === undefined ? "no page" : `p. ${page}`;
}

// One "turn": Rahbar on the left, a card on the right.
function turn(mood) {
  const wrap = el("div", { class: "turn" });
  const side = el("div", { class: "turn-mascot" });
  const face = mascot(mood);
  side.append(face);
  const card = el("article", { class: "card" });
  wrap.append(side, card);
  return { wrap, card, face };
}

// ---------- badges ----------

const CONFIDENCE_FLAMES = { high: 3, medium: 2, low: 1 };
const FLAME_PATH = "M7 1C8 5 12 6 12 10a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 1-8z";

function confidenceBadge(level) {
  const lit = CONFIDENCE_FLAMES[level];
  if (!lit) return null;
  const badge = el("span", { class: "badge", title: "How sure the model says it is" });
  const flames = el("span", { class: "flames", "aria-hidden": "true" });
  for (let i = 0; i < 3; i += 1) {
    const flame = svg("svg", { class: i < lit ? "flame lit" : "flame", viewBox: "0 0 14 16" });
    flame.append(svg("path", { d: FLAME_PATH }));
    flames.append(flame);
  }
  badge.append(flames, el("span", {}, `Confidence: ${level}`));
  return badge;
}

// X-Cache comes from the API (routes/ask.py). Each value is shown exactly as it is.
const CACHE_BADGES = {
  hit: { cls: "cache-hit", text: "Remembered · cache hit",
         title: "X-Cache: hit. A very similar question was answered recently; that stored answer was reused.",
         icon: "M13 2 4 14h6l-1 8 9-12h-6z" },
  miss: { cls: "cache-miss", text: "Fresh look-up · cache miss",
          title: "X-Cache: miss. The documents were searched and the answer was written just now.",
          icon: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm9 16-4-4" },
  error: { cls: "cache-error", text: "Cache unavailable",
           title: "X-Cache: error. The cache could not be reached, so this answer was not cached.",
           icon: "M12 3 2 21h20zm0 6v5m0 3v.5" },
  off: { cls: "cache-miss", text: "Cache off",
         title: "X-Cache: off. The cache is not configured for this API.",
         icon: "M4 4l16 16M12 3a9 9 0 1 0 9 9" },
};

function cacheBadge(header) {
  const info = header ? CACHE_BADGES[header.trim().toLowerCase()] : null;
  if (!info) return null;
  const badge = el("span", { class: `badge ${info.cls}`, title: info.title });
  badge.append(icon(info.icon), el("span", {}, info.text));
  return badge;
}

// ---------- rendering an answer ----------

function isAskResponse(data) {
  return data && typeof data.answer === "string" && Array.isArray(data.citations)
    && typeof data.answered_from_context === "boolean" && Array.isArray(data.retrieved);
}

function citationKey(item) {
  return `${item.source}\u0000${item.section}\u0000${item.page ?? ""}`;
}

function renderAnswer(card, face, data, cacheHeader, elapsedMs) {
  const grounded = data.answered_from_context;
  setMood(face, grounded ? "happy" : "unsure");
  card.classList.toggle("is-unsure", !grounded);

  const top = el("div", { class: "card-top" });
  top.append(grounded
    ? el("span", { class: "stamp ok" }, "From the documents")
    : el("span", { class: "stamp no" }, "Not in the documents"));
  const conf = confidenceBadge(data.confidence);
  if (conf) top.append(conf);
  const cache = cacheBadge(cacheHeader);
  if (cache) top.append(cache);
  card.append(top);

  card.append(el("p", { class: "answer-text" }, data.answer));
  if (!grounded) {
    card.append(el("p", { class: "unsure-help" },
      "The documents don't seem to cover this, so RAHBAR won't guess. "
      + "Try rewording the question, or ask the office responsible for the rule."));
  }

  // Sources: exactly the citations the API returned, nothing added.
  if (data.citations.length) {
    card.append(el("h3", { class: "section-title" }, `Sources (${data.citations.length})`));
    const list = el("ol", { class: "tickets" });
    data.citations.forEach((c, i) => {
      const item = el("li", { class: "ticket" });
      item.style.setProperty("--i", String(i));
      const body = el("div", { class: "ticket-body" });
      body.append(el("span", { class: "ticket-doc" }, c.source),
                  el("span", { class: "ticket-sec" }, c.section));
      item.append(el("span", { class: "ticket-no", "aria-hidden": "true" }, i + 1), body,
                  el("span", { class: "ticket-page" }, pageLabel(c.page)));
      list.append(item);
    });
    card.append(list);
  }

  // Evidence: the passages the retriever looked at, with their similarity scores.
  if (data.retrieved.length) {
    const cited = new Set(data.citations.map(citationKey));
    const details = el("details", { class: "evidence" });
    details.append(el("summary", {}, `Evidence trail: ${data.retrieved.length} passages checked`));
    const list = el("ol", { class: "passages" });
    for (const r of data.retrieved) {
      const row = el("li", { class: "passage" });
      const ref = el("div", { class: "passage-ref" });
      ref.append(el("b", {}, r.source), document.createTextNode(` · ${r.section} · ${pageLabel(r.page)}`));
      if (cited.has(citationKey(r))) ref.append(el("span", { class: "passage-cited" }, "cited"));
      const score = typeof r.score === "number" ? Math.max(0, Math.min(1, r.score)) : null;
      const meter = el("div", { class: "score", title: "Similarity to your question (1 = identical)" });
      const bar = el("span", { class: "score-bar", "aria-hidden": "true" });
      const fill = el("span", { class: "score-fill" });
      fill.style.setProperty("--w", score === null ? "0%" : `${(score * 100).toFixed(0)}%`);
      bar.append(fill);
      meter.append(bar, el("span", {}, score === null ? "–" : score.toFixed(2)));
      row.append(ref, meter);
      list.append(row);
    }
    details.append(list, el("p", { class: "evidence-note" },
      "Scores are the similarity between your question and each passage. "
      + "Passages below the relevance floor are not used for the answer."));
    card.append(details);
  }

  const foot = el("div", { class: "card-foot" });
  foot.append(el("span", {}, `answered in ${seconds(elapsedMs)}`));
  if (data.provider) foot.append(el("span", {}, `model: ${data.provider}`));
  if (data.request_id) {
    const id = el("span", {}, "request ");
    id.append(el("code", {}, data.request_id));
    foot.append(id);
  }
  card.append(foot);
}

// ---------- errors ----------

function errorMessage(status, body) {
  const detail = body && typeof body.detail === "string" ? body.detail : "";
  if (status === 422) return `Questions must be ${MIN_LEN} to ${MAX_LEN} characters.`;
  if (status === 429) return "Too many questions in a short time. Wait a moment and try again.";
  if (status === 503) return `The service is not ready yet${detail ? `: ${detail}` : "."}`;
  if (status === 504) return "The answer took too long and the gateway gave up. Please try again.";
  if (status === 502) return "The API is not reachable right now (HTTP 502). Please try again shortly.";
  if (status >= 500) return `The service failed (HTTP ${status})${detail ? `: ${detail}` : "."}`;
  return `The request failed (HTTP ${status})${detail ? `: ${detail}` : "."}`;
}

function renderError(card, face, title, message, question) {
  setMood(face, "error");
  card.classList.add("is-error");
  const top = el("div", { class: "card-top" });
  top.append(el("span", { class: "stamp err" }, title));
  card.append(top, el("p", { class: "answer-text" }, message));
  if (question) {
    const retry = el("button", { type: "button", class: "btn primary" }, "Ask again");
    retry.addEventListener("click", () => { retry.disabled = true; ask(question); });
    card.append(retry);
  }
}

// ---------- asking ----------

async function ask(question) {
  if (busy) return;
  busy = true;
  send.disabled = true;
  welcome.classList.add("is-hidden");

  const bubble = el("div", { class: "q" });
  bubble.append(el("span", { class: "q-label" }, "You asked"), document.createTextNode(question));
  thread.append(bubble);

  // Pending card: Rahbar walks while we wait. The timer is real elapsed time.
  const { wrap, card, face } = turn("thinking");
  wrap.classList.add("pending");
  const status = el("div", { role: "status" });
  const line = el("p", { class: "pending-line" }, "Searching the documents and writing an answer…");
  const sub = el("p", { class: "pending-sub" }, "0 s");
  const steps = el("div", { class: "footsteps", "aria-hidden": "true" });
  for (let i = 0; i < 5; i += 1) {
    const s = el("span");
    s.style.setProperty("--i", String(i));
    steps.append(s);
  }
  const stop = el("button", { type: "button", class: "btn" }, "Stop waiting");
  status.append(line, steps, sub);
  card.append(status, stop);
  thread.append(wrap);
  scrollToEnd(wrap);

  const controller = new AbortController();
  let stoppedByUser = false;
  stop.addEventListener("click", () => { stoppedByUser = true; controller.abort(); });
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
  const started = performance.now();
  const tick = setInterval(() => {
    const ms = performance.now() - started;
    sub.textContent = ms > SLOW_NOTE_MS
      ? `${seconds(ms)} · the answer model runs on a laptop CPU, so this can take a minute or two`
      : seconds(ms);
  }, 500);

  try {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
    let body = null;
    try { body = await res.json(); } catch { /* an error page that is not JSON */ }
    wrap.classList.remove("pending");
    card.replaceChildren();
    if (!res.ok) {
      renderError(card, face, "Something went wrong", errorMessage(res.status, body),
                  res.status === 422 ? null : question);
    } else if (!isAskResponse(body)) {
      renderError(card, face, "Unexpected reply",
                  "The API answered, but not in the expected format, so nothing is shown.", question);
    } else {
      renderAnswer(card, face, body, res.headers.get("X-Cache"), performance.now() - started);
    }
  } catch (err) {
    wrap.classList.remove("pending");
    card.replaceChildren();
    if (err && err.name === "AbortError") {
      renderError(card, face, stoppedByUser ? "Stopped" : "Too slow",
        stoppedByUser ? "You stopped waiting. The question can be asked again."
                      : `No answer after ${TIMEOUT_MS / 1000} seconds, so RAHBAR stopped waiting.`,
        question);
    } else {
      renderError(card, face, "Can't reach RAHBAR",
        `The API at ${location.origin}${API_URL} could not be reached. Check the connection and try again.`,
        question);
    }
  } finally {
    clearTimeout(timeout);
    clearInterval(tick);
    busy = false;
    updateComposer();
    if (finePointer.matches) input.focus();   // on phones, don't pop the keyboard back up
    scrollToEnd(wrap);
  }
}

// ---------- the composer ----------

function updateComposer() {
  const length = input.value.trim().length;
  counter.textContent = `${input.value.length} / ${MAX_LEN}`;
  counter.classList.toggle("near", input.value.length > MAX_LEN - 50);
  send.disabled = busy || length < MIN_LEN;
  // grow with the text, up to the CSS max-height
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}

function submitQuestion() {
  const question = input.value.trim();
  if (busy || question.length < MIN_LEN) return;
  input.value = "";
  updateComposer();
  ask(question);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  submitQuestion();
});

input.addEventListener("input", updateComposer);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitQuestion();
  }
});

for (const button of document.querySelectorAll(".suggestion")) {
  button.addEventListener("click", () => {
    input.value = button.textContent.trim();
    submitQuestion();
  });
}

document.getElementById("brand-mascot").append(mascot("idle"));
updateComposer();
// The first measurement can run before the layout settles; measure again once loaded.
window.addEventListener("load", updateComposer);
window.addEventListener("resize", updateComposer);
