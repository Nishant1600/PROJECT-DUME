/* Connects the HUD to the JARVIS backend over Server-Sent Events, and posts
 * typed input, confirmation answers and controls back. No framework. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const body = document.body;
  const core = window.JarvisCore;

  const STATE_TEXT = {
    idle: "standby", listening: "listening", thinking: "thinking",
    speaking: "speaking", acting: "working", boot: "booting",
  };

  // ---- clock
  setInterval(() => {
    const d = new Date();
    $("clock").textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }, 1000);

  // ---- helpers
  function setState(state, detail) {
    body.dataset.state = state;
    $("stateLabel").textContent = detail || STATE_TEXT[state] || state;
    $("coreLabel").textContent = (STATE_TEXT[state] || state).toUpperCase();
    if (core) core.setState(state);
  }

  const ticker = $("ticker");
  function addTick(text, cls) {
    const li = document.createElement("li");
    if (cls) li.className = cls;
    const t = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    li.innerHTML = `<span class="t">${t}</span>${escapeHtml(text)}`;
    ticker.prepend(li);
    while (ticker.children.length > 40) ticker.lastChild.remove();
  }

  const log = $("log");
  let lastJarvis = null;       // merge streamed jarvis chunks into one bubble
  function addMsg(role, text, final) {
    if (role === "jarvis" && lastJarvis && !lastJarvis.dataset.done) {
      lastJarvis.querySelector(".body").textContent = text;
    } else {
      const m = document.createElement("div");
      m.className = "msg " + role;
      m.innerHTML = `<span class="who">${role === "jarvis" ? "jarvis" : role}</span><span class="body"></span>`;
      m.querySelector(".body").textContent = text;
      log.appendChild(m);
      if (role === "jarvis") lastJarvis = m;
    }
    if (role === "jarvis" && final && lastJarvis) lastJarvis.dataset.done = "1";
    if (role !== "jarvis") lastJarvis = null;
    while (log.children.length > 60) log.firstChild.remove();
    log.scrollTop = log.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  let toastTimer;
  function toast(text, err) {
    const el = $("toast");
    el.textContent = text; el.className = (err ? "err " : "") + "show";
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (el.className = el.className.replace("show", "")), 3200);
  }

  // ---- POST helpers
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) }).catch(() => {});
  }

  // ---- input
  $("inputForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const inp = $("textInput"); const text = inp.value.trim();
    if (!text) return;
    inp.value = ""; post("/input", { text });
  });
  document.querySelectorAll(".ctl").forEach((b) =>
    b.addEventListener("click", () => post("/control", { action: b.dataset.action })));

  // ---- confirmation
  const layer = $("confirmLayer");
  let activeConfirm = null, confirmCountdown = null;
  function showConfirm(d) {
    activeConfirm = d.id;
    $("confirmAsk").textContent = "Shall I " + (d.spoken || "run this action") + "?";
    $("confirmDetail").textContent = d.detail || "";
    layer.hidden = false;
    const bar = $("confirmTimer"); bar.style.transition = "none"; bar.style.width = "100%";
    requestAnimationFrame(() => { bar.style.transition = "width 30s linear"; bar.style.width = "0%"; });
    clearTimeout(confirmCountdown);
    confirmCountdown = setTimeout(() => resolveConfirm(false), 30000); // default deny
  }
  function resolveConfirm(allow) {
    if (!activeConfirm) return;
    post("/confirm", { id: activeConfirm, allow });
    activeConfirm = null; layer.hidden = true; clearTimeout(confirmCountdown);
  }
  $("allowBtn").addEventListener("click", () => resolveConfirm(true));
  $("denyBtn").addEventListener("click", () => resolveConfirm(false));
  document.addEventListener("keydown", (e) => {
    if (layer.hidden) return;
    if (e.key === "Enter") resolveConfirm(true);
    if (e.key === "Escape") resolveConfirm(false);
  });

  // ---- meters
  function setSession(d) {
    if (d.turns != null) $("turns").textContent = d.turns;
    if (d.project) $("project").textContent = d.project;
    if (d.context_tokens != null && d.token_cap) {
      $("ctxVal").textContent = d.context_tokens.toLocaleString();
      const pct = Math.min(100, (d.context_tokens / d.token_cap) * 100);
      const bar = $("ctxBar"); bar.style.width = pct + "%";
      bar.classList.toggle("hot", pct > 80);
    }
  }

  // ---- SSE
  function connect() {
    const es = new EventSource("/events");
    const on = (name, fn) => es.addEventListener(name, (e) => { try { fn(JSON.parse(e.data)); } catch (_) {} });
    on("state", (d) => setState(d.state, d.detail));
    on("level", (d) => core && core.setLevel(d.rms));
    on("partial", (d) => { $("partial").textContent = d.text || ""; });
    on("transcript", (d) => {
      $("partial").textContent = "";
      addMsg(d.role, d.text, d.final);
      if (d.role === "user") addTick("“" + d.text + "”");
    });
    on("tool", (d) => {
      if (d.phase === "start") addTick("▸ " + (d.summary || d.name), "tool");
      else if (d.detail) addTick("  " + d.detail);
    });
    on("confirm", (d) => showConfirm(d));
    on("confirm_result", (d) => { if (d.id === activeConfirm) { activeConfirm = null; layer.hidden = true; } });
    on("notice", (d) => { toast(d.text, d.level === "error"); addTick(d.text, d.level === "error" ? "err" : d.level === "warn" ? "warn" : ""); });
    on("session", (d) => setSession(d));
    on("log", (d) => addTick(d.line));
    es.onerror = () => { setState("boot", "reconnecting"); };
    es.onopen = () => addTick("link established", "tool");
  }
  connect();
  addTick("HUD online", "tool");
})();
