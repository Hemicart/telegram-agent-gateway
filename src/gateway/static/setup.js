"use strict";
// Setup wizard. Server is the source of truth: every action re-reads /setup/api/state.

const $ = (sel) => document.querySelector(sel);
const STEPS = ["password", "credentials", "login", "folder", "connect", "done"];
let pollTimer = null;
let folderAccepted = false;

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Requested-With": "gateway" }, credentials: "same-origin" };
  if (opts.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  } else if (opts.method === "POST") {
    init.headers["Content-Type"] = "application/json";
    init.body = "{}";
  }
  const resp = await fetch(path, init);
  let data = {};
  try { data = await resp.json(); } catch (_) { /* empty body */ }
  if (!resp.ok) {
    const err = new Error(data.message || `Request failed (${resp.status})`);
    err.status = resp.status;
    err.code = data.error;
    throw err;
  }
  return data;
}

function flash(message, kind = "danger") {
  const box = $("#flash");
  box.innerHTML = "";
  if (!message) return;
  const div = document.createElement("div");
  div.className = `callout ${kind}`;
  div.textContent = message;
  box.appendChild(div);
}

function show(step) {
  document.querySelectorAll("[data-screen]").forEach((el) => { el.hidden = el.dataset.screen !== step; });
  const idx = STEPS.indexOf(step);
  document.querySelectorAll("#rail li").forEach((li) => {
    const i = STEPS.indexOf(li.dataset.step);
    li.className = i < idx ? "done" : i === idx ? "current" : "";
  });
}

function setConn(text, kind) {
  const pill = $("#conn");
  pill.textContent = text;
  pill.className = `pill ${kind || ""}`.trim();
}

function pickStep(s) {
  if (s.locked) return "done";
  if (!s.credentials) return "credentials";
  if (!s.authorized) return "login";
  if (!s.folder || !s.folder.ok || !folderAccepted) return "folder";
  return "connect";
}

async function render() {
  const status = await api("/setup/api/status");
  if (!status.authed) {
    setConn(status.claimed ? "Owner only" : "Setup", "");
    $("#claim-new").hidden = status.claimed;
    $("#claim-taken").hidden = !status.claimed;
    $("#claim-pw-field").hidden = !status.password_required;
    show("password");
    return;
  }
  const s = await api("/setup/api/state");
  setConn(s.authorized ? `Logged in${s.me ? " · " + s.me.name : ""}` : s.telegram.replace("_", " "),
          s.authorized ? "ok" : "warn");
  const step = pickStep(s);
  show(step);
  if (step === "credentials" && s.credentials_from_env) flash("API keys come from the environment.", "ok");
  if (step === "login") startLogin(s);
  if (step === "folder" || step === "done") renderFolder(s.folder, step === "done" ? "#done-folder" : null);
  if (step === "connect") renderConnect(s);
}

// --- login -------------------------------------------------------------------

function loginView(view) {
  $("#qr-block").hidden = view !== "qr";
  $("#f-phone").hidden = view !== "phone";
  $("#f-code").hidden = view !== "code";
  $("#f-2fa").hidden = view !== "2fa";
}

function drawQr(data) {
  if (data.svg) $("#qr").innerHTML = data.svg;  // server-generated SVG of our own login URL
}

function showTwoFa(hint) {
  $("#twofa-hint").textContent = hint ? `Your hint: “${hint}”` : "";
  loginView("2fa");
}

async function startLogin(s) {
  if (s.qr && s.qr.status === "password_needed") { showTwoFa(s.password_hint); return; }
  if (!$("#f-phone").hidden || !$("#f-code").hidden || !$("#f-2fa").hidden) return;
  loginView("qr");
  try { drawQr(await api("/setup/api/qr/start", { method: "POST" })); } catch (e) { flash(e.message); return; }
  pollQr();
}

function pollQr() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    let data;
    try { data = await api("/setup/api/qr/status"); } catch (e) { flash(e.message); return; }
    if (data.status === "pending") { drawQr(data); pollQr(); return; }
    if (data.status === "password_needed") { render(); return; }
    if (data.status === "done") { flash(""); render(); return; }
    $("#qr").innerHTML = '<span class="muted">Code expired</span>';
    $("#qr-retry").hidden = false;
    if (data.status === "error") flash(`QR login failed (${data.error}). Try again or use your phone number.`);
  }, 1500);
}

// --- folder ------------------------------------------------------------------

const FOLDER_MESSAGES = {
  folder_not_found: "No folder with this name yet. Create it in Telegram, then press Check folder.",
  folder_ambiguous: "Two folders have this name. Rename one, then press Check folder.",
  folder_insecure: "The folder includes whole chat types (Contacts, Groups, Channels or Bots). Edit the folder, untick those, and add chats one by one.",
  folder_needs_confirmation: "The pinned folder was renamed or replaced. The agent is paused until you confirm.",
};

