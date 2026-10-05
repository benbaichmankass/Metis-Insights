/* Phone Probe 1a — document-start script, injected into EVERY frame (androidx.webkit addDocumentStartJavaScript).
 *
 * READ-ONLY. It never clicks, types, submits or reads an input's value. It reports a REDACTED SHAPE of the
 * page: tag names, roles, control types and labels, where a label is reduced to a fixed trading/login
 * vocabulary — every other word becomes "*" and every digit run becomes "#". So an account name, balance,
 * email or id cannot leave the page through this script. The report is posted to the app through the
 * WebMessageListener "PhoneProbe" (one message per frame; the app tells main frame from iframe).
 */
(function () {
  if (window.__probeInstalled) return;
  window.__probeInstalled = true;

  var VOCAB = {};
  ("buy sell market limit stop loss take profit tp sl quantity qty lots lot size price order orders send " +
   "position positions close cancel history portfolio watchlist protection login log in sign signin up out " +
   "email password code verify verification continue submit next back open terminal account balance equity " +
   "pnl margin trade trading amount side type toggle on off search symbol symbols deposit withdraw settings " +
   "menu help logout dashboard profile modify edit confirm yes no ok reset accept cookies terms agree " +
   "remember me forgot two factor authentication authenticator resend phone number enter your the a an to " +
   "of and or for with from by at is are all new add remove delete chart charts quotes quote bid ask spread " +
   "sltp points pips percent risk reward working pending filled rejected status time date id name " +
   "mode demo live real beta version mobile platform limited functionality see more less show hide " +
   "dark light language currency fee fees swap commission free used available total net gross unrealized " +
   "realized daily weekly drawdown max target challenge phase funded evaluation payout request support " +
   "long short leverage reduce only post gtc ioc fok trigger mark last index cross isolated entry exit " +
   "bracket oco tpsl notional contract contracts usd usdt lot step tick distance trailing breakeven " +
   "adjust amend modify cancelled canceled expire expiry validity day week gtd good till cancel stp " +
   "one click confirmation confirm review preview place submit slider percent pct max min half full " +
   "unrealised unrealized floating open closed pnl roe liquidation funding rate cooldown trades limit").split(/\s+/)
    .forEach(function (w) { if (w) VOCAB[w] = 1; });

  // Letters-runs in the vocabulary survive; any other word -> "*", digit runs -> "#". Cap 60 chars.
  function redact(s, cap) {
    if (s == null) return "";
    s = String(s).replace(/\s+/g, " ").trim().slice(0, cap || 60);
    var out = s.replace(/[A-Za-zÀ-ɏ]+|\d+/g, function (m) {
      if (/^\d/.test(m)) return "#";
      return VOCAB[m.toLowerCase()] ? m : "*";
    });
    return out.replace(/(\*[^\w*]*){2,}/g, "*").replace(/#{2,}/g, "#").slice(0, cap || 60);
  }
  // Only an attribute value that is a plain short lowercase-ish identifier is kept (names like "qty"), never an id-like token.
  function ident(s) {
    if (!s) return "";
    s = String(s);
    return /^[A-Za-z][A-Za-z_-]{0,23}$/.test(s) ? s : "";
  }
  window.__probeRedact = redact;

  function allElements(root, out, depth) {
    var list = root.querySelectorAll("*");
    for (var i = 0; i < list.length && out.length < 6000; i++) {
      out.push(list[i]);
      if (list[i].shadowRoot && depth < 4) allElements(list[i].shadowRoot, out, depth + 1);
    }
    return out;
  }

  function labelOf(el) {
    var tag = el.tagName.toLowerCase();
    var l = el.getAttribute("aria-label") || el.getAttribute("title") || "";
    if (!l && (tag === "input" || tag === "textarea" || tag === "select")) {
      var id = el.id;
      var lab = id ? document.querySelector('label[for="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]') : null;
      if (!lab && el.closest) lab = el.closest("label");
      if (lab) l = lab.textContent;
      if (!l) l = el.getAttribute("placeholder") || "";
      if (!l) { var p = el.previousElementSibling; if (p) l = p.textContent; }
    } else if (!l) {
      l = el.textContent;
    }
    return redact(l, 40);
  }

  function chain(el) {
    var parts = [], n = el.parentElement, k = 0;
    while (n && k < 3) {
      var r = n.getAttribute && n.getAttribute("role");
      parts.push(n.tagName.toLowerCase() + (r ? "[" + r + "]" : ""));
      n = n.parentElement; k++;
    }
    return parts.join("<");
  }

  var SELECT = "button,a,input,select,textarea,label,h1,h2,h3,h4,canvas,iframe,table," +
    "[role=button],[role=tab],[role=switch],[role=checkbox],[role=radio],[role=combobox],[role=listbox]," +
    "[role=menuitem],[role=spinbutton],[role=slider],[role=grid],[role=table],[role=dialog],[role=alert]," +
    "[role=heading],[role=columnheader],[contenteditable],[aria-pressed],[aria-checked],[aria-expanded]";

  function states(el) {
    var s = [];
    if (el.disabled || el.getAttribute("aria-disabled") === "true") s.push("disabled");
    ["aria-checked", "aria-pressed", "aria-selected", "aria-expanded"].forEach(function (a) {
      var v = el.getAttribute(a); if (v === "true" || v === "false") s.push(a.slice(5) + "=" + v);
    });
    if (el.readOnly) s.push("readonly");
    if (el.required) s.push("required");
    return s.join(",");
  }

  function capture() {
    var all = allElements(document, [], 0);
    var counts = {el: all.length, canvas: 0, iframe: 0, input: 0, select: 0, textarea: 0, button: 0, table: 0,
                  svg: 0, shadow: 0, editable: 0, dialog: 0, tab: 0};
    var iframes = [], controls = [], tables = [], headings = [];
    var markers = {email: false, nummatch: false, otp_like: false, pw: false, otc: false, cf: false, blocked: false, twofa: false, buy: false, sell: false, send: false, login_word: false};
    var CAP = 300, ptr = 0, numBtns = 0;
    for (var i = 0; i < all.length; i++) {
      var el = all[i], tag = el.tagName.toLowerCase();
      if (el.shadowRoot) counts.shadow++;
      if (tag === "canvas") counts.canvas++;
      else if (tag === "iframe") counts.iframe++;
      else if (tag === "input") counts.input++;
      else if (tag === "select") counts.select++;
      else if (tag === "textarea") counts.textarea++;
      else if (tag === "button") counts.button++;
      else if (tag === "table") counts.table++;
      else if (tag === "svg") counts.svg++;
      if (el.isContentEditable && el.getAttribute("contenteditable") != null) counts.editable++;
      var role = el.getAttribute("role");
      if (role === "dialog") counts.dialog++;
      if (role === "tab") counts.tab++;
    }
    var picked = [];
    try { picked = Array.prototype.slice.call(document.querySelectorAll(SELECT)); } catch (e) {}
    // shadow roots: query each too
    all.forEach(function (el) { if (el.shadowRoot) { try { picked = picked.concat(Array.prototype.slice.call(el.shadowRoot.querySelectorAll(SELECT))); } catch (e) {} } });
    // clickable-looking non-semantic elements (div/span with cursor:pointer): React UIs often use these as buttons
    for (var j = 0; j < all.length && ptr < 120; j++) {
      var e2 = all[j], t2 = e2.tagName.toLowerCase();
      if ((t2 === "div" || t2 === "span" || t2 === "li") && e2.children.length < 3) {
        var cs = null; try { cs = getComputedStyle(e2); } catch (e) {}
        if (cs && cs.cursor === "pointer" && (e2.textContent || "").trim().length) { picked.push(e2); ptr++; }
      }
    }
    var seen = [];
    for (var k = 0; k < picked.length && controls.length < CAP; k++) {
      var c = picked[k];
      if (seen.indexOf(c) >= 0) continue; seen.push(c);
      var tg = c.tagName.toLowerCase(), r = c.getAttribute("role") || "";
      var rect = c.getBoundingClientRect();
      var vis = rect.width > 0 && rect.height > 0;
      if (tg === "iframe") {
        var src = ""; try { src = new URL(c.src, location.href).hostname; } catch (e) {}
        iframes.push({host: src, w: Math.round(rect.width / 10) * 10, h: Math.round(rect.height / 10) * 10, vis: vis});
        continue;
      }
      if (tg === "table" || r === "grid" || r === "table") {
        var heads = Array.prototype.slice.call(c.querySelectorAll("th,[role=columnheader]")).slice(0, 20).map(function (h) { return redact(h.textContent, 24); });
        var rows = c.querySelectorAll("tr,[role=row]").length;
        tables.push({t: tg, r: r, cols: heads, rows: rows, vis: vis});
        continue;
      }
      var lab = labelOf(c);
      if (/^h[1-4]$/.test(tg) || r === "heading") { headings.push(lab); continue; }
      var typ = tg === "input" ? (c.type || "text") : "";
      if (typ === "password") markers.pw = true;
      if (typ === "password" || tg === "input" || tg === "textarea") {
        var ac = (c.getAttribute("autocomplete") || "").toLowerCase();
        if (ac === "one-time-code") markers.otc = true;
        var ml = parseInt(c.getAttribute("maxlength") || "0", 10);
        if (tg === "input" && typ !== "password" && ml >= 4 && ml <= 8 && /numeric|decimal|tel|number/.test((c.getAttribute("inputmode") || "") + typ)) markers.otp_like = true;
      }
      var low = lab.toLowerCase();
      if (typ === "email" || (tg === "input" && /email/.test(low))) markers.email = true;
      if (/^#$/.test(lab.trim()) && (tg === "button" || r === "button" || tg === "a")) numBtns++;
      if (/\bbuy\b/.test(low)) markers.buy = true;
      if (/\bsell\b/.test(low)) markers.sell = true;
      if (/\bsend\b.*\border\b|\bsend order\b/.test(low)) markers.send = true;
      if (/\b(log in|login|sign in|signin)\b/.test(low)) markers.login_word = true;
      controls.push({t: tg, r: r, ty: typ, im: tg === "input" ? ident(c.getAttribute("inputmode")) : "",
                     l: lab, nm: ident(c.getAttribute("name")), tid: ident(c.getAttribute("data-testid") || c.getAttribute("data-test") || c.getAttribute("data-qa")), st: states(c), vis: vis,
                     w: Math.round(rect.width / 10) * 10, h: Math.round(rect.height / 10) * 10, up: chain(c)});
    }
    counts.numbtn = numBtns;
    // heuristic: an email "number match" page is a few buttons whose labels are only digits and no inputs
    if (numBtns >= 2 && counts.input === 0 && counts.button <= 10) markers.nummatch = true;
    // markers: booleans only, never page text
    var low2 = "";
    try { low2 = ((document.body && document.body.innerText) || "").slice(0, 4000).toLowerCase(); } catch (e) {}
    var ttl = (document.title || "").toLowerCase();
    if (document.querySelector('#challenge-form,.cf-turnstile,iframe[src*="challenges.cloudflare.com"],#cf-chl-widget') ||
        /just a moment|verify you are human|checking your browser|performing security verification/.test(low2 + " " + ttl)) markers.cf = true;
    if (/access denied|error 1005|you have been blocked|error 1020|sorry, you have been blocked/.test(low2 + " " + ttl)) markers.blocked = true;
    if (/two-factor|2fa|authenticator app|verification code|enter the code|we sent (you )?a code|one-time/.test(low2)) markers.twofa = true;
    var pathSegs = (location.pathname || "/").split("/").filter(Boolean).slice(0, 4).map(function (s) { return redact(s, 20); });
    return {
      host: location.hostname, scheme: location.protocol, path: "/" + pathSegs.join("/"),
      ready: document.readyState, title: redact(document.title, 40),
      vp: [window.innerWidth, window.innerHeight, window.devicePixelRatio],
      counts: counts, markers: markers, iframes: iframes, tables: tables, headings: headings.slice(0, 20),
      controls: controls, capped: controls.length >= CAP
    };
  }


  // ---- direct (listener-independent) capture: main frame + same-origin iframes; cross-origin iframes by host ----
  window.__probeShape = function () {
    try { return JSON.stringify(capture()); } catch (e) { return JSON.stringify({error: String(e && e.message || e).slice(0, 80)}); }
  };
  window.__probeShapeAll = function () {
    var out = {top: null, same: [], cross: []};
    try { out.top = capture(); } catch (e) { out.top = {error: String(e && e.message || e).slice(0, 80)}; }
    var fr = document.querySelectorAll("iframe");
    for (var i = 0; i < fr.length; i++) {
      try {
        var cw = fr[i].contentWindow; var d = cw.document;           // throws when cross-origin
        if (d && cw.__probeShape) out.same.push(JSON.parse(cw.__probeShape()));
        else out.same.push({note: "same_origin_frame_without_probe_script"});
      } catch (e) {
        var h = ""; try { h = new URL(fr[i].src, location.href).hostname; } catch (_) {}
        out.cross.push(h);
      }
    }
    return JSON.stringify(out);
  };

  // ---- login helpers for the app's own auto-login test. They only ever act on a breakoutprop.com page. ----
  function vis(el) { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
  function hostOk(h) { return /(^|\.)breakoutprop\.com$/.test(h || ""); }
  function captchaInfo() {
    var sel = '.cf-turnstile,.g-recaptcha,.h-captcha,iframe[src*="challenges.cloudflare.com"],iframe[src*="recaptcha"],iframe[src*="hcaptcha"]';
    var els = Array.prototype.slice.call(document.querySelectorAll(sel)), best = {present: els.length > 0, w: 0, h: 0};
    els.forEach(function (e) { var r = e.getBoundingClientRect(); if (r.height > best.h) { best.h = Math.round(r.height); best.w = Math.round(r.width); } });
    return best;
  }
  window.__probeLogin = {
    find: function () {
      var out = {host_ok: hostOk(location.hostname), pw: false, id: false, submit: false, action_ok: true, captcha: captchaInfo(), cf: false, blocked: false};
      var low = ""; try { low = ((document.body && document.body.innerText) || "").slice(0, 3000).toLowerCase() + " " + (document.title || "").toLowerCase(); } catch (e) {}
      out.cf = !!document.querySelector('#challenge-form,#cf-chl-widget') || /just a moment|verify you are human|checking your browser|performing security verification/.test(low);
      out.blocked = /access denied|error 1005|you have been blocked|error 1020/.test(low);
      var pw = Array.prototype.slice.call(document.querySelectorAll("input[type=password]")).filter(vis)[0];
      if (!pw) { window.__lp = null; return JSON.stringify(out); }
      out.pw = true;
      var form = pw.form || pw.closest("form") || document;
      var inputs = Array.prototype.slice.call(form.querySelectorAll("input")).filter(vis), idx = inputs.indexOf(pw), idf = null;
      for (var i = idx - 1; i >= 0; i--) { if (/^(text|email|tel)$/.test(inputs[i].type || "text")) { idf = inputs[i]; break; } }
      var btn = form.querySelector("button[type=submit],input[type=submit]");
      if (!btn) { var bs = Array.prototype.slice.call(form.querySelectorAll("button,[role=button]")).filter(vis);
        btn = bs.filter(function (b) { return /log ?in|sign ?in|continue|submit|next/i.test((b.textContent || "") + (b.getAttribute("aria-label") || "")); })[0] || null; }
      try { if (form.action && typeof form.action === "string" && /^https?:/.test(form.action)) out.action_ok = hostOk(new URL(form.action).hostname); } catch (e) {}
      out.id = !!idf; out.submit = !!btn;
      window.__lp = {pw: pw, id: idf, btn: btn, form: form};
      return JSON.stringify(out);
    },
    focus: function (which) {
      var el = window.__lp && window.__lp[which]; if (!el || !hostOk(location.hostname)) return "no";
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, "");
      el.dispatchEvent(new Event("input", {bubbles: true})); el.focus();
      return document.activeElement === el ? "focused" : "nofocus";
    },
    // The value is typed into the field and NEVER returned; only an equal-length verdict comes back.
    fill: function (which, v) {
      var el = window.__lp && window.__lp[which]; if (!el || !hostOk(location.hostname)) return "no";
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, v);
      el.dispatchEvent(new Event("input", {bubbles: true})); el.dispatchEvent(new Event("change", {bubbles: true}));
      return el.value.length === v.length ? "ok" : "len";
    },
    filledLen: function (which, n) { var el = window.__lp && window.__lp[which]; return el && el.value.length === n ? "ok" : "len"; },
    submitDisabled: function () { var b = window.__lp && window.__lp.btn; return b ? !!(b.disabled || b.getAttribute("aria-disabled") === "true") : null; },
    click: function () {
      var l = window.__lp; if (!l || !hostOk(location.hostname)) return "no";
      if (l.btn) { l.btn.click(); return "clicked"; }
      if (l.form && l.form.requestSubmit) { l.form.requestSubmit(); return "requestSubmit"; }
      return "no_submit";
    },
    status: function () {
      var f = JSON.parse(window.__probeLogin.find());
      var sh = null; try { sh = capture(); } catch (e) {}
      var m = sh && sh.markers || {};
      var open = false;
      if (sh) sh.controls.forEach(function (c) { if (/^open terminal$/i.test(c.l)) open = true; });
      return JSON.stringify({pw: f.pw, cf: f.cf || !!m.cf, blocked: f.blocked, captcha: f.captcha, otp: !!m.otp_like || !!m.otc, twofa: !!m.twofa,
        buyish: !!(m.buy && m.sell) || !!m.send, email: !!m.email, nummatch: !!m.nummatch, open_terminal: open, host: location.hostname, path: sh ? sh.path : ""});
    },
    openTerminal: function () {
      var done = "none";
      Array.prototype.slice.call(document.querySelectorAll("button,a,[role=button]")).forEach(function (b) {
        if (done === "none" && /^open terminal$/i.test(redact(b.textContent, 40))) { b.click(); done = "clicked"; }
      });
      return done;
    }
  };

  function post(kind, extra, id) {
    var msg = {kind: kind, id: id || null, top: window.top === window, host: location.hostname};
    for (var k in extra) msg[k] = extra[k];
    try { if (window.PhoneProbe) window.PhoneProbe.postMessage(JSON.stringify(msg)); } catch (e) {}
  }

  function run(id) {
    var shape;
    try { shape = capture(); } catch (e) { shape = {error: String(e && e.message || e).slice(0, 80)}; }
    post("shape", {shape: shape}, id);
    var fr = document.querySelectorAll("iframe");
    for (var i = 0; i < fr.length; i++) {
      try { fr[i].contentWindow.postMessage({__probe: "capture", id: id}, "*"); } catch (e) {}
    }
  }
  window.__probeCapture = run;

  // Fixture-only: a command from the parent frame, honoured only on the fixture hosts.
  function fixtureCmd(d) {
    if (!/^fixture-[ab]\.test$/.test(location.hostname)) return;
    if (window.__fx && window.__fx.cmd) window.__fx.cmd(d, function (res) { post("fx", {res: res}, d.id); });
  }
  window.addEventListener("message", function (ev) {
    var d = ev.data;
    if (!d || typeof d !== "object" || !d.__probe) return;
    if (d.__probe === "capture") run(d.id);
    else if (d.__probe === "fx") fixtureCmd(d);
  });
  // one automatic capture per frame shortly after load, id "auto"
  window.addEventListener("load", function () { setTimeout(function () { run("auto"); }, 2500); });
})();
