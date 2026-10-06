// Metis phone executor: page-side helpers, injected into Breakout's terminal (trade.breakoutprop.com) by the app.
// Labels and roles only, never class names (design 3.5). Every function returns JSON-able data; the app decides.
// Nothing here reads cookies, storage or credentials. Values returned are OUR OWN typed order values and the
// visible control labels; account numbers and balances are never returned.
(function () {
  if (window.__ex) return;
  function hostOk() { return /(^|\.)breakoutprop\.com$/.test(location.hostname || ""); }
  function vis(el) { if (!el) return false; var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
  function t(el) { return ((el && (el.innerText || el.textContent)) || "").replace(/\s+/g, " ").trim(); }
  function all(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)).filter(vis); }
  function norm(s) { return (s || "").toUpperCase().replace(/[^A-Z0-9]/g, ""); }
  function setVal(el, v) {
    var proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
  }
  function labelFor(el) {
    var l = "";
    if (el.id) { var f = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (f) l = t(f); }
    if (!l && el.getAttribute("aria-label")) l = el.getAttribute("aria-label");
    if (!l && el.getAttribute("aria-labelledby")) { var e = document.getElementById(el.getAttribute("aria-labelledby")); if (e) l = t(e); }
    if (!l) { var p = el.closest("label"); if (p) l = t(p); }
    if (!l) { var n = el.parentElement, d = 0; while (n && d < 4 && !l) { var lab = n.querySelector("label"); if (lab && vis(lab)) l = t(lab); n = n.parentElement; d++; } }
    if (!l && el.placeholder) l = el.placeholder;
    return l;
  }
  function form() {
    var sub = submitBtn(); return sub ? (sub.closest("form") || sub.parentElement.parentElement) : null;
  }
  function submitBtn() {
    return all("button").filter(function (b) { return /^(long|short)\s*\((buy|sell)\)/i.test(t(b)); })[0] || null;
  }
  function inputs(root) {
    return Array.prototype.slice.call((root || document).querySelectorAll("input")).filter(function (i) {
      return vis(i) && i.type !== "hidden" && i.type !== "range" && i.type !== "checkbox";
    });
  }
  // ---- instrument selection (fix 2026-10-05 ~22:40Z) ----
  // The first dry ticket found the terminal on BTC: the old selectSymbol only clicked a control whose exact text was
  // the venue symbol, the 1a probe had redacted the symbol strip's labels to "*", nothing matched, and the ticket was
  // (correctly) refused "symbol ETH not verified". The page's selector shape is still UNMEASURED, so symbolStep() tries
  // the routes a DXtrade-style terminal offers, ONE move per call, and the app reads the result back between moves:
  //   1 a visible control whose exact text is the symbol (strip button, watchlist row, search result)
  //   2 a search box outside the ticket: type the symbol, then click the one result that starts with it
  //   3 the current-symbol display (BTCUSD / BTC/USD, often a picker) : open it, then route 2
  // Every move is a selection, never a submit; the app refuses unless the ticket's submit label names the asset.
  function base(sym) { return norm(sym).replace(/(USD[TC]?|PERP)$/, ""); }
  function inForm(el) { var f = form(); return !!(f && f.contains(el)); }
  function looksLikeSymbol(s) { return /^[A-Z]{2,6}(USD[TC]?|PERP)$/.test(norm(s)) || /^[A-Za-z]{2,6}\s*[\/-]\s*[A-Za-z]{2,5}$/.test((s || "").trim()); }
  function innermost(list) { return list.filter(function (c) { return !list.some(function (o) { return o !== c && c.contains(o); }); }); }
  function clickables(sel) { return all(sel || "button,[role=button],[role=tab],[role=option],[role=menuitem],a,li,[role=row],tr,td"); }
  function exactSymbolControls(sym) {
    var n = norm(sym), b = base(sym);
    // never a tab inside the ticket (Market / Limit / Buy / Sell are tabs, not symbols)
    var full = clickables().filter(function (x) { var s = norm(t(x)); return (s === n || s === n + "T" || s === n + "PERP") && !(x.getAttribute("role") === "tab" && inForm(x)); });
    var bare = clickables("button,[role=button],[role=option],a,li").filter(function (x) { return norm(t(x)) === b && !inForm(x); });
    return innermost(full).concat(innermost(bare));
  }
  // Route 1b (fix 06:24Z, am-2 "symbol route: none"): a watchlist/portfolio row whose symbol is a plain text LEAF
  // (span/div/td, no role) with the click handler on an ancestor. Exact symbol text only, outside the ticket.
  function symbolLabels(sym) {
    var n = norm(sym);
    return all("span,div,td,p,strong,b").filter(function (x) {
      if (x.children.length || inForm(x)) return false; var s = norm(t(x)); return s === n || s === n + "T";
    });
  }
  // "Order" (older layout) or "Order form" (the panel toggle, MEASURED 08:58Z).
  var ORDER_RE = /^order( form)?$/i;
  // Watchlist button "<BASE> <signed %>" (MEASURED 08:58Z: "ETH -0.5%", "BTC -1.2%" ...), outside the ticket.
  function watchControls(sym) {
    var rx = new RegExp("^" + base(sym) + "\\s+[-+]?\\d[\\d.,]*\\s*%$", "i");
    return innermost(clickables("button,[role=button],[role=tab],[role=option],a,li").filter(function (x) { return !inForm(x) && rx.test(t(x)); }));
  }
  // The open-instrument chip "<BASE> x" (MEASURED 08:58Z: "ETH x"): the shown symbol only when exactly ONE chip exists.
  function chipSymbol() {
    var c = all("button,[role=button],[role=tab]").map(t).filter(function (x) { return /^[A-Za-z]{2,6}\s*[x\u00d7]$/.test(x); });
    return c.length === 1 ? c[0].replace(/\s*[x\u00d7]$/, "") : "";
  }
  function tpslBox() {
    var cb = all("input[type=checkbox]").filter(function (c) { return /tp\s*\/\s*sl/i.test(labelFor(c) + " " + t(c.parentElement)); })[0];
    if (!cb) { cb = Array.prototype.slice.call(document.querySelectorAll("input[type=checkbox]")).filter(function (c) { return /tp\s*\/\s*sl/i.test(t(c.closest("label") || c.parentElement)); })[0]; }
    return cb || null;
  }
  function desc(x) {
    return {tag: x.tagName.toLowerCase(), role: x.getAttribute("role") || "", type: x.getAttribute("type") || "",
      checked: x.getAttribute("aria-checked") || (x.type === "checkbox" ? String(x.checked) : ""),
      expanded: x.getAttribute("aria-expanded") || "", text: t(x).replace(/\d/g, "#").slice(0, 24)};
  }
  function tpslTexts(cb) {
    var f = form() || document;
    return innermost(Array.prototype.slice.call(f.querySelectorAll("button,[role=button],[role=switch],[role=checkbox],[aria-expanded],label,div,span,p")).filter(function (x) {
      return vis(x) && /^tp\s*\/\s*sl$/i.test(t(x)) && !(cb && (x.contains(cb) || x === cb.closest("label")));
    }));
  }
  function tpslSwitches(h, cb) {
    var p = h.parentElement, d = 0, sw = [];
    while (p && d < 2 && !sw.length) {
      sw = Array.prototype.slice.call(p.querySelectorAll("[role=switch],[role=checkbox],button,input[type=checkbox]")).filter(function (x) {
        return vis(x) && x !== cb && !h.contains(x) && (x.tagName === "INPUT" || !t(x));
      });
      p = p.parentElement; d++;
    }
    return sw;
  }
  function qtyUnitBtn() {
    var f = form(); if (!f) return null;
    return Array.prototype.slice.call(f.querySelectorAll("button,[role=button]")).filter(function (b) {
      return vis(b) && /quantity unit/i.test((b.getAttribute("aria-label") || "") + " " + (b.getAttribute("title") || ""));
    })[0] || null;
  }
  function searchBox() {
    return inputs(document).filter(function (i) {
      return (i.type === "text" || i.type === "search") && !inForm(i) &&
        /search|symbol|instrument/i.test(labelFor(i) + " " + (i.getAttribute("aria-label") || "") + " " + (i.placeholder || ""));
    })[0] || null;
  }
  function symbolLooking(sel) {
    var sub = submitBtn();
    return innermost(all(sel).filter(function (x) { return x !== sub && x.getAttribute("role") !== "tab" && looksLikeSymbol(t(x)); }));
  }
  // The page's current-symbol DISPLAY (read only): a heading or a control showing one symbol; "" when none or several.
  function currentSymbolDisplay() { var c = symbolLooking("button,[role=button],[role=combobox],[aria-haspopup],h1,h2,h3,[role=heading]"); return c.length === 1 ? t(c[0]) : ""; }
  // The current-symbol PICKER (clickable): a control with a popup, else the one symbol-looking control; headings
  // are never clicked. Several symbol-looking controls = a strip, not a picker.
  function currentSymbolControl() {
    var c = symbolLooking("button,[role=button],[role=combobox],[aria-haspopup]");
    var pick = c.filter(function (x) { return x.getAttribute("aria-haspopup") || x.getAttribute("role") === "combobox" || x.hasAttribute("aria-expanded"); });
    if (pick.length) return pick[0];
    return c.length === 1 ? c[0] : null;
  }
  // Inputs BY LABEL (fix 2026-10-05): an index from one ticket() read is not a stable identity; the field set
  // changes with the TP/SL box and the quantity-unit toggle. `prefer` narrows when several labels match.
  function byLabel(re, prefer) {
    var rx = new RegExp(re, "i"), px = prefer ? new RegExp(prefer, "i") : null;
    var list = inputs(form() || document);
    var hits = list.filter(function (i) { return rx.test(labelFor(i)); });
    if (hits.length > 1 && px) hits = hits.filter(function (i) { return px.test(labelFor(i)); });
    return {list: list, hits: hits};
  }
  function near(el) {   // the field's own container text (unit toggle, adornment), digits masked, no values
    var n = el.parentElement, d = 0, s = "";
    while (n && d < 3 && s.length < 8) { s = t(n); n = n.parentElement; d++; }
    return s.replace(/\d/g, "#").slice(0, 80);
  }
  function fieldInfo(h) {
    if (h.hits.length !== 1) return {n: h.hits.length, k: -1, labels: h.hits.map(labelFor)};
    var i = h.hits[0]; return {n: 1, k: h.list.indexOf(i), label: labelFor(i), value: i.value, near: near(i)};
  }
  function tabs(root) {
    return Array.prototype.slice.call((root || document).querySelectorAll("[role=tab]")).filter(vis).map(function (b) {
      return {text: t(b), selected: b.getAttribute("aria-selected") === "true" || b.getAttribute("data-state") === "active"};
    });
  }

  window.__ex = {
    state: function () {
      var pw = all("input[type=password]").length > 0;
      var email = all("input[type=email],input[autocomplete=email],input[name*=mail i]").length > 0;
      var body = t(document.body).slice(0, 4000);
      var codeWait = /check your (e-?mail|inbox)|we('ve| have)? sent|tap the number|select the number|matching number/i.test(body);
      // LOGGED IN (fix 2026-10-05 ~20:20Z): the account landing on trade.breakoutprop.com has no tabs and none
      // of the exact control labels the first build required, so a good login read as "other" and was reloaded
      // every 30 s. Now: trade host, no login form, and either inside an account (/.../account/.../) or the page
      // shows the terminal's navigation words or an account link.
      var tradeHost = /(^|\.)trade\.breakoutprop\.com$/.test(location.hostname);
      var onAccount = /\/account\//.test(location.pathname || "");
      var full = t(document.body).slice(0, 20000);
      var navText = /\b(positions|open orders|closed orders|portfolio|trades)\b/i.test(full);
      var links = {};
      Array.prototype.slice.call(document.querySelectorAll("a[href]")).forEach(function (a) {
        try { var u = new URL(a.href, location.href); if (u.hostname === location.hostname && /\/account\//.test(u.pathname)) links[u.origin + u.pathname] = 1; } catch (e) {}
      });
      var linkList = Object.keys(links);
      var loggedIn = tradeHost && (onAccount || navText || linkList.length > 0);
      return {host: location.hostname, path_depth: (location.pathname || "/").split("/").length, pw: pw, email: email,
        codeWait: codeWait, loggedIn: loggedIn && !pw && !email && !codeWait, ticketOpen: !!submitBtn(),
        onAccount: onAccount, accountLinkCount: linkList.length,
        // Navigation targets stay in the app's memory / private storage; never logged or reported.
        accountHref: onAccount ? location.href : "", singleAccountLink: linkList.length === 1 ? linkList[0] : "",
        challenged: /just a moment|verify you are human|cf-chl/i.test(body)};
    },
    // LOGIN step 1: type the account email and continue. The email value is never returned.
    loginEmail: function (email) {
      if (!hostOk()) return "bad_host";
      var el = all("input[type=email],input[autocomplete=email],input[name*=mail i]")[0];
      if (!el) return "no_email_field";
      el.focus(); setVal(el, email);
      if (el.value !== email) return "not_set";
      var b = all("button").filter(function (b) { return /continue|log ?in|sign ?in|next|send|submit/i.test(t(b) + " " + (b.getAttribute("aria-label") || "")); })[0];
      if (b && !(b.disabled || b.getAttribute("aria-disabled") === "true")) { b.click(); return "clicked"; }
      if (el.form && el.form.requestSubmit) { el.form.requestSubmit(); return "submitted"; }
      return "no_button";
    },
    // LOGIN step 2: the number the waiting page shows (the one to pick in the email). Exactly one prominent
    // 1-3 digit number, else "" (fail closed).
    loginNumber: function () {
      var c = all("h1,h2,h3,h4,p,div,span,strong,b").filter(function (e) { return /^\d{1,3}$/.test(t(e)) && e.children.length === 0; });
      if (!c.length) return "";
      c.sort(function (a, b) { return parseFloat(getComputedStyle(b).fontSize) - parseFloat(getComputedStyle(a).fontSize); });
      var top = parseFloat(getComputedStyle(c[0]).fontSize);
      var same = c.filter(function (e) { return parseFloat(getComputedStyle(e).fontSize) === top && t(e) !== t(c[0]); });
      return same.length ? "" : t(c[0]);
    },
    // TERMINAL GATE (2026-10-06 05:21Z dry test: claimed on an /account/ page that was not the trading terminal,
    // "order control not found"). Ready = an "Order" control, an open ticket, or Buy+Sell tabs are on the page;
    // OR (fix 06:17Z: the live terminal with the ticket CLOSED had none of those exact shapes, "terminal did not
    // load" on the real terminal) the 1a probe's classifier, MEASURED on this page over 6 captures: trade host,
    // a /trade path, buy AND sell text markers, and tabs >= 3 or inputs >= 2.
    terminal: function () {
      var ctl = all("button,[role=tab],[role=button]").filter(function (x) { return ORDER_RE.test(t(x)); }).length > 0;
      var tb = all("[role=tab]").map(t);
      var bs = tb.some(function (x) { return /^buy$/i.test(x); }) && tb.some(function (x) { return /^sell$/i.test(x); });
      var body = t(document.body).slice(0, 20000);
      var probe = /(^|\.)trade\.breakoutprop\.com$/.test(location.hostname) && /\/trade(\/|$)/.test(location.pathname || "") &&
        /\bbuy\b/i.test(body) && /\bsell\b/i.test(body) && (tb.length >= 3 || inputs(document).length >= 2);
      // PANEL layout (MEASURED 2026-10-06 08:58Z terminal_miss on the real terminal, ticket closed: controls "Order form",
      // "Open orders", "Positions", "Market chart", "Order book" ...; tabs=0 inputs=0, no buy/sell text).
      var ct = all("button,[role=tab],[role=button]").map(t);
      var panels = /(^|\.)trade\.breakoutprop\.com$/.test(location.hostname) &&
        ct.some(function (x) { return /^positions$/i.test(x); }) && ct.some(function (x) { return /^open orders$/i.test(x); });
      return {ready: ctl || !!submitBtn() || bs || probe || panels, panels: panels, orderControl: ctl, ticketOpen: !!submitBtn(), buySell: bs,
        probe: probe, tabs: tb.length, inputs: inputs(document).length};
    },
    // Control texts on the page (buttons, tabs, role=button), first 40, digits masked: OUR OWN UI labels only, for
    // the "terminal did not load" event and the refusal dump. Never values, never account numbers.
    // Symbol-selector candidates for the refusal dump (fix 06:24Z): elements whose text or aria-label looks like an
    // instrument (BTC, BTCUSD, ETH/USD ...) or names an instrument/watchlist/search control, plus search-like inputs.
    // Text, aria-label, role and tag only; digits masked; first 30. Our own UI, never values or account numbers.
    symbolCandidates: function () {
      var out = [], seen = {}, kw = /instrument|symbol|watchlist|market|search|asset/i;
      all("button,[role],a,li,span,div,td,h1,h2,h3,input").forEach(function (x) {
        if (out.length >= 30) return;
        var tx = x.tagName === "INPUT" ? "" : t(x), al = x.getAttribute("aria-label") || "", ph = x.getAttribute("placeholder") || "";
        var leafish = x.tagName === "INPUT" || x.children.length <= 2;
        var hit = (tx.length <= 24 && (looksLikeSymbol(tx) || /^[A-Z]{2,5}$/.test(tx))) || kw.test(al + " " + ph) ||
          (x.tagName === "INPUT" && /text|search/.test(x.type) && !inForm(x));
        if (!hit || !leafish) return;
        var row = {tag: x.tagName.toLowerCase(), role: x.getAttribute("role") || "", text: tx.replace(/\d/g, "#").slice(0, 24),
          aria: al.replace(/\d/g, "#").slice(0, 40), ph: ph.replace(/\d/g, "#").slice(0, 30), popup: !!x.getAttribute("aria-haspopup"), inTicket: inForm(x)};
        var k = JSON.stringify(row); if (!seen[k]) { seen[k] = 1; out.push(row); }
      });
      return out;
    },
    controls: function () {
      var seen = {}, out = [];
      all("button,[role=tab],[role=button]").forEach(function (x) {
        var s = t(x).replace(/\d/g, "#").slice(0, 40); if (s && !seen[s] && out.length < 40) { seen[s] = 1; out.push(s); }
      });
      return out;
    },
    // The account's terminal URL, derived from the CURRENT /account/<id>/ path (deterministic: the account the
    // page is already on). "" when the path is not an account path. Never reported.
    terminalHref: function () {
      var m = /^(.*\/account\/[^\/]+)(\/.*)?$/.exec(location.pathname || "");
      return m ? location.origin + m[1] + "/trade" : "";
    },
    clickText: function (re, roleSel) {
      var rx = new RegExp(re, "i");
      var b = all(roleSel || "button,[role=tab],[role=button],a").filter(function (x) { return rx.test(t(x)); })[0];
      if (!b) return "none"; b.click(); return "clicked";
    },
    openTicket: function () {
      if (submitBtn()) return "open";
      var b = all("button,[role=tab],[role=button]").filter(function (x) { return ORDER_RE.test(t(x)); })[0];
      if (!b) return "no_order_control"; b.click(); return "clicked";
    },
    // What the ticket says it trades: the text after "Long (buy)" / "Short (sell)" on the submit button ("BTC").
    symbolOnTicket: function () {
      var sub = submitBtn(); if (!sub) return "";
      var m = t(sub).match(/\((buy|sell)\)\s*(.+)$/i); return m ? m[2].trim() : "";
    },
    // The submit label first; else the page's current-symbol display (ticket closed). "" when neither is readable.
    symbolShown: function () {
      return window.__ex.symbolOnTicket() || currentSymbolDisplay() || chipSymbol();
    },
    // ONE move toward the venue symbol; the app calls it again after reading back. Returns the route taken:
    // done | clicked_symbol | typed_search | search_not_set | clicked_result | no_result | ambiguous | opened_picker | none
    symbolStep: function (sym) {
      if (!hostOk()) return "bad_host";
      if (!looksLikeSymbol(sym)) return "not_a_symbol";   // ETHUSD, ETH/USD, SOLUSDT ...; never a bare word like "Market"
      var n = norm(sym), b = base(sym), shown = norm(window.__ex.symbolShown());
      if (shown && (shown === n || shown === n + "T" || shown === b)) return "done";
      var ex = exactSymbolControls(sym); if (ex.length) { ex[0].click(); return "clicked_symbol"; }
      var sb = searchBox();
      if (sb) {
        if (norm(sb.value) !== n) { sb.focus(); setVal(sb, sym); return norm(sb.value) === n ? "typed_search" : "search_not_set"; }
        var res = innermost(clickables().filter(function (x) { return !inForm(x) && x !== sb && norm(t(x)).indexOf(n) === 0; }));
        if (!res.length) return "no_result"; if (res.length > 1) return "ambiguous"; res[0].click(); return "clicked_result";
      }
      var wc = watchControls(sym); if (wc.length === 1) { wc[0].click(); return "clicked_watch"; }
      var lb = symbolLabels(sym); if (lb.length === 1) { lb[0].click(); return "clicked_label"; }
      if (lb.length > 1) return "ambiguous";
      var pk = currentSymbolControl(); if (pk) { pk.click(); return "opened_picker"; }
      return "none";
    },
    // Kept for the Dry test button / older callers: one exact-text click, else "none".
    selectSymbol: function (sym) { var ex = exactSymbolControls(sym); if (!ex.length) return "none"; ex[0].click(); return "clicked"; },
    // Read the whole ticket back: tabs, labelled inputs with values, the TP/SL box, the submit label.
    ticket: function () {
      var f = form(); var sub = submitBtn();
      var cb = all("input[type=checkbox]").filter(function (c) { return /tp\s*\/\s*sl/i.test(labelFor(c) + " " + t(c.parentElement)); })[0];
      return {open: !!sub, tabs: tabs(f || document),
        inputs: inputs(f || document).map(function (i, k) { return {k: k, label: labelFor(i), type: i.type, mode: i.inputMode || "", value: i.value, near: near(i)}; }),
        symbol: window.__ex.symbolOnTicket(),
        tpsl: cb ? cb.checked : null,
        submit: sub ? {text: t(sub), disabled: !!(sub.disabled || sub.getAttribute("aria-disabled") === "true")} : null,
        heading: all("h1,h2,h3,[role=heading]").map(t).slice(0, 8),
        alerts: all("[role=alert]").map(function (a) { return t(a).replace(/\d/g, "#"); }).slice(0, 6),
        // page controls (digits masked): shows the symbol strip/picker when the symbol route fails
        controls: window.__ex.controls(),
        symbolCandidates: window.__ex.symbolCandidates(),
        tpslArea: window.__ex.tpslArea()};
    },
    tab: function (name) {
      var f = form() || document;
      var b = Array.prototype.slice.call(f.querySelectorAll("[role=tab]")).filter(function (x) { return vis(x) && t(x).toLowerCase() === name.toLowerCase(); })[0];
      if (!b) return "none";
      if (b.getAttribute("aria-selected") !== "true") b.click();
      return "clicked";
    },
    tabSelected: function (name) {
      var f = form() || document;
      var b = Array.prototype.slice.call(f.querySelectorAll("[role=tab]")).filter(function (x) { return vis(x) && t(x).toLowerCase() === name.toLowerCase(); })[0];
      return b ? (b.getAttribute("aria-selected") === "true" || b.getAttribute("data-state") === "active") : null;
    },
    // Field BY LABEL: {n: matches, k, label, value, near}; n != 1 means not unique (labels listed) and nothing is set.
    readByLabel: function (re, prefer) { return fieldInfo(byLabel(re, prefer)); },
    setByLabel: function (re, prefer, v) {
      var h = byLabel(re, prefer); if (h.hits.length !== 1) return fieldInfo(h);
      var i = h.hits[0]; i.focus(); setVal(i, String(v)); i.blur(); return fieldInfo(h);
    },
    focusByLabel: function (re, prefer) {
      var h = byLabel(re, prefer); if (h.hits.length !== 1) return "none";
      var i = h.hits[0]; setVal(i, ""); i.focus(); return document.activeElement === i ? "focused" : "nofocus";
    },
    // Set the k-th visible input of the ticket (index from ticket().inputs). Returns the read-back value.
    setInput: function (k, v) {
      var i = inputs(form() || document)[k]; if (!i) return null;
      i.focus(); setVal(i, String(v)); i.blur(); return i.value;
    },
    focusInput: function (k) {
      var i = inputs(form() || document)[k]; if (!i) return "none";
      setVal(i, ""); i.focus(); return document.activeElement === i ? "focused" : "nofocus";
    },
    setTpsl: function (on) {
      var cb = tpslBox();
      if (!cb) return "none";
      if (cb.checked !== on) { (cb.closest("label") || cb).click(); }
      return cb.checked === on ? "ok" : "unchanged";
    },
    // QUANTITY UNIT (MEASURED 2026-10-06 09:16Z am-3 dump): a button inside the ticket, aria-label "Toggle quantity unit",
    // text "USD" = the quantity field is a USD notional. Returns the button's text, or "" when there is no such toggle.
    qtyUnit: function () { var b = qtyUnitBtn(); return b ? t(b) : ""; },
    toggleQtyUnit: function () { var b = qtyUnitBtn(); if (!b) return "none"; b.click(); return "clicked"; },
    // TP/SL section (MEASURED 09:16Z: a "TP/SL" control inside the ticket; the TP/SL price inputs sit behind it).
    // "ok" when a Take profit input is already visible; else ticks the TP/SL checkbox or clicks the TP/SL control.
    openTpsl: function (attempt) {
      // ONE move per call; the app reads back (polls) between moves. MEASURED am-4 (09:48Z): the TP/SL checkbox read
      // checked but no TP/SL inputs (and no Simple / Risk-Reward tabs) were shown; am-2 (opened by hand) showed
      // "Take profit price" / "Stop loss price" with the box checked. Deterministic candidates, by attempt number:
      //   tick the box if unchecked; phase 0: click the "TP/SL" text element ONCE (handler may be on an ancestor);
      //   phase 1: click the switch / checkbox-role / text-less button beside that text ONCE (handler on a sibling).
      // Returns ok | ticked | expanded | switched | none | ambiguous | wait.
      if (byLabel("take ?profit|\\btp\\b", "price").hits.length) return "ok";
      var cb = tpslBox();
      if (cb && !cb.checked) { (cb.closest("label") || cb).click(); return "ticked"; }
      var hs = tpslTexts(cb);
      if (hs.length > 1) return "ambiguous";
      if (!hs.length) return cb ? "wait" : "none";
      var ph = attempt || 0;   // phase from the app: 0 = header not yet clicked, 1 = switch not yet clicked, 2 = done
      if (ph === 0) { hs[0].click(); return "expanded"; }
      if (ph === 1) {
        var sw = tpslSwitches(hs[0], cb);
        if (sw.length === 1) { sw[0].click(); return "switched"; }
        return sw.length ? "ambiguous" : "wait";
      }
      return "wait";
    },
    // The TP/SL area for the refusal dump: the "TP/SL" text element, up to 3 ancestors and their direct children, as
    // tag / role / type / aria-checked / aria-expanded / short text (digits masked). Our own UI only.
    tpslArea: function () {
      var hs = tpslTexts(tpslBox()); if (!hs.length) return [];
      var out = [], n = hs[0], d = 0;
      while (n && d < 4) {
        out.push({depth: d, node: desc(n), kids: Array.prototype.slice.call(n.children).slice(0, 8).map(desc)});
        n = n.parentElement; d++;
      }
      return out;
    },
    submit: function () {
      var b = submitBtn(); if (!b) return "none";
      if (b.disabled || b.getAttribute("aria-disabled") === "true") return "disabled";
      b.click(); return "clicked";
    },
    dialog: function () {
      var d = all("[role=dialog],[role=alertdialog]")[0];
      if (!d) return null;
      return {text: t(d).slice(0, 300), buttons: Array.prototype.slice.call(d.querySelectorAll("button")).filter(vis).map(t)};
    },
    dialogClick: function (re) {
      var d = all("[role=dialog],[role=alertdialog]")[0]; if (!d) return "none";
      var rx = new RegExp(re, "i");
      var b = Array.prototype.slice.call(d.querySelectorAll("button")).filter(function (x) { return vis(x) && rx.test(t(x)); })[0];
      if (!b) return "none"; b.click(); return "clicked";
    },
    // Rows of the currently shown table-like list (Positions / Open orders): visible text per row.
    rows: function () {
      var rs = all("tr,[role=row]");
      return rs.map(function (r) { return Array.prototype.slice.call(r.querySelectorAll("td,th,[role=cell],[role=gridcell],[role=columnheader]")).map(t); })
        .filter(function (c) { return c.length > 1; }).slice(0, 40);
    },
    rowClose: function (sym, sideRe) {
      var n = norm(sym); var rx = new RegExp(sideRe, "i");
      var r = all("tr,[role=row]").filter(function (r) { var s = t(r); return norm(s).indexOf(n) >= 0 && rx.test(s); })[0];
      if (!r) return "none";
      var b = Array.prototype.slice.call(r.querySelectorAll("button,[role=button]")).filter(function (x) { return vis(x) && /close/i.test(t(x) + " " + (x.getAttribute("aria-label") || "")); })[0];
      if (!b) return "no_close"; b.click(); return "clicked";
    }
  };
})();