function renderFolder(f, target) {
  const statusBox = target ? $(target) : $("#folder-status");
  const list = target ? null : $("#chats");
  statusBox.innerHTML = "";
  if (list) list.innerHTML = "";
  if (!f) return;
  $("#want-title").textContent = f.wanted_title;
  const box = document.createElement("div");
  if (f.ok) {
    box.className = "callout ok";
    box.textContent = `Folder “${f.pinned ? f.pinned.title : f.wanted_title}” · ${f.chats.length} chat${f.chats.length === 1 ? "" : "s"} visible to the agent.`;
  } else {
    box.className = f.error === "folder_insecure" ? "callout danger" : "callout warn";
    let text = FOLDER_MESSAGES[f.error] || f.message || "Folder not checked yet. Press Check folder.";
    if (f.found_title) text += ` Folder is now titled “${f.found_title}”.`;
    box.textContent = text;
  }
  statusBox.appendChild(box);
  if (!target) {
    $("#folder-confirm").hidden = f.error !== "folder_needs_confirmation";
    $("#folder-next").disabled = !f.ok || f.chats.length === 0;
    for (const c of f.chats) {
      const li = document.createElement("li");
      const t = document.createElement("span"); t.className = "title"; t.textContent = c.title;
      const k = document.createElement("span"); k.className = "kind"; k.textContent = c.kind;
      li.append(t, k);
      list.appendChild(li);
    }
  }
}

// --- connect agent -------------------------------------------------------------

function tokenBox(token) {
  const wrap = document.createElement("div");
  wrap.className = "secret";
  const code = document.createElement("code"); code.textContent = token;
  const btn = document.createElement("button");
  btn.className = "btn"; btn.type = "button"; btn.textContent = "Copy";
  btn.onclick = async () => { await navigator.clipboard.writeText(token); btn.textContent = "Copied"; };
  wrap.append(code, btn);
  return wrap;
}

function agentPrompt(token) {
  const base = `${location.origin}/v1`;
  return [
    "You can read some of my Telegram chats through a read-only HTTP API.",
    `Base URL: ${base}`,
    `Send this header on every request: Authorization: Bearer ${token}`,
    "",
    "GET /chats  - the chats you may read (dialog_id, title, kind)",
    "GET /chats/{dialog_id}/messages?limit=50  - newest first. Older: add before_id=<next_before_id>. Newer: after_id=<message id>",
    "GET /chats/{dialog_id}  - chat info, description, member count",
    "GET /chats/{dialog_id}/pinned  - pinned messages (usually the important ones)",
    "GET /chats/{dialog_id}/messages/{message_id}  - one message (e.g. the one a reply points to)",
    "GET /chats/{dialog_id}/messages/{message_id}/reactions  - who reacted with what (smaller groups only)",
    "GET /chats/{dialog_id}/members?q=  - members with name, username and role (owner/admin/member)",
    "GET /chats/{dialog_id}/topics  - topic names in forum groups (then use topic_id on /messages)",
    "GET /search?dialog_id={dialog_id}&q={text}  - search inside one chat",
    "Messages include links (also URLs hidden behind words), reactions, edits, pinned, album_id, polls and link previews.",
    "GET /media/{dialog_id}/{message_id}?thumb=1  - image preview of a photo or file (drop thumb=1 for the original, max 20 MB)",
    "GET /transcript/{dialog_id}/{message_id}  - text of a voice message or video note (Telegram's own transcription).",
    "  status done = text is ready; pending = retry after Retry-After seconds; unavailable = download media_path and transcribe the audio yourself.",
    "  Voice messages in /messages carry transcript (if already known) and transcript_path.",
    "",
    "404 = chat not shared with you. 429 = wait Retry-After seconds. 503 = folder needs my attention, tell me.",
    "Only ever call this address over https://. Never send the token over plain http.",
    "Never print or share the token.",
  ].join("\n");
}

let issuedToken = null;

function showAgentBlock(token) {
  issuedToken = token;
  $("#agent-prompt").textContent = agentPrompt(token);
  $("#agent-block").hidden = false;
  $("#token-area").hidden = true;
  $("#finish").disabled = false;
}

function renderConnect(s) {
  if (s.token_from_env) {
    $("#token-area").hidden = true;
    $("#token-env").hidden = false;
    $("#base-url-env").textContent = `${location.origin}/v1`;
    $("#finish").disabled = false;
    return;
  }
  if (!issuedToken && s.token_issued) $("#token-issue").textContent = "Create a new token";
  $("#finish").disabled = !issuedToken && !s.token_issued;
}

