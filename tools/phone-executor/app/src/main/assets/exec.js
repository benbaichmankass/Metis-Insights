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
      var ctrls = all("button,[role=tab],a").map(t);
      var loggedIn = /(^|\.)trade\.breakoutprop\.com$/.test(location.hostname) && ctrls.some(function (s) { return /^(positions|open orders|portfolio)$/i.test(s); });
      return {host: location.hostname, path_depth: (location.pathname || "/").split("/").length, pw: pw, email: email,
        codeWait: codeWait, loggedIn: loggedIn && !pw && !email, ticketOpen: !!submitBtn(),
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
    clickText: function (re, roleSel) {
      var rx = new RegExp(re, "i");
      var b = all(roleSel || "button,[role=tab],[role=button],a").filter(function (x) { return rx.test(t(x)); })[0];
      if (!b) return "none"; b.click(); return "clicked";
    },
    openTicket: function () {
      if (submitBtn()) return "open";
      var b = all("button,[role=tab],[role=button]").filter(function (x) { return /^order$/i.test(t(x)); })[0];
      if (!b) return "no_order_control"; b.click(); return "clicked";
    },
    // Symbol: click a control whose normalized text equals the venue symbol (ETHUSD, ETH/USD, ETH-USD ...).
    selectSymbol: function (sym) {
      var n = norm(sym);
      var cands = all("button,[role=button],[role=tab],a,[role=option],li").filter(function (x) { var s = norm(t(x)); return s === n || s === n + "T" || s === n + "PERP"; });
      if (!cands.length) return "none";
      cands[0].click(); return "clicked";
    },
    // Read the whole ticket back: tabs, labelled inputs with values, the TP/SL box, the submit label.
    ticket: function () {
      var f = form(); var sub = submitBtn();
      var cb = all("input[type=checkbox]").filter(function (c) { return /tp\s*\/\s*sl/i.test(labelFor(c) + " " + t(c.parentElement)); })[0];
      return {open: !!sub, tabs: tabs(f || document),
        inputs: inputs(f || document).map(function (i, k) { return {k: k, label: labelFor(i), type: i.type, mode: i.inputMode || "", value: i.value}; }),
        tpsl: cb ? cb.checked : null,
        submit: sub ? {text: t(sub), disabled: !!(sub.disabled || sub.getAttribute("aria-disabled") === "true")} : null,
        heading: all("h1,h2,h3,[role=heading]").map(t).slice(0, 8),
        alerts: all("[role=alert]").map(function (a) { return t(a).replace(/\d/g, "#"); }).slice(0, 6)};
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
      var cb = all("input[type=checkbox]").filter(function (c) { return /tp\s*\/\s*sl/i.test(labelFor(c) + " " + t(c.parentElement)); })[0];
      if (!cb) { cb = Array.prototype.slice.call(document.querySelectorAll("input[type=checkbox]")).filter(function (c) { return /tp\s*\/\s*sl/i.test(t(c.closest("label") || c.parentElement)); })[0]; }
      if (!cb) return "none";
      if (cb.checked !== on) { (cb.closest("label") || cb).click(); }
      return cb.checked === on ? "ok" : "unchanged";
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
