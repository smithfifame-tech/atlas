/* Interface de l'assistant — JavaScript sans dépendance (marked/DOMPurify facultatifs). */
(() => {
  "use strict";

  // ---------- jeton d'accès
  const params = new URLSearchParams(location.search);
  if (params.get("token")) {
    try { localStorage.setItem("assistant_token", params.get("token")); } catch (e) {}
    history.replaceState(null, "", "/" + location.hash);
  }
  let TOKEN = params.get("token") || "";
  try { TOKEN = TOKEN || localStorage.getItem("assistant_token") || ""; } catch (e) {}

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  const state = {
    settings: {}, models: {}, modes: {}, computer: {},
    currentId: null, awaitingNew: false, running: new Set(),
    attachments: [], ws: null, live: null, conversations: [],
  };

  const TOOL_LABELS = {
    run_command: ["⌘", "Terminal"],
    str_replace_based_edit_tool: ["✎", "Fichier"],
    find_files: ["🔎", "Recherche de fichiers"],
    search_in_files: ["🔎", "Recherche dans les fichiers"],
    read_document: ["📄", "Lecture"],
    move_path: ["↪", "Déplacement"],
    delete_path: ["🗑", "Corbeille"],
    open_path: ["↗", "Ouverture"],
    clipboard: ["📋", "Presse-papiers"],
    memory: ["◎", "Mémoire"],
    connector_tools: ["🔌", "Connecteurs"],
    connector_call: ["🔌", "Application connectée"],
    manage_connectors: ["🔌", "Gestion des connecteurs"],
  };
  const COMPUTER_LABELS = {
    screenshot: "Capture d'écran", zoom: "Zoom", left_click: "Clic", right_click: "Clic droit",
    middle_click: "Clic milieu", double_click: "Double-clic", triple_click: "Triple-clic",
    left_click_drag: "Glisser", mouse_move: "Déplacer la souris", left_mouse_down: "Bouton enfoncé",
    left_mouse_up: "Bouton relâché", cursor_position: "Position du curseur", scroll: "Défilement",
    type: "Saisie", key: "Touche", hold_key: "Touche maintenue", wait: "Attente",
  };
  const SUGGESTIONS = [
    "Range mon dossier Téléchargements par type de fichier",
    "Trouve tous mes PDF modifiés cette semaine et résume-les",
    "Ouvre le Bloc-notes et écris-moi une liste de courses",
    "Cherche les dernières actualités tech au Bénin et fais-moi un résumé",
    "Vérifie l'espace disque et dis-moi ce qui prend le plus de place",
    "Crée un projet Next.js dans mon dossier Projets et lance-le",
  ];

  // ---------- API
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-Token": TOKEN, ...(opts.headers || {}) },
    });
    if (res.status === 401) {
      showFatal("Accès refusé : rouvre l'adresse complète affichée dans la fenêtre de démarrage (avec ?token=…).");
      throw new Error("401");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    return data;
  }

  function showFatal(msg) {
    $("messages").innerHTML = "";
    $("welcome").classList.add("hidden");
    $("messages").append(el("div", "notice error", msg));
  }

  // ---------- markdown
  function renderMarkdown(target, text) {
    if (window.marked && window.DOMPurify) {
      target.innerHTML = DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }));
      target.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
    } else {
      target.textContent = text;
      target.style.whiteSpace = "pre-wrap";
    }
  }

  // ---------- défilement
  const chat = $("chat");
  let stickToBottom = true;
  chat.addEventListener("scroll", () => {
    stickToBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 120;
  });
  function scrollDown(force) {
    if (force || stickToBottom) requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
  }

  // ---------- rendu des éléments
  const box = $("messages");

  function addUser(text, attachments) {
    $("welcome").classList.add("hidden");
    const n = el("div", "msg-user", text);
    if (attachments) n.append(el("span", "att", `📎 ${attachments} pièce(s) jointe(s)`));
    box.append(n);
    scrollDown(true);
  }

  function addText(text, citations) {
    const n = el("div", "msg-text");
    renderMarkdown(n, text);
    if (citations && citations.length) {
      const c = el("div", "cites");
      const seen = new Set();
      citations.forEach((ci) => {
        if (seen.has(ci.url)) return;
        seen.add(ci.url);
        const a = el("a", "", ci.title || ci.url);
        a.href = ci.url; a.target = "_blank"; a.rel = "noopener"; a.title = ci.url;
        c.append(a);
      });
      n.append(c);
    }
    box.append(n);
    return n;
  }

  function addThinking(text, open) {
    const d = el("details", "thinking");
    if (open) d.open = true;
    d.append(el("summary", "", "Réflexion"));
    const b = el("div", "body-t", text || "");
    d.append(b);
    box.append(d);
    return { node: d, body: b };
  }

  function toolLabel(name, toolset) {
    if (toolset === "computer") return ["🖱", COMPUTER_LABELS[name] || name];
    return TOOL_LABELS[name] || ["•", name];
  }

  function addTool({ id, name, toolset, summary, ok, preview, image, running, input }) {
    const [ic, label] = toolLabel(name, toolset);
    const n = el("div", "tool" + (toolset === "computer" ? " compact" : ""));
    if (id) n.dataset.id = id;
    const head = el("div", "tool-head");
    head.append(el("span", "ic", ic), el("span", "nm", label), el("span", "sm", summary || ""), el("span", "st"));
    const body = el("div", "tool-body");
    if (input && name === "str_replace_based_edit_tool" && (input.file_text || input.new_str)) {
      body.append(el("pre", "", input.file_text || `− ${input.old_str || ""}\n+ ${input.new_str}`));
    }
    n.append(head, body);
    head.addEventListener("click", () => n.classList.toggle("open"));
    box.append(n);
    if (running) n.classList.add("run");
    else finishTool(n, ok, preview, image);
    return n;
  }

  function finishTool(n, ok, preview, image, duration) {
    n.classList.remove("run");
    n.classList.add(ok ? "ok" : "err");
    n.querySelector(".st").textContent = (ok ? "✓" : "✗") + (duration ? ` ${duration}s` : "");
    const body = n.querySelector(".tool-body");
    if (preview) body.append(el("pre", "", preview));
    if (image) {
      const img = new Image();
      img.src = "data:image/jpeg;base64," + image;
      body.append(img);
      showScreen(image);
    }
    if (!ok) n.classList.add("open");
  }

  function addWeb(name, input) {
    const n = el("div", "web-chip");
    n.append(el("b", "", name === "web_fetch" ? "🌐 Lecture web" : "🌐 Recherche web"), el("span", "", input || ""));
    box.append(n);
    return n;
  }

  function addNotice(text, level) {
    const n = el("div", "notice " + (level || "info"), text);
    box.append(n);
    scrollDown();
    return n;
  }

  function addApproval(req) {
    if (document.querySelector(`.approval[data-rid="${req.request_id}"]`)) return;
    const n = el("div", "approval" + (req.level === "critique" ? " critique" : ""));
    n.dataset.rid = req.request_id;
    const [, label] = toolLabel(req.name, req.toolset);
    n.append(el("h4", "", `Autorisation demandée · ${label}`));
    n.append(el("div", "why", req.reason || ""));
    let detail = req.summary || "";
    if (req.name === "connector_call" && req.input && req.input.arguments) {
      detail += "\n" + JSON.stringify(req.input.arguments, null, 2);
    } else if (req.name === "str_replace_based_edit_tool" && req.input) {
      detail += "\n" + (req.input.file_text || req.input.new_str || "").slice(0, 3000);
    }
    n.append(el("pre", "", detail));
    const acts = el("div", "acts");
    const comment = el("input");
    comment.placeholder = "Commentaire facultatif (ex. « fais plutôt… »)";
    const b1 = el("button", "btn-allow", "Autoriser");
    const b2 = el("button", "btn-always", "Toujours (cette conversation)");
    const b3 = el("button", "btn-deny", "Refuser");
    const send = (decision) => {
      state.ws && state.ws.send(JSON.stringify({ type: "approval", conversation_id: req.conversation_id,
        request_id: req.request_id, decision, comment: comment.value }));
      n.classList.add("done");
    };
    b1.onclick = () => send("allow");
    b2.onclick = () => send("always");
    b3.onclick = () => send("deny");
    acts.append(b1);
    if (req.can_always) acts.append(b2);
    acts.append(b3, comment);
    n.append(acts);
    box.append(n);
    scrollDown(true);
    setStatus("En attente de ton autorisation…");
  }

  function showScreen(b64) {
    $("screenImg").src = "data:image/jpeg;base64," + b64;
    $("screenEmpty").classList.add("hidden");
  }

  // ---------- état « en cours »
  function setStatus(text, spinning = true) {
    const s = $("statusLine");
    s.innerHTML = "";
    if (!text) return;
    if (spinning) s.append(el("span", "spin"));
    s.append(document.createTextNode(text));
  }

  function setRunning(on) {
    $("btnSend").classList.toggle("hidden", on);
    $("btnStop").classList.toggle("hidden", !on);
    if (!on) setStatus("");
  }

  function fmtTokens(u) {
    if (!u) return "";
    const k = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + " k" : String(n));
    const inp = (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
    return `${k(inp)} jetons lus · ${k(u.output_tokens || 0)} écrits`;
  }

  // ---------- flux en direct
  function resetLive() { state.live = { blocks: {}, tools: {}, turn: 0, raf: null }; }
  resetLive();

  function handleEvent(ev) {
    if (ev.type === "pong") return;
    if (ev.type === "conversation_started") {
      state.running.add(ev.conversation_id);
      if (state.awaitingNew && !state.currentId) {
        state.currentId = ev.conversation_id;
        state.awaitingNew = false;
      }
      refreshList();
      if (ev.conversation_id === state.currentId) { setRunning(true); setStatus("Réflexion…"); }
      return;
    }
    if (ev.type === "title") {
      refreshList();
      if (ev.conversation_id === state.currentId) $("convTitle").textContent = ev.title;
      return;
    }
    if (ev.conversation_id && ev.conversation_id !== state.currentId) {
      if (ev.type === "done") { state.running.delete(ev.conversation_id); refreshList(); }
      return;
    }
    const L = state.live;
    switch (ev.type) {
      case "status":
        L.turn += 1; L.blocks = {};
        setStatus(ev.message);
        break;
      case "block_start": {
        const key = `${L.turn}:${ev.index}`;
        if (ev.kind === "thinking") {
          if (!state.settings.show_thinking) break;
          const t = addThinking("", false);
          L.blocks[key] = { kind: "thinking", node: t.body, text: "", wrap: t.node };
          t.node.classList.add("hidden");
        } else {
          const n = el("div", "msg-text");
          box.append(n);
          L.blocks[key] = { kind: "text", node: n, text: "" };
        }
        setStatus(ev.kind === "thinking" ? "Réflexion…" : "Rédaction…");
        break;
      }
      case "thinking_delta": {
        const b = L.blocks[`${L.turn}:${ev.index}`];
        if (!b) break;
        b.text += ev.text;
        b.wrap.classList.remove("hidden");
        b.node.textContent = b.text;
        scrollDown();
        break;
      }
      case "text_delta": {
        const key = `${L.turn}:${ev.index}`;
        let b = L.blocks[key];
        if (!b) { const n = el("div", "msg-text"); box.append(n); b = L.blocks[key] = { kind: "text", node: n, text: "" }; }
        b.text += ev.text;
        if (!b.pending) {
          b.pending = true;
          requestAnimationFrame(() => { b.pending = false; renderMarkdown(b.node, b.text); scrollDown(); });
        }
        break;
      }
      case "tool_start": {
        L.tools[ev.id] = addTool({ ...ev, running: true });
        const [, label] = toolLabel(ev.name, ev.toolset);
        setStatus(`${label}…`);
        scrollDown();
        break;
      }
      case "tool_result": {
        const n = L.tools[ev.id] || box.querySelector(`.tool[data-id="${ev.id}"]`);
        if (n) finishTool(n, ev.ok, ev.preview, ev.image, ev.duration);
        scrollDown();
        break;
      }
      case "screen":
        showScreen(ev.image);
        break;
      case "server_tool":
        addWeb(ev.name, ev.input);
        setStatus(ev.name === "web_fetch" ? "Lecture de la page…" : "Recherche sur le web…");
        scrollDown();
        break;
      case "server_tool_result":
        break;
      case "approval_request":
        addApproval(ev);
        break;
      case "approval_closed": {
        const n = document.querySelector(`.approval[data-rid="${ev.request_id}"]`);
        if (n) n.classList.add("done");
        break;
      }
      case "provider":
        $("convMeta").dataset.provider = ev.name;
        setStatus(`Modèle : ${ev.name}`);
        break;
      case "usage":
        $("convMeta").textContent = fmtTokens(ev.usage);
        break;
      case "notice":
        addNotice(ev.message, ev.level);
        break;
      case "error":
        addNotice(ev.message, "error");
        break;
      case "done":
        state.running.delete(ev.conversation_id);
        setRunning(false);
        if (ev.usage) $("convMeta").textContent = fmtTokens(ev.usage);
        refreshList();
        break;
    }
  }

  // ---------- WebSocket
  let retry = 0;
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
    state.ws = ws;
    ws.onopen = () => {
      retry = 0;
      if (state.currentId) openConversation(state.currentId, true);
    };
    ws.onmessage = (m) => { try { handleEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
    ws.onclose = (e) => {
      state.ws = null;
      if (e.code === 4401) { showFatal("Connexion refusée : rouvre l'adresse affichée au démarrage."); return; }
      setStatus("Connexion perdue, reconnexion…");
      setTimeout(connect, Math.min(10000, 500 * 2 ** retry++));
    };
  }
  setInterval(() => { if (state.ws && state.ws.readyState === 1) state.ws.send('{"type":"ping"}'); }, 25000);

  // ---------- conversations
  async function refreshList() {
    try {
      const s = await api("/api/state");
      state.conversations = s.conversations;
      state.running = new Set(s.running);
      renderConnectorsMini(s.connectors);
    } catch (e) { return; }
    const list = $("convList");
    list.innerHTML = "";
    state.conversations.forEach((c) => {
      const item = el("div", "conv-item" + (c.id === state.currentId ? " active" : ""));
      if (state.running.has(c.id)) item.append(el("span", "run"));
      item.append(el("span", "t", c.title));
      const del = el("button", "del", "✕");
      del.title = "Supprimer";
      del.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`Supprimer « ${c.title} » ?`)) return;
        await api(`/api/conversations/${c.id}`, { method: "DELETE" });
        if (c.id === state.currentId) newConversation();
        refreshList();
      };
      item.append(del);
      item.onclick = () => { openConversation(c.id); $("sidebar").classList.remove("open"); };
      list.append(item);
    });
  }

  function renderConnectorsMini(conns) {
    const m = $("connMini");
    m.innerHTML = "";
    (conns || []).forEach((c) => {
      const chip = el("span", "c");
      chip.title = c.error || `${c.tools.length} outil(s)`;
      chip.append(el("span", "dot " + (c.status === "connecté" ? "ok" : c.status === "erreur" ? "bad" : "wait")), document.createTextNode(c.name));
      m.append(chip);
    });
  }

  function newConversation() {
    state.currentId = null;
    try { history.replaceState(null, "", "/"); } catch (e) {}
    state.awaitingNew = false;
    resetLive();
    box.innerHTML = "";
    $("welcome").classList.remove("hidden");
    $("convTitle").textContent = "Nouvelle conversation";
    $("convMeta").textContent = "";
    setRunning(false);
    document.querySelectorAll(".conv-item").forEach((n) => n.classList.remove("active"));
    $("input").focus();
  }

  async function openConversation(id, keepScroll) {
    let data;
    try { data = await api(`/api/conversations/${id}`); } catch (e) { return; }
    state.currentId = id;
    try { history.replaceState(null, "", "#c=" + id); } catch (e) {}
    resetLive();
    box.innerHTML = "";
    $("welcome").classList.add("hidden");
    $("convTitle").textContent = data.title;
    $("convMeta").textContent = fmtTokens(data.usage);
    for (const it of data.log) {
      if (it.kind === "user") addUser(it.text, it.attachments);
      else if (it.kind === "text") addText(it.text, it.citations);
      else if (it.kind === "thinking") { if (state.settings.show_thinking) addThinking(it.text, false); }
      else if (it.kind === "tool") addTool({ name: it.name, toolset: it.toolset, summary: it.summary, ok: it.ok, preview: it.preview, duration: it.duration });
      else if (it.kind === "server_tool") addWeb(it.name, it.input);
      else if (it.kind === "notice") addNotice(it.text, "warn");
      else if (it.kind === "error") addNotice(it.text, "error");
    }
    setRunning(data.running);
    if (data.running) setStatus("Tâche en cours…");
    (data.pending || []).forEach(addApproval);
    document.querySelectorAll(".conv-item").forEach((n) => n.classList.remove("active"));
    refreshList();
    scrollDown(!keepScroll);
  }

  // ---------- envoi
  async function send() {
    const input = $("input");
    const text = input.value.trim();
    if (!text && !state.attachments.length) return;
    if (!state.ws || state.ws.readyState !== 1) { addNotice("Pas de connexion au serveur local.", "error"); return; }
    if (state.currentId && state.running.has(state.currentId)) return;
    const atts = state.attachments.slice();
    addUser(text, atts.length);
    if (!state.currentId) state.awaitingNew = true;
    state.ws.send(JSON.stringify({ type: "send", conversation_id: state.currentId, text, attachments: atts }));
    input.value = "";
    autosize();
    state.attachments = [];
    renderAttachments();
    setRunning(true);
    setStatus("Envoi…");
    resetLive();
  }

  function stop() {
    if (state.ws && state.currentId) state.ws.send(JSON.stringify({ type: "stop", conversation_id: state.currentId }));
    setStatus("Arrêt…");
  }

  // ---------- pièces jointes
  function readFile(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve({ name: file.name, media_type: file.type || "application/octet-stream", data: String(r.result).split(",")[1] || "" });
      r.onerror = reject;
      r.readAsDataURL(file);
    });
  }
  async function addFiles(files) {
    for (const f of files) {
      if (f.size > 40 * 1024 * 1024) { addNotice(`${f.name} dépasse 40 Mo.`, "warn"); continue; }
      state.attachments.push(await readFile(f));
    }
    renderAttachments();
  }
  function renderAttachments() {
    const row = $("attachRow");
    row.innerHTML = "";
    state.attachments.forEach((a, i) => {
      const c = el("span", "chip", "📎 " + a.name);
      const x = el("button", "", "✕");
      x.onclick = () => { state.attachments.splice(i, 1); renderAttachments(); };
      c.append(x);
      row.append(c);
    });
  }

  function autosize() {
    const t = $("input");
    t.style.height = "auto";
    t.style.height = Math.min(220, t.scrollHeight) + "px";
  }

  // ---------- réglages
  function fillSelect(sel, options, value) {
    sel.innerHTML = "";
    Object.entries(options).forEach(([k, v]) => {
      const o = el("option", "", v);
      o.value = k;
      if (k === value) o.selected = true;
      sel.append(o);
    });
  }

  function applySettings(s) {
    state.settings = s;
    $("brandName").textContent = s.assistant_name || "Atlas";
    document.title = s.assistant_name || "Assistant";
    $("welcomeTitle").textContent = s.user_name ? `Bonjour ${s.user_name}, que veux-tu que je fasse ?` : "Que veux-tu que je fasse ?";
    const modeLabels = { prudent: "Prudent", equilibre: "Équilibré", autonome: "Autonome" };
    fillSelect($("modeSelect"), modeLabels, s.approval_mode);
    $("modeDot").style.background = { prudent: "var(--ok)", equilibre: "var(--warn)", autonome: "var(--bad)" }[s.approval_mode] || "var(--ok)";
  }

  async function openSettings() {
    const s = await api("/api/state");
    const st = s.settings;
    $("sKey").value = "";
    $("sKey").placeholder = st.api_key_set ? `Clé enregistrée (${st.api_key_hint}) — laisse vide pour la garder` : "sk-ant-…";
    fillSelect($("sModel"), s.models, st.model);
    fillSelect($("sMode"), s.modes, st.approval_mode);
    $("sName").value = st.assistant_name || "";
    $("sUser").value = st.user_name || "";
    $("sWorkdir").value = st.working_dir || "";
    $("sComputer").checked = !!st.computer_use;
    $("sComputerState").textContent = s.computer.available ? "" : `(indisponible : ${s.computer.reason})`;
    $("sSearch").checked = !!st.web_search;
    $("sFetch").checked = !!st.web_fetch;
    $("sThinking").checked = !!st.show_thinking;
    $("sShotW").value = st.screenshot_max_width;
    $("sSteps").value = st.max_steps;
    $("sCountry").value = st.location_country || "";
    $("sCity").value = st.location_city || "";
    $("sInstr").value = st.custom_instructions || "";
    renderProviders(st.providers || {});
    loadOllamaModels();
    const mem = await api("/api/memory");
    $("sMemory").value = mem.text;
    const conn = await api("/api/connectors");
    $("sConn").value = conn.config;
    renderConnStatus(conn.servers);
    $("connMsg").textContent = "";
    $("settingsModal").classList.remove("hidden");
  }

  const PROV_LINKS = {
    deepseek: "Clé sur platform.deepseek.com", openrouter: "Clé sur openrouter.ai/keys",
    groq: "Clé gratuite sur console.groq.com", custom: "Ex. LM Studio : http://127.0.0.1:1234/v1",
  };

  function renderProviders(provs) {
    const list = $("provList");
    list.innerHTML = "";
    Object.entries(provs).sort((a, b) => (a[1].priority || 99) - (b[1].priority || 99)).forEach(([pid, p]) => {
      const card = el("div", "prov" + (p.enabled ? "" : " off"));
      card.dataset.pid = pid;
      const head = el("div", "prov-head");
      const en = el("input"); en.type = "checkbox"; en.checked = !!p.enabled; en.dataset.k = "enabled";
      en.onchange = () => card.classList.toggle("off", !en.checked);
      const pr = el("input"); pr.type = "number"; pr.min = 1; pr.max = 99; pr.value = p.priority || 9; pr.dataset.k = "priority";
      pr.title = "Priorité (1 = en premier)";
      head.append(en, el("b", "", p.label), el("span", "muted small", "priorité"), pr);
      card.append(head);
      const body = el("div", "prov-body");
      const field = (label, key, value, placeholder, type) => {
        const l = el("label", "field"); l.append(el("span", "", label));
        const i = el("input"); i.dataset.k = key; i.value = value || ""; i.placeholder = placeholder || "";
        if (type) i.type = type;
        if (key === "model" && pid === "ollama") i.setAttribute("list", "ollamaModels");
        l.append(i); body.append(l);
      };
      if (pid === "anthropic") {
        card.append(el("div", "prov-note", "Clé API et modèle Claude : onglet Général."));
      } else {
        if (p.needs_key || pid === "custom") field("Clé API", "api_key", "", p.api_key_set ? `Enregistrée (${p.api_key_hint}) — vide = garder` : "Colle ta clé", "password");
        field("Modèle", "model", p.model, p.default_model || (pid === "ollama" ? "Automatique (1er modèle installé)" : "nom du modèle"));
        if (pid === "custom" || pid === "ollama") field("Adresse du service", "base_url", p.base_url, p.default_base_url || "https://…/v1");
        card.append(body);
        if (PROV_LINKS[pid]) card.append(el("div", "prov-note", PROV_LINKS[pid]));
        if (pid === "ollama") { const n = el("div", "prov-note", "Vérification d'Ollama…"); n.id = "ollamaNote"; card.append(n); }
      }
      list.append(card);
    });
  }

  async function loadOllamaModels() {
    try {
      const r = await api("/api/ollama/models");
      const dl = $("ollamaModels"); dl.innerHTML = "";
      r.models.forEach((m) => { const o = el("option"); o.value = m; dl.append(o); });
      const note = $("ollamaNote");
      if (note) note.textContent = r.running
        ? (r.models.length ? `Ollama est lancé · modèles installés : ${r.models.join(", ")}` : "Ollama est lancé mais aucun modèle n'est installé (ex. : ollama pull qwen2.5:7b).")
        : "Ollama n'est pas lancé sur ce PC : ouvre l'application Ollama pour utiliser ce mode gratuit.";
    } catch (e) {}
  }

  function collectProviders() {
    const out = {};
    document.querySelectorAll("#provList .prov").forEach((card) => {
      const entry = {};
      card.querySelectorAll("[data-k]").forEach((i) => {
        const k = i.dataset.k;
        if (i.type === "checkbox") entry[k] = i.checked;
        else if (k === "priority") entry[k] = parseInt(i.value, 10) || 9;
        else entry[k] = i.value.trim();
      });
      out[card.dataset.pid] = entry;
    });
    return out;
  }

  function renderConnStatus(servers) {
    const box2 = $("connStatus");
    box2.innerHTML = "";
    if (!servers.length) { box2.append(el("span", "muted", "Aucun connecteur pour l'instant.")); return; }
    servers.forEach((c) => {
      const r = el("div", "row");
      r.append(el("span", "dot " + (c.status === "connecté" ? "ok" : c.status === "erreur" ? "bad" : "wait")),
        el("b", "", c.name), el("span", "muted", `${c.status} · ${c.tools.length} outil(s)`));
      box2.append(r);
      if (c.error) box2.append(el("div", "err", c.error));
    });
  }

  async function saveSettings() {
    const btn = $("btnSave");
    btn.disabled = true;
    try {
      const body = {
        model: $("sModel").value, approval_mode: $("sMode").value,
        assistant_name: $("sName").value.trim() || "Atlas", user_name: $("sUser").value.trim(),
        working_dir: $("sWorkdir").value.trim(), computer_use: $("sComputer").checked,
        web_search: $("sSearch").checked, web_fetch: $("sFetch").checked, show_thinking: $("sThinking").checked,
        screenshot_max_width: $("sShotW").value || 1366, max_steps: $("sSteps").value || 150,
        location_country: $("sCountry").value.trim().toUpperCase(), location_city: $("sCity").value.trim(),
        custom_instructions: $("sInstr").value,
        providers: collectProviders(),
      };
      if ($("sKey").value.trim()) body.api_key = $("sKey").value.trim();
      applySettings(await api("/api/settings", { method: "POST", body: JSON.stringify(body) }));
      await api("/api/memory", { method: "POST", body: JSON.stringify({ text: $("sMemory").value }) });
      const connText = $("sConn").value;
      const current = await api("/api/connectors");
      if (connText.trim() !== current.config.trim()) {
        $("connMsg").textContent = "Connexion des applications…";
        try {
          const r = await api("/api/connectors", { method: "POST", body: JSON.stringify({ config: connText }) });
          renderConnStatus(r.servers);
          renderConnectorsMini(r.servers);
        } catch (e) {
          $("connMsg").textContent = e.message;
          document.querySelector('[data-tab="connectors"]').click();
          return;
        }
      }
      $("settingsModal").classList.add("hidden");
    } catch (e) {
      alert("Enregistrement impossible : " + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  const CONN_EXAMPLE = {
    mcpServers: {
      fichiers: { command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/MOI/Documents"] },
      github: { command: "npx", args: ["-y", "@modelcontextprotocol/server-github"], env: { GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_…" } },
      "service-distant": { url: "https://exemple.com/mcp", headers: { Authorization: "Bearer VOTRE_CLE" }, disabled: true },
    },
  };

  // ---------- événements de l'interface
  $("btnSend").onclick = send;
  $("btnStop").onclick = stop;
  $("btnNew").onclick = newConversation;
  $("btnMenu").onclick = () => $("sidebar").classList.toggle("open");
  $("btnAttach").onclick = () => $("fileInput").click();
  $("fileInput").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
  $("btnSettings").onclick = openSettings;
  $("btnSave").onclick = saveSettings;
  $("btnScreen").onclick = () => $("screenPanel").classList.toggle("hidden");
  $("btnScreenClose").onclick = () => $("screenPanel").classList.add("hidden");
  $("btnConnExample").onclick = () => { $("sConn").value = JSON.stringify(CONN_EXAMPLE, null, 2); };
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => $("settingsModal").classList.add("hidden")));
  $("tabs").addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === t));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === t.dataset.tab));
  });
  $("modeSelect").onchange = async (e) => applySettings(await api("/api/settings", { method: "POST", body: JSON.stringify({ approval_mode: e.target.value }) }));

  const input = $("input");
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  input.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  chat.addEventListener("dragover", (e) => e.preventDefault());
  chat.addEventListener("drop", (e) => { e.preventDefault(); addFiles([...e.dataTransfer.files]); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("settingsModal").classList.contains("hidden")) $("settingsModal").classList.add("hidden");
      else if (state.currentId && state.running.has(state.currentId)) stop();
    }
  });

  const sug = $("suggestions");
  SUGGESTIONS.forEach((s) => {
    const b = el("button", "", s);
    b.onclick = () => { input.value = s; autosize(); input.focus(); };
    sug.append(b);
  });

  // ---------- démarrage
  (async () => {
    if (!TOKEN) { showFatal("Ouvre l'adresse complète affichée dans la fenêtre de démarrage (elle contient ?token=…)."); return; }
    try {
      const s = await api("/api/state");
      state.models = s.models;
      state.modes = s.modes;
      state.computer = s.computer;
      applySettings(s.settings);
      refreshList();
      if (!s.settings.api_key_set) {
        addNotice("Bienvenue ! Ajoute d'abord ta clé API Anthropic dans les Réglages (⚙ en bas à gauche).", "warn");
        $("welcome").classList.remove("hidden");
      }
    } catch (e) { return; }
    const m = location.hash.match(/^#c=([a-z0-9]+)$/);
    if (m) await openConversation(m[1]);
    connect();
    input.focus();
  })();
})();