async function testToken() {
  const box = $("#test-result");
  box.innerHTML = "";
  const div = document.createElement("div");
  const resp = await fetch("/v1/chats", { headers: { Authorization: `Bearer ${issuedToken}` } });
  const data = await resp.json().catch(() => ({}));
  if (resp.ok) {
    div.className = "callout ok";
    div.textContent = `Works. The agent can see ${data.chats.length} chat${data.chats.length === 1 ? "" : "s"}.`;
  } else {
    div.className = "callout danger";
    div.textContent = `Test failed (${resp.status}): ${data.message || "unknown error"}`;
  }
  box.appendChild(div);
}

// --- wiring ------------------------------------------------------------------

function onSubmit(id, handler) {
  $(id).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = ev.target.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    try { flash(""); await handler(); } catch (e) { flash(e.message); } finally { if (btn) btn.disabled = false; }
  });
}

function onClick(id, handler) {
  $(id).addEventListener("click", async () => {
    try { flash(""); await handler(); } catch (e) { flash(e.message); }
  });
}

onSubmit("#f-claim", async () => {
  try {
    await api("/setup/api/claim", { body: { password: $("#claim-pw").value } });
  } catch (e) {
    if (e.code !== "already_claimed") throw e;
    $("#claim-pw").value = "";
    await render();
    flash("Someone else pressed Start before you. If that wasn't you, don't continue: delete this service on your host and deploy a fresh one.");
    return;
  }
  $("#claim-pw").value = "";
  await render();
});
onSubmit("#f-apilogin", async () => {
  await api("/setup/api/login", { body: { api_id: $("#login-api-id").value, api_hash: $("#login-api-hash").value } });
  $("#login-api-hash").value = "";
  await render();
});
onSubmit("#f-password", async () => { await api("/setup/api/login", { body: { password: $("#pw").value } }); $("#pw").value = ""; await render(); });
onSubmit("#f-credentials", async () => {
  await api("/setup/api/credentials", { body: { api_id: $("#api_id").value, api_hash: $("#api_hash").value } });
  $("#api_hash").value = "";
  await render();
});
onSubmit("#f-phone", async () => { clearTimeout(pollTimer); await api("/setup/api/phone/send", { body: { phone: $("#phone").value } }); loginView("code"); });
onSubmit("#f-code", async () => {
  const r = await api("/setup/api/phone/code", { body: { code: $("#code").value } });
  if (r.status === "password_needed") showTwoFa(r.password_hint); else await render();
});
onSubmit("#f-2fa", async () => { await api("/setup/api/password", { body: { password: $("#twofa").value } }); $("#twofa").value = ""; loginView("qr"); await render(); });
onClick("#to-phone", async () => { clearTimeout(pollTimer); loginView("phone"); });
onClick("#to-qr", async () => { loginView("qr"); await startLogin({}); });
onClick("#qr-retry", async () => { $("#qr-retry").hidden = true; await startLogin({}); });
onClick("#folder-refresh", async () => renderFolder(await api("/setup/api/folder/refresh", { method: "POST" })));
onClick("#folder-confirm", async () => renderFolder(await api("/admin/confirm-folder", { method: "POST" })));
onClick("#folder-next", async () => { folderAccepted = true; await render(); });
onClick("#token-issue", async () => {
  const { token } = await api("/setup/api/token", { method: "POST" });
  showAgentBlock(token);
});
onClick("#copy-prompt", async () => {
  await navigator.clipboard.writeText($("#agent-prompt").textContent);
  $("#copy-prompt").textContent = "Copied";
});
onClick("#test-token", testToken);
onClick("#finish", async () => { await api("/setup/api/complete", { method: "POST" }); await render(); });
onClick("#rotate", async () => {
  if (!confirm("Rotate the agent token? The current one stops working immediately.")) return;
  const { token } = await api("/admin/rotate-token", { method: "POST" });
  const area = $("#rotate-area"); area.innerHTML = "";
  const pre = document.createElement("pre"); pre.textContent = agentPrompt(token);
  area.append(tokenBox(token), pre);
});
onClick("#sign-out-others", async () => {
  const { signed_out: n } = await api("/setup/api/sign-out-others", { method: "POST" });
  flash(n ? `Signed out ${n} other browser${n === 1 ? "" : "s"}.` : "No other browsers were signed in.", "ok");
});
onClick("#unlock", async () => { await api("/setup/unlock", { method: "POST" }); folderAccepted = false; await render(); });

render().catch((e) => flash(e.message));
