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
  // Fix 2026-10-07 (SOL-PICKER): MEASURED 2026-10-06 dumps also show the price between them ("SOL ###.## -#.##%").
  function watchControls(sym) {
    var rx = new RegExp("^" + base(sym) + "(\\s+[\\d.,]+)?\\s+[-+]?\\d[\\d.,]*\\s*%$", "i");
    return innermost(clickables("button,[role=button],[role=tab],[role=option],a,li").filter(function (x) { return !inForm(x) && rx.test(t(x)); }));
  }
  // The open-instrument chip "<BASE> x" (MEASURED 08:58Z: "ETH x"): the shown symbol only when exactly ONE chip exists.
  function chipSymbol() {
    var mc = marketChip(); if (mc) { var m = CHIP_RE.exec(t(mc)); return m ? m[1].toUpperCase() : ""; }
    var c = all("button,[role=button],[role=tab]").map(t).filter(function (x) { return /^[A-Za-z]{2,6}\s*[x\u00d7]$/.test(x); });
    return c.length === 1 ? c[0].replace(/\s*[x\u00d7]$/, "") : "";
  }
  // ---- the "Select market" picker (fix 2026-10-07, lane SOL-PICKER, PI-20261006-APBY4NTV-0007) ----
  // MEASURED (2026-10-06 dry-fill dumps on /api/bot/prop/tickets?account_id=breakout_2): the terminal's instrument
  // picker is ONE div role=button, aria-label "Select market", text "<BASE> <leverage>x" ("ETH #x", digits masked).
  // UNMEASURED: the list it opens. So the row is found by what it says, not by its markup: inside an open
  // dialog/listbox/menu when the page marks one, else among the elements that APPEARED after the chip was clicked
  // (a snapshot taken just before the click), so the always-visible watchlist ("SOL ###.## -#.##%") never competes.
  // A row is the nearest clickable ancestor of text that starts with the base as a whole word ("SOL", "SOL/USD",
  // "SOLUSD", "SOL Solana"; never "SOLANA..."). Exactly one row is clicked; two = ambiguous, refuse; none = fall back
  // to the watchlist. Every move is a selection; the app still refuses unless the submit label names the asset.
  var CHIP_RE = /^([A-Za-z]{2,6})(\s*\d+(\.\d+)?\s*[x\u00d7]|\s*[x\u00d7])?$/;
  var ROW_SEL = "button,[role=button],[role=option],[role=menuitem],[role=menuitemradio],[role=row],[role=gridcell],li,a,tr";
  function marketChip() {
    var c = all("button,[role=button],[role=combobox]").filter(function (x) { return /^select market$/i.test(x.getAttribute("aria-label") || "") && !inForm(x); });
    return c.length === 1 ? c[0] : null;
  }
  function startsWithBase(s, b) {
    var u = (s || "").trim().toUpperCase();
    return new RegExp("^" + b + "(?![A-Z])").test(u) || /^(USD[TC]?|PERP)/.test(norm(u).slice(b.length)) && norm(u).indexOf(b) === 0;
  }
  function popupRoots(chip) {
    var r = all("[role=dialog],[role=listbox],[role=menu],[aria-modal=true],[data-radix-popper-content-wrapper]");
    var ctl = chip && chip.getAttribute("aria-controls"); if (ctl) { var e = document.getElementById(ctl); if (e && vis(e) && r.indexOf(e) < 0) r.push(e); }
    return r.filter(function (x) { return !inForm(x); });
  }
  function marketCandidates(sym) {
    var b = base(sym), chip = marketChip();
    var leaves = all("span,div,p,strong,b,td,li,a,button,[role]").filter(function (x) {
      return !inForm(x) && !(chip && (chip === x || chip.contains(x) || x.contains(chip))) && t(x).length <= 40 && startsWithBase(t(x), b);
    });
    var roots = popupRoots(chip), pk = window.__ex._mk;
    if (roots.length) leaves = leaves.filter(function (x) { return roots.some(function (r) { return r.contains(x); }); });
    else if (pk && pk.pre) leaves = leaves.filter(function (x) { return pk.pre.indexOf(x) < 0; });
    else return [];
    var rows = [];
    leaves.forEach(function (x) { var r = x.closest(ROW_SEL) || x; if (chip && r.contains(chip)) r = x; if (rows.indexOf(r) < 0) rows.push(r); });
    return innermost(rows);
  }
  function marketSearch() {
    var chip = marketChip(), roots = popupRoots(chip), pk = window.__ex._mk;
    return inputs(document).filter(function (i) {
      if (inForm(i) || !(i.type === "text" || i.type === "search")) return false;
      if (roots.length) return roots.some(function (r) { return r.contains(i); });
      return !!(pk && pk.preInputs && pk.preInputs.indexOf(i) < 0);
    })[0] || null;
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

  // ---- POSITION AMEND (PROP-TRAIL-PHONE, 2026-10-07): the trail's SL (and a TP revision) edited on an OPEN position.
  // UNMEASURED on Breakout's terminal: no capture has shown the Positions row's edit control or its dialog. So every
  // helper finds things by text/label/role only, demands exactly ONE match, and returns a dump (our own UI labels,
  // digits masked) when it cannot; the app refuses before any click it cannot verify. Never a close control.
  var NOT_EDIT = /close|flatten|reverse|cancel|remove|delete|partial/i;
  // BREAKOUT-POS-UNPARSED (2026-10-14): the live Positions view is a DIV GRID (no tr / [role=row]), so posRows found
  // nothing and every trail amend reported no_position while the ETH short was open. With no table row, the ONE
  // Positions div-grid header's data rows are matched instead (same discovery as readGrid); GRID_HDR remembers each
  // grid row's header so rowCells can align its cells.
  var GRID_HDR = typeof WeakMap === "function" ? new WeakMap() : null;
  function posRows(sym, sideRe) {
    var n = norm(sym), b = base(sym), rx = new RegExp(sideRe, "i");
    var hit = function (r) { var s = t(r) + " " + wt(r), ns = norm(s); return (ns.indexOf(n) >= 0 || ns.indexOf(b) >= 0) && rx.test(s); };
    var rs = innermost(all("tr,[role=row]").filter(function (r) { return hit(r) && !r.querySelector("th,[role=columnheader]"); }));
    if (rs.length || !GRID_HDR) return rs;
    var g = gridHeaders(POS_COLS); if (g.length !== 1) return [];
    return gridRowsOf(g[0]).rows.filter(function (r) { if (!hit(r)) return false; GRID_HDR.set(r, g[0]); return true; });
  }
  function isGridRow(r) { return !!(GRID_HDR && GRID_HDR.get(r)); }
  function ctlText(x) { return (t(x) + " " + (x.getAttribute("aria-label") || "") + " " + (x.getAttribute("title") || "")).trim(); }
  function rowCtls(r) { return Array.prototype.slice.call(r.querySelectorAll("button,[role=button],a")).filter(vis); }
  function rowCells(r) {
    var cells = Array.prototype.slice.call(r.querySelectorAll("td,[role=cell],[role=gridcell]"));
    var tb = r.closest("table,[role=table],[role=grid]"), hs = [];
    if (!cells.length && isGridRow(r)) { cells = kids(r); hs = kids(GRID_HDR.get(r)).map(t); tb = null; }
    if (tb) hs = Array.prototype.slice.call(tb.querySelectorAll("th,[role=columnheader]")).map(t);
    var out = {}; if (hs.length === cells.length) cells.forEach(function (c, i) { out[hs[i]] = t(c); });
    return {headers: hs, map: out, n: cells.length};
  }
  // a combined "TP / SL" (or "SL / TP") column: its two halves, in the header's order; null when absent or not two
  function tpslCol(map) {
    var ks = Object.keys(map).filter(function (k) { return /^(tp\s*\/\s*sl|sl\s*\/\s*tp)$/i.test(k.trim()); });
    if (ks.length !== 1) return null;
    var parts = map[ks[0]].split("/").map(function (x) { return x.trim(); }); if (parts.length !== 2) return null;
    return /^tp/i.test(ks[0].trim()) ? {tp: parts[0], sl: parts[1]} : {sl: parts[0], tp: parts[1]};
  }
  // MEASURE-FIRST dump of a row's possibly-interactive elements: tag, role, aria-label / title / text, test id, the
  // first class names and the column it sits in -- digits masked, never a click. The div grid's edit control is
  // UNMEASURED, so this is the evidence a later build needs before anything in that row is ever clicked.
  function ctlDump(r) {
    var hs = isGridRow(r) ? kids(GRID_HDR.get(r)).map(t) : [], cs = isGridRow(r) ? kids(r) : [];
    var m = function (s) { return (s || "").replace(/\d/g, "#"); };
    return Array.prototype.slice.call(r.querySelectorAll("button,[role=button],a,[tabindex],svg,img,[onclick]")).filter(vis).slice(0, 12).map(function (x) {
      var ci = -1; cs.forEach(function (c, i) { if (c.contains(x)) ci = i; });
      return {col: ci >= 0 ? m(hs[ci]).slice(0, 16) : "", tag: x.tagName.toLowerCase(), role: x.getAttribute("role") || "",
        label: m(x.getAttribute("aria-label") || x.getAttribute("title") || t(x)).slice(0, 30), testid: m(x.getAttribute("data-testid") || "").slice(0, 30),
        cls: m(typeof x.className === "string" ? x.className : (x.getAttribute("class") || "")).split(/\s+/).slice(0, 3).join(" ").slice(0, 40)};
    });
  }
  // the dump as short strings that survive the server's 40-char scrub (positions diag)
  function ctlLines(d) {
    var out = []; d.forEach(function (c) {
      out.push((c.col ? c.col + ":" : "") + c.tag + (c.role ? "/" + c.role : "") + " " + c.label);
      if (c.testid || c.cls) out.push("  " + (c.testid ? "id=" + c.testid + " " : "") + (c.cls ? "cls=" + c.cls : ""));
    });
    return out.map(function (x) { return x.slice(0, 40); }).slice(0, 30);
  }
  function col(map, re) { var rx = new RegExp(re, "i"), ks = Object.keys(map).filter(function (k) { return rx.test(k); }); return ks.length === 1 ? map[ks[0]] : null; }
  // ---- TABLE READS (PROP-TRACKING, 2026-10-12): Positions + Closed orders, mapped by header text. First match wins,
  // so the more specific patterns come first ("Mark price" is not "Market", "Realized PnL" is not unrealized).
  // Breakout's Positions grid (read live 2026-10-12): Market | UP&L | Side | Value | Opening price | Current price |
  // TP / SL | Net P&L. There is NO size column, so qty is derived (see parseRow). "UP&L" is the unrealized P&L and is
  // the ONLY source of upnl; "Net P&L" is kept as net_pnl and never substituted for it.
  var POS_COLS = [["skip", /liq|margin|^realized|funding|^tp\s*\/\s*sl|^sl\s*\/\s*tp|\broe\b|^actions?$/i],
    ["value", /^(position )?value$|^notional/i], ["netpnl", /^net\s*p\s*&?\s*l/i],
    ["upnl", /^u\.?\s*p\s*&\s*l|u\.?\s*pnl|unreali[sz]ed|^p\s*&\s*l|^pnl/i], ["mark", /^mark(?!et)|^last|^current/i],
    ["entry", /entry|avg|average|open(ing)? price/i],
    ["sl", /^(sl|s\/l|stop)/i], ["tp", /^(tp|t\/p|take)/i], ["side", /^(side|direction|dir)$/i],
    ["qty", /^(size|qty|quantity|amount|position|contracts?)\b/i], ["lev", /^lev/i],
    ["symbol", /^(symbol|market|instrument|contract|asset|pair|coin|name)$/i], ["time", /time|date|opened/i]];
  // Closed orders: UNMEASURED layout (no live capture yet); the div-grid reader below is generic, and a miss posts
  // its digit-masked headers (closed_ prefix) exactly like Positions.
  var CLOSED_COLS = [["skip", /^actions?$|fee|reduce|^tp\s*\/\s*sl/i], ["netpnl", /^net\s*p\s*&?\s*l/i], ["pnl", /pnl|p\s*&\s*l|profit/i],
    ["value", /^(position |order )?value$|^notional/i],
    ["price", /avg|average|(fill(ed)?|clos(e|ed|ing)|exit|exec(uted)?)\s*price|^price$/i], ["trigger", /trigger|stop price/i],
    ["side", /^(side|direction|dir)$/i], ["qty", /^(size|qty|quantity|amount|filled)\b/i],
    ["type", /^(type|order type)$/i], ["status", /^status$/i], ["time", /time|date|closed|updated/i],
    ["symbol", /^(symbol|market|instrument|contract|asset|pair|coin|name)$/i]];
  var ROW_CAP = 15;   // phone_reconcile._MAX_ROWS (40) is above it
  function colKey(h, cols) { for (var i = 0; i < cols.length; i++) if (cols[i][1].test((h || "").trim())) return cols[i][0]; return null; }
  function firstNumber(s) {   // "1.1758 ETH" -> 1.1758; "+31.27 USD (1.2%)" -> 31.27; "−31.27" -> -31.27; none -> null
    var m = /([-+−]?)\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?/.exec((s || "").replace(/–/g, "-"));
    if (!m) return null; var v = parseFloat((m[2] + (m[3] || "")).replace(/,/g, "")); return (m[1] === "-" || m[1] === "−") ? -v : v;
  }
  // BREAKOUT-POS-UNPARSED (2026-10-14): innerText glues adjacent inline spans ("<span>Short</span><span>20x</span>" ->
  // "Short20x"), so \bshort\b missed a side word the operator can plainly read -- the "Positions1" badge failure again,
  // in a cell. wt() is the cell's text nodes joined WITH spaces; it is used ONLY to find words (side, symbol), never
  // numbers: a price whose digits sit in separate spans ("2,4<b>58</b>.24") must stay one number.
  function wt(el) {
    var out = [], w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null), n;
    while ((n = w.nextNode())) if (n.parentElement && vis(n.parentElement) && n.nodeValue.trim()) out.push(n.nodeValue.trim());
    return out.join(" ").replace(/\s+/g, " ").trim();
  }
  // BREAKOUT-POS-UNPARSED (1b.193 live dump 2026-10-14 21:38Z): the Positions Side cell has NO TEXT -- the side is
  // drawn as an icon / colour / attribute. attrText(el) is the words in the cell's own and descendants' attributes
  // (class, aria-label, title, alt, data-*, svg <title>, <use> href), camelCase / kebab / snake split into words.
  // Words only, never colour: "text-red" is not a side. Ambiguous (both a long and a short word) -> null.
  function attrEls(el) { return [el].concat(Array.prototype.slice.call(el.querySelectorAll("*"))).slice(0, 12); }
  function attrText(el) {
    var out = [];
    attrEls(el).forEach(function (e) {
      Array.prototype.slice.call(e.attributes || []).forEach(function (a) {
        if (/^(class|aria-label|title|alt|href|xlink:href)$|^data-/i.test(a.name)) out.push(a.value);
      });
      if (e.tagName && e.tagName.toLowerCase() === "title") out.push(e.textContent || "");
    });
    return out.join(" ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[^A-Za-z]+/g, " ").trim();
  }
  function sideOfAttr(s) {
    var l = /\b(long|buy)\b/i.test(s || ""), sh = /\b(short|sell)\b/i.test(s || "");
    return l === sh ? null : (l ? "long" : "short");
  }
  // the attribute SHAPE of a cell whose need-key read empty, for the miss dump: tag[.class tokens] aria/title/data
  function attrShape(el) {
    return attrEls(el).slice(0, 6).map(function (e) {
      var tag = (e.tagName || "").toLowerCase(), bits = [tag];
      Array.prototype.slice.call(e.attributes || []).forEach(function (a) {
        if (/^(class|aria-label|title|alt|href|xlink:href|role)$|^data-/i.test(a.name) && a.value) bits.push(a.name.replace(/^data-/, "d-") + "=" + a.value);
      });
      return bits.join(" ");
    });
  }
  function sideOf(s) { var m = /\b(long|short|buy|sell)\b/i.exec(s || ""); if (!m) return null; return /long|buy/i.test(m[1]) ? "long" : "short"; }
  function tabCount(re) {   // "Positions (2)" / "Positions 2" / "Positions1" -> 2/2/1; a bare label -> null (the page did not say)
    var c = all("button,[role=tab],[role=button],a").filter(function (x) { return tabMatch(re, t(x)); });
    if (c.length !== 1) return null;
    var tm = TAB_LABEL.exec(t(c[0])); if (tm) return tm[2] ? parseInt(tm[2].replace(/\D/g, ""), 10) : null;
    var m = /\(?\s*(\d+)\s*\)?\s*$/.exec(t(c[0]).replace(re, "")); return m ? parseInt(m[1], 10) : null;
  }
  function masked(hs) { return hs.map(function (h) { return h.replace(/\d/g, "#").slice(0, 24); }).slice(0, 20); }
  function controlsDump() { return all("button,[role=tab],[role=button]").map(function (x) { return t(x).replace(/\d/g, "#").slice(0, 30); }).filter(Boolean).slice(0, 30); }
  // one row's cells -> {symbol, side, qty, ...}; cells are aligned to keys (null / "skip" columns ignored)
  // `words` (optional): the same cells as wt() text, for the side and symbol words only; `attrs` (optional): the
  // cells' attrText(), for a side drawn without text
  function parseRow(cells, keys, words, attrs) {
    var o = {}, rowText = cells.join(" ") + " " + (words || []).join(" ");
    cells.forEach(function (c, i) {
      var k = keys[i], w = (words && words[i]) || c; if (!k || k === "skip" || o[k] !== undefined) return;
      if (k === "side") o.reduce = /reduce/i.test(c);   // "Long (Reduce)": the span after the side word; closes a SHORT
      o[k] = k === "symbol" ? w.slice(0, 40) : (k === "status" || k === "type" || k === "time") ? c.slice(0, 40) : (k === "side" ? (sideOf(c) || sideOf(w) || sideOfAttr(attrs && attrs[i])) : firstNumber(c));
    });
    if (o.side == null) o.side = sideOf(o.symbol) || sideOf(rowText);
    // a size carries the side only when it is SIGNED ("-1.5" short, "+1.5" long); an unsigned size is not a long
    var qc = keys.indexOf("qty") >= 0 ? (cells[keys.indexOf("qty")] || "").trim() : "";
    if (o.side == null && typeof o.qty === "number" && o.qty !== 0 && /^[-+−]/.test(qc)) o.side = o.qty > 0 ? "long" : "short";
    if (typeof o.qty === "number") o.qty = Math.abs(o.qty);
    // NO size column (Breakout's Positions grid shows Value, not contracts): qty = |Value| / Current price, marked
    // qty_src so the server and a reader know it is DERIVED (rounded Value, so only approximately the placed size)
    // a mark more than 2x away from the entry is a misread number (e.g. "2 462.10" read as 2), never a qty divisor
    var px = o.mark != null ? o.mark : o.price, pxOk = typeof o.entry !== "number" || !(o.entry > 0) || (px / o.entry > 0.5 && px / o.entry < 2);
    if (keys.indexOf("qty") < 0 && keys.indexOf("value") >= 0 && typeof o.value === "number" && typeof px === "number" && px > 0 && pxOk && o.value !== 0) {
      o.qty = Math.round(Math.abs(o.value) / (o.mark != null ? o.mark : o.price) * 1e8) / 1e8; o.qty_src = "value/mark";
    }
    // the live Market cell reads "ETH 5" (a leverage badge with no x, 1b.193 dump): a bare number token is never part
    // of a venue symbol, and left in it made the symbol unmappable ("ETH5")
    // the GROUP row's Market cell leads with a "⌄" expand glyph (operator screenshot 2026-10-14): glyphs are not symbol
    if (o.symbol) o.symbol = o.symbol.replace(/[^A-Za-z0-9 .\/\u00d7-]/g, " ").replace(/\b(long|short|buy|sell)\b/ig, "").replace(/\d+(\.\d+)?\s*[x×]|\b[x×]\s*\d+(\.\d+)?\b/ig, "")
      .replace(/(^|\s)\d+(\.\d+)?(?=\s|$)/g, " ").replace(/\s+/g, " ").trim();
    return o;
  }
  // every parsed row is sent, never a prefix: a row the server does not see would read as gone (review of #17745);
  // a strict list longer than the cap is refused instead of truncated. `emptyOk` = positive evidence the view is empty.
  // An unparsed miss says WHICH need keys were missing (detail, "+"-joined: the server scrub keeps "+" and "=") and the
  // first failing row's cells as key:shape with digits -> "d" (BREAKOUT-POS-UNPARSED: the first live miss said only
  // "unparsed", so the failing field had to be guessed).
  function cellShapes(cells, keys) {
    return (cells || []).slice(0, 20).map(function (c, i) {
      return ((keys[i] || "none") + ":" + c.replace(/\d/g, "d").replace(/,/g, "_").replace(/[^A-Za-z0-9 _.:\/()=+&%-]/g, "=")).slice(0, 40);
    });
  }
  function finishRead(out, bad, hs, count, need, strict, emptyOk, raw, keys, els) {
    if (strict && out.length > ROW_CAP) return {state: "too_many_rows", n_rows: out.length, count: count, headers: masked(hs)};
    var rows = out.slice(0, ROW_CAP);
    var miss = function (o) { return need.filter(function (k) { return o[k] == null || o[k] === ""; }); };
    var fi = -1; out.forEach(function (o, i) { if (fi < 0 && miss(o).length) fi = i; });
    if (bad || fi >= 0) {
      var ms = []; out.forEach(function (o) { miss(o).forEach(function (k) { if (ms.indexOf(k) < 0) ms.push(k); }); });
      var cs = fi >= 0 && raw ? cellShapes(raw[fi], keys || []) : [];
      // a need-key cell that read EMPTY: its attribute shape too (digits masked), so the next miss shows how it is drawn
      if (fi >= 0 && els && els[fi]) miss(out[fi]).forEach(function (k) {
        var i = (keys || []).indexOf(k); if (i < 0 || !els[fi][i] || (raw[fi][i] || "").trim()) return;
        attrShape(els[fi][i]).forEach(function (a) {
          for (var j = 0; j < a.length && cs.length < 36; j += 36) cs.push((k + "+" + a.slice(j, j + 36)).replace(/\d/g, "d").replace(/[^A-Za-z0-9 _.:\/()=+&%-]/g, "=").slice(0, 40));
        });
      });
      // SIDE ONLY: every row has symbol + qty and the venue drew the side without any word we can read. The rows go
      // to the server as "side_unread" (never "read"): it may take the side from the ONE open journal ticket with the
      // same symbol and entry (ESTIMATED), or leave the read un-reconciled. Never guessed here.
      var sideOnly = !bad && ms.length === 1 && ms[0] === "side" && need.indexOf("side") >= 0 && (count === null || count === out.length);
      var r0 = {state: sideOnly ? "side_unread" : "unparsed", n_rows: out.length + bad, count: count, headers: masked(hs),
        detail: ("miss=" + (ms.join("+") || "none") + " bad=" + bad + " n=" + out.length).slice(0, 31), cells: cs};
      if (sideOnly) r0.rows = rows;
      return r0;
    }
    if (count !== null && count !== out.length) return {state: "count_mismatch", n_rows: out.length, count: count, headers: masked(hs)};
    // flat needs POSITIVE evidence (the tab says 0, or the table's / panel's container says "no positions"): a header
    // row with no data rows may be a table still rendering, and a false "flat" would close live journal rows.
    if (!out.length && count !== 0 && !emptyOk) return {state: "empty_unconfirmed", n_rows: 0, count: count, headers: masked(hs)};
    return {state: "read", rows: rows, n_rows: out.length, count: count, headers: masked(hs)};
  }
  // ---- DIV GRID (PHONE-POSITIONS-DIV, 2026-10-12): the live Positions view has NO <table>/<tr>/[role=row]. Headers are
  // plain elements (they surface as controls: Market, UP&L, Side, Value, Opening price, Current price, TP / SL,
  // Net P&L). The HEADER ROW is the smallest ancestor holding >= 4 distinct mapped labels incl. side and an entry /
  // mark column; its children are the columns, in document order. DATA ROWS are the following siblings of that header
  // (or of its wrapper, up to 3 levels, or those siblings' children) with the SAME child count. The PANEL is the
  // highest ancestor of the header that holds no tab label (Positions / Open orders / Closed orders / ...): the empty
  // state counts only INSIDE it, and a panel that reaches <body> proves nothing.
  var TAB_LABEL = /^(open orders|positions|portfolio|closed orders|trades)(\s*\(?\s*\d+\s*\)?)?$/i;
  // BREAKOUT-UNTRACKED-1014 (2026-10-14): with a position open the chip renders its count badge GLUED to the label
  // (innerText "Positions1", "Open orders2"), so /^positions\b/ and /^positions$/ missed it and every read was
  // no_tab for 9 h while an ETH short was open. A tab chip's text is matched with its count badge dropped too.
  function tabText(s) { var m = TAB_LABEL.exec(s); return m ? m[1] : s; }
  function tabMatch(rx, s) { return rx.test(s) || rx.test(tabText(s)); }
  function kids(e) { return Array.prototype.slice.call(e.children).filter(vis); }
  function gridHeaders(cols) {
    var labels = innermost(all("div,span,p,button,[role=button],[role=columnheader],th,li,a,label")).filter(function (e) {
      var s = t(e); return s && s.length <= 24 && colKey(s, cols);
    }), found = [];
    labels.forEach(function (l) {
      for (var a = l.parentElement, d = 0; a && d < 6 && a !== document.body; a = a.parentElement, d++) {
        var ks = {}; labels.forEach(function (m) { if (a.contains(m)) ks[colKey(t(m), cols)] = 1; });
        var okShape = cols === CLOSED_COLS ? (ks.side && ks.symbol && (ks.type || ks.status) && (ks.price || ks.qty || ks.pnl || ks.status)) : (ks.side && (ks.entry || ks.mark));
        if (Object.keys(ks).length >= 4 && okShape) { if (found.indexOf(a) < 0) found.push(a); return; }
      }
    });
    return innermost(found);
  }
  function leafTabs() {
    return all("button,[role=tab],[role=button],a,div,span,li").filter(function (x) { return TAB_LABEL.test(t(x)); })
      .filter(function (x, i, a) { return !a.some(function (y) { return y !== x && x.contains(y); }); });
  }
  function gridPanel(h) {
    var tabs = all("button,[role=tab],[role=button],a,div,span,li").filter(function (x) { return TAB_LABEL.test(t(x)); });
    var p = h;
    while (p.parentElement && p.parentElement !== document.body && p.parentElement !== document.documentElement &&
           !tabs.some(function (x) { return p.parentElement.contains(x); })) p = p.parentElement;
    return (p === document.body || p === document.documentElement || tabs.some(function (x) { return p.contains(x); })) ? null : p;
  }
  // a div-grid header's DATA ROWS: the following siblings of the header (or of its wrapper, up to 3 levels, or those
  // siblings' children) with the SAME child count; `direct` / `sib` are kept for readGrid's misaligned-row count
  function gridRowsOf(h) {
    var hc = kids(h), node = h, sib;
    for (var lvl = 0; lvl < 4 && node.parentElement; lvl++, node = node.parentElement) {
      sib = kids(node.parentElement).filter(function (x) { return x !== node; });
      var direct = sib.filter(function (x) { return kids(x).length === hc.length; });
      var nested = []; sib.forEach(function (x) { if (direct.indexOf(x) < 0) kids(x).forEach(function (y) { if (kids(y).length === hc.length) nested.push(y); }); });
      var pool = direct.length ? direct : nested;
      if (pool.length) return {rows: pool, direct: direct, sib: sib};
    }
    return {rows: [], direct: [], sib: []};
  }
  // BREAKOUT-SL-AMEND-CLICK (operator screenshot 2026-10-14 ~21:41Z): Positions groups rows per symbol. The GROUP row
  // ("⌄ ETH 1  Sum ⌄  20.88 USD") carries "Sum" aggregates and a position count; the CHILD row ("ETH 5x  20.88 USD") is
  // the position. A row with a "Sum" cell is a group row: its children are read instead -- any element in the panel
  // with the header's cell count that is not a group row and holds no other such element. With no child row in the DOM
  // (collapsed), a group row whose count is 1 stands in for its one position (row_kind "group"); a group of more than
  // one position is never read as one row (unparsed, miss=group).
  function isGroupRow(r) { return kids(r).some(function (c) { return /^\s*sum\b/i.test(t(c)); }); }
  function groupCount(r) { var c = kids(r)[0], m = c ? /(\d+)\s*$/.exec(t(c)) : null; return m && !/[x\u00d7]\s*$/i.test(t(c)) ? parseInt(m[1], 10) : null; }
  function childRows(h, groups) {
    var n = kids(h).length, panel = gridPanel(h) || h.parentElement;
    var c = Array.prototype.slice.call(panel.querySelectorAll("*")).filter(function (x) {
      return vis(x) && x !== h && !x.contains(h) && !h.contains(x) && kids(x).length === n && !isGroupRow(x) && /\d/.test(t(x));
    });
    c = c.filter(function (x) { return !c.some(function (o) { return o !== x && x.contains(o); }); });
    return groups.length ? c : [];
  }
  function readGrid(cols, need, emptyRe, count, strict, hdrs) {
    var h = hdrs[0], hs = kids(h).map(t), keys = hs.map(function (s) { return colKey(s, cols); });
    var gr = gridRowsOf(h), rows = gr.rows, bad = 0, kinds = null;
    var groups = rows.filter(isGroupRow);
    if (groups.length) {
      var ch = childRows(h, groups);
      if (ch.length) { rows = ch; kinds = rows.map(function () { return "child"; }); }
      else if (groups.every(function (g) { return groupCount(g) === 1; })) kinds = rows.map(function (r) { return isGroupRow(r) ? "group" : "child"; });
      else return {state: "unparsed", n_rows: rows.length, count: count, headers: masked(hs), detail: "miss=group bad=0 n=" + rows.length, layout: "div_grid", cells: []};
    }
    // anything else in the same container that looks like a row but has another cell count is a row we cannot align
    if (gr.direct.length) gr.sib.forEach(function (x) {
      if (gr.direct.indexOf(x) >= 0 || emptyRe.test(t(x)) || !/\d/.test(t(x)) || rows.some(function (r) { return x.contains(r); })) return;
      if (kids(x).length >= 2) bad += 1;
    });
    var raw = rows.map(function (r) { return kids(r).map(t); });
    var els = rows.map(kids);
    var out = rows.map(function (r, i) { var o = parseRow(raw[i], keys, els[i].map(wt), els[i].map(attrText)); if (kinds) o.row_kind = kinds[i]; return o; });
    var panel = gridPanel(h), emptyOk = !!(panel && emptyRe.test(t(panel)));
    var r = finishRead(out, bad, hs, count, need, strict, emptyOk, raw, keys, els);
    r.layout = "div_grid";
    return r;
  }
  function readTable(cols, need, emptyRe, tabRe, strict) {   // need: the keys every row must carry; a table is ours when it has a symbol column and a qty/price/entry column
    var hits = [];
    all("table,[role=table],[role=grid]").forEach(function (tb) {
      var hs = Array.prototype.slice.call(tb.querySelectorAll("th,[role=columnheader]")).map(t);
      var keys = hs.map(function (h) { return colKey(h, cols); });
      if (keys.indexOf("symbol") >= 0 && keys.some(function (k) { return k === "qty" || k === "price" || k === "entry"; })) hits.push({tb: tb, hs: hs, keys: keys});
    });
    var count = tabCount(tabRe);
    if (hits.length === 0) {
      var g = gridHeaders(cols);
      if (g.length === 1) return readGrid(cols, need, emptyRe, count, strict, g);
      if (g.length > 1) return {state: "ambiguous", tables: g.length, count: count, headers: masked(g[0] ? kids(g[0]).map(t) : []), controls: controlsDump()};
    }
    if (hits.length !== 1) {
      // No table: flat ONLY on the panel's own evidence -- the tab's count says 0, or the ONE visible tab panel says
      // "no positions". Page-wide text is never enough (review of #17745: a "no positions" string elsewhere would
      // read as flat and close every open journal row).
      var panels = all("[role=tabpanel]"), panelEmpty = panels.length === 1 && emptyRe.test(t(panels[0]));
      if (hits.length === 0 && (count === 0 || (count === null && panelEmpty))) return {state: "read", rows: [], count: count, headers: []};
      return {state: hits.length ? "ambiguous" : "no_table", tables: hits.length, count: count,
        headers: hits.length ? masked(hits[0].hs) : [], controls: controlsDump()};
    }
    var h = hits[0], out = [], bad = 0, raw = [], rels = [];
    innermost(Array.prototype.slice.call(h.tb.querySelectorAll("tr,[role=row]")).filter(vis)).forEach(function (r) {
      if (r.querySelector("th,[role=columnheader]")) return;
      var els = Array.prototype.slice.call(r.querySelectorAll("td,[role=cell],[role=gridcell]")), cells = els.map(t);
      if (cells.length === 1 && emptyRe.test(cells[0])) return;   // the table's own "no positions" row
      if (cells.length !== h.hs.length) { bad += 1; return; }
      raw.push(cells); rels.push(els); out.push(parseRow(cells, h.keys, els.map(wt), els.map(attrText)));
    });
    return finishRead(out, bad, h.hs, count, need, strict, emptyRe.test(t(h.tb.parentElement || h.tb)), raw, h.keys, rels);
  }
  function dialogs() { return all("[role=dialog],[role=alertdialog],dialog[open]"); }
  function dlg() { var d = dialogs(); return d.length === 1 ? d[0] : null; }
  function dlgByLabel(re, prefer) {
    var d = dlg(); if (!d) return {list: [], hits: []};
    var rx = new RegExp(re, "i"), px = prefer ? new RegExp(prefer, "i") : null, list = inputs(d);
    var hits = list.filter(function (i) { return rx.test(labelFor(i)); });
    if (hits.length > 1 && px) hits = hits.filter(function (i) { return px.test(labelFor(i)); });
    return {list: list, hits: hits};
  }
  function dlgButtons(re, notRe) {
    var d = dlg(); if (!d) return [];
    var rx = new RegExp(re, "i");
    return Array.prototype.slice.call(d.querySelectorAll("button,[role=button]")).filter(function (x) {
      var s = ctlText(x); return vis(x) && rx.test(s) && !(notRe && notRe.test(s));
    });
  }

  // login lineup helpers (PHONE-AUTOLOGIN-3, 2026-10-10: the live page read page_n=0 for 90 s after the email click on
  // portal.breakoutprop.com, so the reader is broadened and every miss is described). A candidate is the innermost
  // element whose text, with ALL whitespace and zero-width characters removed, is exactly a 1-3 digit number (an icon
  // child or split digit spans no longer hide it), or a radio / button / option whose aria-label or value is one.
  // Searched in the document, open shadow roots and same-origin iframes. Matching stays fail-closed (loginPick).
  var ZW = /[\s\u200B-\u200D\u2060\uFEFF\u00A0]+/g;
  function nt(el) { return ((el && (el.innerText || el.textContent)) || "").replace(ZW, ""); }
  function gcs(e) { return (e.ownerDocument.defaultView || window).getComputedStyle(e); }
  // every tree we can read: the document, open shadow roots, same-origin iframes (3 levels); xo = unreadable frames
  function roots() {
    var out = [], st = {shadow: 0, frames: 0, xo: 0};
    (function walk(r, depth) {
      out.push(r);
      var els = r.querySelectorAll("*");
      for (var i = 0; i < els.length; i++) {
        var e = els[i];
        if (e.shadowRoot) { st.shadow++; walk(e.shadowRoot, depth); }
        if (/^I?FRAME$/.test(e.tagName)) {
          st.frames++;
          var d = null; try { d = e.contentDocument; } catch (x) { }
          if (d && d.documentElement && depth < 3) walk(d, depth + 1); else st.xo++;
        }
      }
    })(document, 0);
    out.st = st; return out;
  }
  function deep(sel) {
    var a = []; roots().forEach(function (r) { a = a.concat(Array.prototype.slice.call(r.querySelectorAll(sel))); }); return a;
  }
  function up(n) { return n.parentElement || (n.parentNode && n.parentNode.host) || null; }
  function ctl(n) {
    return /^(BUTTON|A|INPUT|OPTION|LABEL)$/.test(n.tagName) || /^(button|radio|option|checkbox|link)$/.test(n.getAttribute("role") || "") ||
      n.hasAttribute("onclick");
  }
  var SKIP = /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|HTML|HEAD|BODY|svg|SVG|PATH|path)$/;
  function numCands() {
    var c = [];
    deep("*").forEach(function (e) {
      if (SKIP.test(e.tagName) || !vis(e)) return;
      var v = nt(e);
      if (/^\d{1,3}$/.test(v)) {
        // the innermost element carrying the whole number: a child with the same text represents it
        for (var i = 0; i < e.children.length; i++) if (nt(e.children[i]) === v) return;
        c.push({e: e, v: v});
        return;
      }
      if (v) return;   // has other text: never read an attribute behind visible words
      var a = (e.getAttribute("aria-label") || "").replace(ZW, "");
      var val = (/^(radio|button|submit)$/.test(e.type || "") || /^(BUTTON|OPTION)$/.test(e.tagName)) ? String(e.value || "").replace(ZW, "") : "";
      if (/^\d{1,3}$/.test(a)) c.push({e: e, v: a}); else if (/^\d{1,3}$/.test(val)) c.push({e: e, v: val});
    });
    // nested candidates (split digits "4" "2" inside "42"; or two choices "1" "2" inside a row reading "12"): a
    // control inside wins over its container, else the container (the whole number) wins over its pieces
    return c.filter(function (x) {
      for (var i = 0; i < c.length; i++) {
        var y = c[i]; if (y === x) continue;
        if (y.e !== x.e && y.e.contains(x.e) && !ctl(x.e) && !hasCtlInside(y, c)) return false;   // a piece of y
        if (y.e !== x.e && x.e.contains(y.e) && hasCtlInside(x, c)) return false;                  // a row of controls
      }
      return true;
    });
  }
  function hasCtlInside(x, c) { return c.some(function (y) { return y !== x && x.e.contains(y.e) && ctl(y.e); }); }
  function clickable(e) {
    for (var n = e, d = 0; n && d < 5; n = up(n), d++) {
      if (!/^\D*\d{1,3}\D*$/.test(t(n)) && !(n === e && nt(n) === "")) return null;   // a container holding SEVERAL numbers is never "the" choice
      if (n.tagName === "LABEL" && n.control) return n.control;
      if (/^(BUTTON|A|INPUT|OPTION)$/.test(n.tagName) || /^(button|radio|option|checkbox)$/.test(n.getAttribute("role") || "") ||
          n.hasAttribute("onclick") || (gcs(n).cursor === "pointer" &&
          !(up(n) && up(n).nodeType === 1 && gcs(up(n)).cursor === "pointer"))) return n;   // cursor inherits: only where it is SET
    }
    return null;
  }
  // scrubbed text for the diag: emails, links and EVERY digit removed, 40 chars
  function scrub(s) {
    return (s || "").replace(/\S+@\S+/g, "EML").replace(/(https?:\/\/|www\.)\S*/gi, "URL").replace(/\d+/g, "N")
      .replace(/[^A-Za-z .:\/+=_-]/g, " ").replace(/\s+/g, " ").trim().slice(0, 40);
  }
  function deepText() {
    var parts = [];
    roots().forEach(function (r) {
      if (r.body) parts.push(r.body.innerText || "");
      else Array.prototype.forEach.call(r.children || [], function (k) { if (!SKIP.test(k.tagName)) parts.push(k.innerText || k.textContent || ""); });
    });
    return parts.join("\n");
  }
  function sig(s) { var h = 5381; for (var i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0; return h.toString(36); }

  // ---- POSITION SHEET (BREAKOUT-SL-AMEND-CLICK, 2026-10-14). The flow is operator-MEASURED from screenshots (~21:41Z,
  // phone width); its DOM is NOT measured yet, so every helper finds things by visible text / attribute words, demands
  // exactly ONE match and otherwise returns a digit-masked dump. Tapping the Positions row opens a bottom sheet titled
  // with the symbol ("ETH"), subtitle "Position – <id>", tabs [Position details] [Opening trades] [1 TP / 1 SL].
  // Position details: Side "Short", "Total open volume" 0.54 ETH, "Avg. opening price", "Current price", "uPnL", and a
  // RED "Close position" button. [1 TP / 1 SL]: SIDE | TYPE | TRIGGER PRICE rows ("Long | Stop l… | 2,504.25 USD | ✎ |
  // ⓧ"): that SIDE is the CLOSING order's side, never the position's. ✎ opens "Edit Stop loss"; ⓧ CANCELS the order.
  // DENYLIST: every click goes through safeClick, which refuses "Close position", any control whose words say
  // close / cancel / remove / delete / flatten / reverse, and the ⓧ ✕ × glyph icons (a Positions row's ✕ closes the
  // position; an order row's ⓧ cancels the stop). The two dismissals (the modal's "Cancel" text button, the sheet's
  // header ✕) are separate, narrower paths: dlgDismiss / sheetCloseX.
  var DENY_WORDS = /close|cancel|remove|delete|flatten|reverse|liquidat|discard/i;
  var DENY_GLYPH = /^[✕✖✗✘×ⓧ⊗⊘❌❎⨯☒xX]$/;
  var PENCIL_GLYPH = /^[✎✏✐\u{1f589}\u{1f58a}\u{1f58b}]$/u;
  var SHEET_TAB = {details: /^position details$/i, trades: /^opening trades$/i, tpsl: /^\d+\s*tp\s*\/\s*\d+\s*sl$/i};
  var SUBTITLE_RE = /^position\s*[–—-]\s*\S+/i;
  function ctlWords(x) {   // a control's own words: text, aria-label, title, test id / icon names, svg <title>, <use> href
    var s = [t(x)];
    [x].concat(Array.prototype.slice.call(x.querySelectorAll("*")).slice(0, 12)).forEach(function (e) {
      ["aria-label", "title", "data-testid", "data-icon", "data-id", "name", "alt", "href", "xlink:href"].forEach(function (a) { var v = e.getAttribute(a); if (v) s.push(v); });
      if (e.tagName && e.tagName.toLowerCase() === "title") s.push(e.textContent || "");
    });
    return s.join(" ") + " " + attrText(x);
  }
  // why a control must never be clicked, or null. Words are matched in text AND attributes (class names included), so
  // a false refusal ("closeable") is possible and safe; a missed ✕ is not.
  function denied(x) {
    if (!x || !x.getAttribute) return "none";
    var txt = t(x);
    if (/close\s*position/i.test(txt)) return "deny_close_position";
    if (DENY_GLYPH.test(txt) || DENY_GLYPH.test((x.getAttribute("aria-label") || "").trim())) return "deny_glyph";
    if (DENY_WORDS.test(ctlWords(x))) return "deny_word";
    return null;
  }
  function safeClick(x) { var d = denied(x); if (d) return d; x.click(); return "clicked"; }
  // a touch-style tap at the element's centre: pointer + mouse down/up, then click (BREAKOUT-SHEET-PROBE)
  function safeTap(x) {
    var d = denied(x); if (d) return d;
    var r = x.getBoundingClientRect(), o = {bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0};
    var P = typeof PointerEvent === "function" ? PointerEvent : MouseEvent;
    x.dispatchEvent(new P("pointerdown", Object.assign({pointerType: "touch", isPrimary: true, pointerId: 1}, o)));
    x.dispatchEvent(new MouseEvent("mousedown", o));
    x.dispatchEvent(new P("pointerup", Object.assign({pointerType: "touch", isPrimary: true, pointerId: 1}, o)));
    x.dispatchEvent(new MouseEvent("mouseup", o));
    x.dispatchEvent(new MouseEvent("click", o));
    return "tapped";
  }
  function textEls(root, re, max) {   // innermost visible elements whose own text matches re
    return innermost(Array.prototype.slice.call((root || document).querySelectorAll("*")).filter(function (e) {
      if (!vis(e)) return false; var s = t(e); return s.length > 0 && s.length <= (max || 40) && re.test(s);
    }));
  }
  // the ONE Positions row cell to tap for `sym`: which = "child" -> the position row's Market cell ("ETH 5x", a
  // leverage badge); "group" -> the group row's ("ETH 1", the symbol + a count). Only inside the Positions grid panel.
  // The element, or why not: no_grid | no_panel | none | rows_<n>
  function sheetHit(sym, which) {
    var g = gridHeaders(POS_COLS); if (g.length !== 1) return "no_grid";
    var panel = gridPanel(g[0]); if (!panel) return "no_panel";
    var b = base(sym).replace(/[^A-Z0-9]/g, ""), sfx = "(USD[TC]?|PERP)?";
    var re = which === "group" ? new RegExp("^[\\u2304\\u2335\\u25be\\u25bc\\u02c5v]?\\s*" + b + sfx + "\\s*\\d+$", "i")
      : new RegExp("^" + b + sfx + "\\s*\\d+(\\.\\d+)?\\s*[x\\u00d7]$", "i");
    var hits = textEls(panel, re, 24).filter(function (e) { return !g[0].contains(e); });
    if (!hits.length) return "none"; if (hits.length > 1) return "rows_" + hits.length;
    return hits[0];
  }
  function sheetRoots() {   // the smallest ancestor of a [Position details] tab that also holds the [n TP / n SL] tab and the subtitle
    var found = [];
    textEls(document, SHEET_TAB.details).forEach(function (d) {
      for (var a = d.parentElement, k = 0; a && k < 12 && a !== document.body; a = a.parentElement, k++) {
        if (textEls(a, SHEET_TAB.tpsl).length && textEls(a, SUBTITLE_RE, 60).length) { if (found.indexOf(a) < 0) found.push(a); return; }
      }
    });
    return innermost(found);
  }
  function sheetRoot() { var r = sheetRoots(); return r.length === 1 ? r[0] : null; }
  function before(a, b) { return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING); }
  // the sheet's value for a row label ("Side" -> "Short"): the label's nearest ancestor whose text is the label + value
  // and that holds no other details label
  var DETAIL_LABELS = {side: /^side$/i, qty: /^total open volume$/i, entry: /^avg\.?\s*opening price$/i, mark: /^current price$/i,
    upnl: /^u\.?\s*p\s*n\s*l$|^u\.?\s*p\s*&\s*l$|^unreali[sz]ed/i, margin: /^initial margin$/i};
  function detailValue(root, re) {
    var ls = textEls(root, re); if (ls.length !== 1) return {n: ls.length};
    var lab = t(ls[0]), all_ = Object.keys(DETAIL_LABELS).map(function (k) { return DETAIL_LABELS[k]; });
    for (var a = ls[0].parentElement, k = 0; a && k < 3 && a !== root; a = a.parentElement, k++) {
      var others = all_.filter(function (rx) { return rx !== re && textEls(a, rx).length; });
      if (others.length) return {n: 1, value: null, why: "shared_parent"};
      var s = t(a); if (s.length > lab.length && s.toLowerCase().indexOf(lab.toLowerCase()) === 0) return {n: 1, value: s.slice(lab.length).trim(), el: a};
    }
    return {n: 1, value: null, why: "no_value"};
  }
  var PRICE_LEAF = /^[-+−]?\s*\d[\d,]*(\.\d+)?\s*(USD[TC]?)?$/i;
  // the [n TP / n SL] tab's order rows: each TYPE cell ("Stop l…" / "Take p…") and its smallest ancestor holding ONE
  // price and no other TYPE cell
  function sheetOrderRows(root) {
    var types = textEls(root, /^(stop|take)\b/i, 30), out = [];
    types.forEach(function (ty) {
      for (var a = ty.parentElement, k = 0; a && k < 5 && a !== root; a = a.parentElement, k++) {
        if (types.some(function (o) { return o !== ty && a.contains(o); })) break;
        var px = textEls(a, PRICE_LEAF);
        if (px.length) { out.push({type: /^stop/i.test(t(ty)) ? "stop" : "take", el: a, typeEl: ty, px: px}); return; }
      }
      out.push({type: /^stop/i.test(t(ty)) ? "stop" : "take", el: null, typeEl: ty, px: []});
    });
    return out;
  }
  // a row's icon-like controls, outermost interactive element each: button / role=button / a / [tabindex] / svg / img /
  // a one-glyph text element
  function rowIcons(row, skip) {
    var c = Array.prototype.slice.call(row.querySelectorAll("button,[role=button],a,[tabindex],svg,img,i,span,div")).filter(function (x) {
      if (!vis(x) || skip.some(function (s) { return s === x || s.contains(x) || x.contains(s); })) return false;
      var tag = x.tagName.toLowerCase();
      return /^(button|a|svg|img|i)$/.test(tag) || x.getAttribute("role") === "button" || x.hasAttribute("tabindex") || (t(x).length === 1 && !x.children.length);
    }).map(function (x) {
      var o = x.closest("button,[role=button],a,[tabindex]");
      return (!o || o === row || !row.contains(o) || skip.some(function (s) { return o.contains(s); })) ? x : o;
    });
    var u = c.filter(function (x, i) { return c.indexOf(x) === i; });
    return u.filter(function (x) { return !u.some(function (o) { return o !== x && o.contains(x); }); });
  }
  function isPencil(x) { var s = t(x); return (PENCIL_GLYPH.test(s) || /\b(edit|pencil|modify)\b/i.test(ctlWords(x))) && !denied(x); }
  function iconDump(x) {
    var m = function (s) { return (s || "").replace(/\d/g, "#"); };
    return {tag: x.tagName.toLowerCase(), role: x.getAttribute("role") || "", text: m(t(x)).slice(0, 8),
      label: m(x.getAttribute("aria-label") || x.getAttribute("title") || "").slice(0, 24), testid: m(x.getAttribute("data-testid") || "").slice(0, 24),
      words: m(attrText(x)).slice(0, 40), pencil: isPencil(x), deny: denied(x) || ""};
  }
  function iconLines(d) {
    return d.map(function (c) { return (c.tag + (c.role ? "/" + c.role : "") + " " + (c.pencil ? "P " : "") + (c.deny ? "D " : "") + (c.text || c.label || c.testid || c.words)).slice(0, 40); });
  }
  // the edit modal: the ONE "Edit Stop loss" / "Edit Take profit" title and its smallest ancestor holding a Confirm and
  // a Cancel button
  function modalRoot() {
    var ts = textEls(document, /^edit\s+(stop\s*loss|take\s*profit)$/i), found = [];
    ts.forEach(function (ti) {
      for (var a = ti.parentElement, k = 0; a && k < 10 && a !== document.body; a = a.parentElement, k++) {
        var bs = Array.prototype.slice.call(a.querySelectorAll("button,[role=button]")).filter(vis);
        if (bs.some(function (b) { return /^confirm$/i.test(t(b)); }) && bs.some(function (b) { return /^cancel$/i.test(t(b)); })) { found.push({root: a, title: ti}); return; }
      }
    });
    return found;
  }
  var MODAL_HDR = /^(long|short)\s+(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z]{2,8})?\s*@\s*(\d[\d,]*(?:\.\d+)?)\s*(USD[TC]?)?$/i;
  function modalInputs(root) { return inputs(root).map(function (i) { return {label: labelFor(i).replace(/\s+/g, " ").trim().slice(0, 40), value: i.value}; }); }
  function triggerInput(root) { return inputs(root).filter(function (i) { var l = labelFor(i); return /trigger/i.test(l) && !/distance|%|r\s*:\s*r|quantity/i.test(l); }); }
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
    // PHONE-AUTOLOGIN-3: the continue control is looked for in the email field's own form first, a social / passkey
    // button ("Continue with Google") is never the one, and window.__exSubmit records which kind was used (scrubbed).
    loginEmail: function (email) {
      if (!hostOk()) return "bad_host";
      var el = deep("input[type=email],input[autocomplete=email],input[name*=mail i]").filter(vis)[0];
      if (!el) return "no_email_field";
      el.focus(); setVal(el, email);
      if (el.value !== email) return "not_set";
      var lab = function (b) { return t(b) + " " + (b.getAttribute("aria-label") || "") + " " + (b.value || ""); };
      var ok = function (b) {
        var s = lab(b);
        return /continue|log ?in|sign ?in|next|send|submit|get (a |the )?(code|link)|verify/i.test(s) &&
          !/google|apple|microsoft|facebook|github|passkey|sso|discord|wallet/i.test(s) && !(b.disabled || b.getAttribute("aria-disabled") === "true");
      };
      var scope = el.form || el.closest("form,[role=dialog],main") || document;
      var sel = "button,input[type=submit],[role=button]";
      var inForm = Array.prototype.slice.call(scope.querySelectorAll(sel)).filter(vis);
      var b = inForm.filter(function (x) { return x.type === "submit" && ok(x); })[0] || inForm.filter(ok)[0];
      var where = "form";
      if (!b) { b = deep(sel).filter(vis).filter(ok)[0]; where = "page"; }
      window.__exSubmit = {where: b ? where : "none", label: b ? scrub(lab(b)) : "", buttons: inForm.length};
      if (b) { b.click(); return "clicked"; }
      if (el.form && el.form.requestSubmit) { el.form.requestSubmit(); return "submitted"; }
      return "no_button";
    },
    // The email submit did not move the page: press Enter in the email field (keydown/keypress/keyup), then submit
    // its form. The value is not touched.
    loginEnter: function () {
      if (!hostOk()) return "bad_host";
      var el = deep("input[type=email],input[autocomplete=email],input[name*=mail i]").filter(vis)[0];
      if (!el) return "no_email_field";
      el.focus();
      ["keydown", "keypress", "keyup"].forEach(function (k) {
        el.dispatchEvent(new KeyboardEvent(k, {key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true}));
      });
      if (el.form && el.form.requestSubmit) { try { el.form.requestSubmit(); return "enter+submit"; } catch (e) { } }
      return "enter";
    },
    // What the submit recorded (scrubbed: no digits, no email, no link).
    loginSubmitInfo: function () { return window.__exSubmit || {where: "unread", label: "", buttons: 0}; },
    // A scrubbed STRUCTURE snapshot of whatever page is in front of us, for the login_diag (never a value: numbers
    // are COUNTED, every digit in a text becomes "N", emails and links are dropped). sig = a hash of the readable
    // text, compared before/after the email submit ("did the page change").
    loginProbe: function () {
      var rs = roots(), tags = {}, inputs = {}, texts = [];
      rs.forEach(function (r) {
        Array.prototype.forEach.call(r.querySelectorAll("*"), function (e) {
          var k = e.tagName.toLowerCase(); tags[k] = (tags[k] || 0) + 1;
          if (k === "input" && vis(e)) inputs[e.type || "text"] = (inputs[e.type || "text"] || 0) + 1;
        });
      });
      var top = Object.keys(tags).sort(function (a, b) { return tags[b] - tags[a]; }).slice(0, 15).map(function (k) { return k + "." + tags[k]; });
      deep("button,[role=button],a,h1,h2,h3,h4,h5,h6,[role=heading],label,legend,[role=alert],[role=status]").filter(vis).forEach(function (e) {
        var s = scrub(t(e) || e.getAttribute("aria-label") || ""); if (s && texts.indexOf(s) < 0 && texts.length < 20) texts.push(s);
      });
      var body = deepText();
      var ph = {check_email: /check your (e-?mail|inbox)/i, we_sent: /we('ve| have)? sent|sent (you )?(an? )?(e-?mail|link|code)/i,
        select: /\bselect/i, choose: /\bchoose/i, verify: /verif/i, number: /\bnumber/i, code: /\bcode\b/i, link: /\blink\b/i,
        tap: /\b(tap|click)\b/i, expired: /expir/i, error: /error|invalid|wrong|try again|too many/i, social: /google|apple|passkey/i};
      var hits = Object.keys(ph).filter(function (k) { return ph[k].test(body); });
      return {tags: top, shadow: rs.st.shadow, frames: rs.st.frames, frames_xo: rs.st.xo, inputs: inputs, texts: texts,
        phrases: hits, numtok: (body.match(/(^|[^\d])\d{1,3}(?![\d])/g) || []).length, digits: (body.match(/\d+/g) || []).length,
        cands: numCands().length, chars: body.length, sig: sig(body), host: location.hostname};
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
    // LOGIN step 2, LINEUP form (PHONE-AUTOLOGIN-2, operator 2026-10-10: "a login check that verifies you can choose
    // the correct 2 digit number from a line up of 3"). The VALUES go to the app's memory only (never logged,
    // reported or stored); the app reports counts. top = distinct numbers at the largest font size; btn = distinct
    // numbers that sit in a clickable control (the lineup to tap); n = every visible 1-3 digit leaf.
    loginChoices: function () {
      var c = numCands();
      if (!c.length) return {n: 0, top: [], btn: []};
      var fs = function (x) { return parseFloat(gcs(x.e).fontSize) || 0; };
      var max = Math.max.apply(null, c.map(fs));
      var uniq = function (a) { return a.filter(function (x, i) { return a.indexOf(x) === i; }); };
      var v = function (x) { return x.v; };
      return {n: c.length, top: uniq(c.filter(function (x) { return fs(x) === max; }).map(v)),
        btn: uniq(c.filter(function (x) { return !!clickable(x.e); }).map(v))};
    },
    // Tap the ONE lineup entry whose text is exactly [n]. More than one distinct control for it, or none: nothing is
    // tapped ("ambiguous" / "none"), the app fails closed.
    loginPick: function (n) {
      if (!hostOk()) return "bad_host";
      var hits = numCands().filter(function (x) { return x.v === String(n); }).map(function (x) { return clickable(x.e) || x.e; });
      hits = hits.filter(function (x, i) { return hits.indexOf(x) === i; });
      if (hits.length !== 1) return hits.length ? "ambiguous" : "none";
      var el = hits[0];
      ["pointerdown", "mousedown", "pointerup", "mouseup"].forEach(function (k) { el.dispatchEvent(new MouseEvent(k, {bubbles: true})); });
      el.click();
      return "clicked";
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
        ct.some(function (x) { return /^positions$/i.test(tabText(x)); }) && ct.some(function (x) { return /^open orders$/i.test(tabText(x)); });
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
      var b = all(roleSel || "button,[role=tab],[role=button],a").filter(function (x) { return tabMatch(rx, t(x)); })[0];
      if (!b) return "none";
      // the tab strip scrolls horizontally on a phone ("Closed orders" is the last chip, clipped): bring it into view
      try { b.scrollIntoView({block: "nearest", inline: "center"}); } catch (e) { }
      b.click(); return "clicked";
    },
    // POSITIONS-READ CONTEXT (PHONE-POSITIONS-READ-CONTEXT, 2026-10-13). The bottom-panel tab chips render AFTER the
    // document is "complete" (SPA), so the app waits on this before clicking. Names + counts only, never values.
    tabChips: function () {
      var n = leafTabs().length, path = location.pathname || "/";
      return {chips: n, path_depth: path.split("/").length, trade: /\/trade(\/|$)/.test(path), acct_href: window.__ex.terminalHref() ? 1 : 0};
    },
    // Like clickText, but also finds a chip that is a plain div/span/li (no button/role): the LEAF element whose
    // whole text is the label. Read-only view switch, same as clickText.
    clickTab: function (re) {
      if (window.__ex.clickText(re) === "clicked") return "clicked";
      var rx = new RegExp(re, "i");
      var b = all("div,span,li").filter(function (x) { var s = t(x); return s.length > 0 && s.length <= 30 && tabMatch(rx, s) && TAB_LABEL.test(s); })
        .filter(function (x, i, a) { return !a.some(function (y) { return y !== x && x.contains(y); }); })[0];
      if (!b) return "none";
      try { b.scrollIntoView({block: "nearest", inline: "center"}); } catch (e) { }
      b.click(); return "clicked";
    },
    // The miss dump for a tab that could not be found: where we are (path depth, /trade or not), how many chips, the
    // buttons/tabs, and a short list of the leaf texts that ARE on the page. Digits masked, first 25, no links.
    missDump: function () {
      var seen = {}, out = [];
      all("*").forEach(function (x) {
        if (out.length >= 25 || x.children.length > 0) return;
        var s = t(x).replace(/\d/g, "#").slice(0, 30); if (s && !seen[s]) { seen[s] = 1; out.push(s); }
      });
      var tc = window.__ex.tabChips();
      return {path_depth: tc.path_depth, trade: tc.trade, chips: tc.chips, headers: out, controls: window.__ex.controls()};
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
      if (shown && (shown === n || shown === n + "T" || shown === b)) { window.__ex._mk = null; return "done"; }
      // Route 0 (SOL-PICKER): the "Select market" chip. Opened once per symbol, then ONE row picked from what it shows.
      var mk = window.__ex._mk; if (mk && (mk.sym !== n || Date.now() - mk.at > 120000)) mk = window.__ex._mk = null;
      if (mk && mk.phase === "opened") {
        var rows = marketCandidates(sym);
        if (rows.length === 1) { mk.phase = "picked"; rows[0].click(); return "picked_market"; }
        var ms = rows.length ? null : marketSearch();
        if (ms && norm(ms.value) !== b && !mk.typed) { mk.typed = 1; ms.focus(); setVal(ms, b); return "typed_market_search"; }
        // nothing clicked: close the list and let the next call take the watchlist route
        mk.phase = "failed";
        document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
        return rows.length > 1 ? "market_ambiguous" : "market_no_row";
      }
      var chip = marketChip();
      if (chip && !mk) {
        window.__ex._mk = {sym: n, at: Date.now(), phase: "opened",
          pre: all("span,div,p,strong,b,td,li,a,button,[role]"), preInputs: inputs(document)};
        chip.click(); return "opened_market";
      }
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
    // Forget the picker state: the app calls this once at the start of each ticket.
    symbolReset: function () { window.__ex._mk = null; return "ok"; },
    // The "Select market" chip's base ("ETH" from "ETH 20x"); "" when there is not exactly one such chip.
    marketShown: function () { var mc = marketChip(); if (!mc) return ""; var m = CHIP_RE.exec(t(mc)); return m ? m[1].toUpperCase() : "?"; },
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
    // ACCOUNT PANEL READ (PHONE-BALANCE-READ, read-only). The terminal shows "Portfolio #,###.## USD" as a control and
    // may label Balance / Equity beside their values. Anchored on the LABEL (own text, or label + value in one short
    // element, or label element + adjacent value element); a value is returned only when ONE distinct number matched,
    // else null (absent is never 0, never guessed). "Portfolio" is reported separately so the app can say which label
    // the equity came from. Numbers only; no account id, no other text.
    accountPanel: function () {
      function num(s) { var m = /^[-+]?\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(USD[TC]?)?$/i.exec((s || "").replace(/\u2212/g, "-").trim()); return m ? parseFloat(m[0].replace(/[^\d.\-]/g, "")) : null; }
      function pick(label) {
        var rx = new RegExp("^" + label + "\\s*:?\\s*(.*)$", "i"), found = {};
        all("button,[role=button],[role=tab],div,span,p,td,li,dt,strong,b,h1,h2,h3,h4").forEach(function (x) {
          var tx = t(x); if (tx.length > 60) return;
          var m = rx.exec(tx); if (!m) return;
          var v = num(m[1]);
          if (v === null && m[1] === "") {   // the element is the bare label: value in the next sibling or the parent's remainder
            var sib = x.nextElementSibling; v = sib ? num(t(sib)) : null;
            if (v === null && x.parentElement) { var pm = rx.exec(t(x.parentElement)); v = pm ? num(pm[1]) : null; }
          }
          if (v !== null) found[v] = 1;
        });
        var k = Object.keys(found); return {n: k.length, v: k.length === 1 ? parseFloat(k[0]) : null};
      }
      var b = pick("balance"), e = pick("equity"), p = pick("portfolio");
      // the terminal's own account-level unrealized P&L when it labels one (PROP-TRACKING); signed, absent = null
      var u = pick("(?:unreali[sz]ed\\s*(?:pnl|p\\s*&\\s*l)?|u\\.?\\s*pnl)");   // non-capturing: pick() reads group 1
      return {balance: b.v, equity: e.v, portfolio: p.v, unrealized: u.v, n: {balance: b.n, equity: e.n, portfolio: p.n, unrealized: u.n}};
    },
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
    // AMEND: the ONE open-position row for (symbol, side): {n, sl, tp (column text or null), headers, controls}.
    // controls are the row's own control texts (digits masked) for the refusal dump; never values beyond SL/TP.
    posInfo: function (sym, sideRe) {
      var rs = posRows(sym, sideRe);
      if (rs.length !== 1) return {n: rs.length};
      var c = rowCells(rs[0]), both = tpslCol(c.map), d = ctlDump(rs[0]);
      return {n: 1, layout: isGridRow(rs[0]) ? "div_grid" : "table",
        sl: both ? both.sl : col(c.map, "^(sl|s/l|stop ?loss)"), tp: both ? both.tp : col(c.map, "^(tp|t/p|take ?profit)"),
        headers: c.headers.map(function (h) { return h.replace(/\d/g, "#").slice(0, 24); }),
        controls: rowCtls(rs[0]).map(function (x) { return ctlText(x).replace(/\d/g, "#").slice(0, 40); }).slice(0, 12),
        ctls: d, ctl_lines: ctlLines(d)};
    },
    // AMEND: click the row's ONE edit control (text / aria-label / title naming edit, modify or TP/SL; never a close).
    // Returns clicked | no_row | rows_<n> | no_edit | ambiguous.
    posEdit: function (sym, sideRe) {
      if (!hostOk()) return "bad_host";
      var rs = posRows(sym, sideRe);
      if (!rs.length) return "no_row"; if (rs.length > 1) return "rows_" + rs.length;
      // MEASURE FIRST: no capture has shown Breakout's div-grid edit control, so nothing in a grid row is clicked; the
      // app refuses with the row's ctlDump (posInfo) as the evidence for the build that measures it.
      if (isGridRow(rs[0])) return "no_edit_unmeasured";
      var e = rowCtls(rs[0]).filter(function (x) { var s = ctlText(x); return /edit|modify|tp\s*\/\s*sl|sl\s*\/\s*tp|protection/i.test(s) && !NOT_EDIT.test(s); });
      if (!e.length) return "no_edit"; if (e.length > 1) return "ambiguous";
      e[0].click(); return "clicked";
    },
    // AMEND dialog read: how many dialogs, their inputs (label + value: values are OUR prices) and button texts.
    editDialog: function () {
      var ds = dialogs(); if (ds.length !== 1) return {n: ds.length};
      return {n: 1, text: t(ds[0]).replace(/\d/g, "#").slice(0, 200),
        inputs: inputs(ds[0]).map(function (i) { return {label: labelFor(i), value: i.value}; }),
        buttons: Array.prototype.slice.call(ds[0].querySelectorAll("button,[role=button]")).filter(vis).map(function (x) { return ctlText(x).replace(/\d/g, "#").slice(0, 30); })};
    },
    dlgRead: function (re, prefer) { return fieldInfo(dlgByLabel(re, prefer)); },
    dlgSet: function (re, prefer, v) {
      var h = dlgByLabel(re, prefer); if (h.hits.length !== 1) return fieldInfo(h);
      var i = h.hits[0]; i.focus(); setVal(i, String(v)); i.blur(); return fieldInfo(h);
    },
    dlgFocus: function (re, prefer) {
      var h = dlgByLabel(re, prefer); if (h.hits.length !== 1) return "none";
      var i = h.hits[0]; setVal(i, ""); i.focus(); return document.activeElement === i ? "focused" : "nofocus";
    },
    // The ONE save/confirm button of the ONE dialog (never close/cancel/remove/flatten/reverse). clicked | none | ambiguous | disabled
    dlgSave: function () {
      var b = dlgButtons("^\\s*(save|confirm|modify|apply|update|submit|ok)\\b", NOT_EDIT);
      if (!b.length) return "none"; if (b.length > 1) return "ambiguous";
      if (b[0].disabled || b[0].getAttribute("aria-disabled") === "true") return "disabled";
      b[0].click(); return "clicked";
    },
    // Dismiss the ONE dialog without saving: its ONE cancel/discard button, else Escape. clicked | escape | none
    dlgCancel: function () {
      var d = dlg(); if (!d) return "none";
      var b = dlgButtons("^\\s*(cancel|discard|dismiss)\\s*$", null);
      if (b.length === 1) { b[0].click(); return "clicked"; }
      d.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", keyCode: 27, bubbles: true}));
      document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", keyCode: 27, bubbles: true}));
      return "escape";
    },
    // POSITIONS READ (PROP-TRACKING, 2026-10-12, read-only; operator: "we need to be able to track the prop trades").
    // The Positions view must already be shown (the app clicks the tab). Columns are mapped BY HEADER TEXT; a
    // value the page does not show stays null, never 0. state: read (positions [] means the terminal SAYS flat:
    // a recognised header row with no data rows, or an explicit "no positions" text) | no_table | unparsed |
    // count_mismatch (the tab's own count disagrees with the rows parsed). Anything but "read" reconciles nothing.
    // UNMEASURED: no capture has shown Breakout's Positions table; headers come back digit-masked for a miss dump.
    positionsRead: function () {
      if (!hostOk()) return {state: "bad_host"};
      return readTable(POS_COLS, ["symbol", "side", "qty"], /no (open )?positions|you have no (open )?positions|start trading with leverage/i, /^positions\b/i, true);
    },
    // CLOSED ORDERS READ (PROP-TRACKING): the shown Closed orders / Trades table, newest rows as the page orders
    // them, max 15. Only OUR orders' symbol, side, qty, price, status, time and realized P&L columns; nothing else.
    closedRead: function () {
      if (!hostOk()) return {state: "bad_host"};
      return readTable(CLOSED_COLS, ["symbol", "side"], /no (closed )?orders|no (trades|history)/i, /^(closed orders|trades)\b/i, false);
    },
    // ---- POSITION SHEET (BREAKOUT-SL-AMEND-CLICK) ----
    // Open the sheet of the ONE Positions row for `sym`. which = "child" taps the position row's Market cell ("ETH 5x",
    // a leverage badge); "group" taps the group row's ("ETH 1", the symbol + a count). Only inside the Positions grid
    // panel, never a control the denylist names. clicked | bad_host | sheet_open | no_grid | no_panel | none | rows_<n> |
    // deny_*.
    sheetOpen: function (sym, which, mode) {
      if (!hostOk()) return "bad_host";
      if (sheetRoots().length) return "sheet_open";
      var h = sheetHit(sym, which); if (typeof h === "string") return h;
      window.__exTap = h;
      // mode "pointer" (BREAKOUT-SHEET-PROBE): the 1b.205 run tapped both rows with click() and no sheet opened; a
      // touch-style pointer sequence at the cell's centre is the second, measured attempt. Same target, same denylist.
      if (mode === "pointer" && h.scrollIntoView) h.scrollIntoView({block: "center", inline: "nearest"});
      return mode === "pointer" ? safeTap(h) : safeClick(h);
    },
    // NATIVE TAP target (BREAKOUT-NATIVE-TAP; 1b.210 08:37Z: click() and a synthetic pointer tap reached the cell, the
    // group tap EXPANDED the row, and no synthetic event opened the sheet -- the opener likely wants a trusted touch).
    // The SAME row cell sheetOpen would click, denylist-checked, and the point the app will touch re-checked with
    // elementFromPoint: the topmost element there must be that cell or inside it (never a row ✕ or an overlay), and
    // nothing from it up to the cell may be denied. scroll=true centres the cell first (the caller waits, then asks
    // again without scrolling, right before it touches). {ok, x, y, vw, vh} in CSS px, or {ok:false, why}.
    sheetTarget: function (sym, which, scroll) {
      if (!hostOk()) return {ok: false, why: "bad_host"};
      if (sheetRoots().length) return {ok: false, why: "sheet_open"};
      var h = sheetHit(sym, which); if (typeof h === "string") return {ok: false, why: h};
      window.__exTap = h;
      var d = denied(h); if (d) return {ok: false, why: d};
      if (scroll && h.scrollIntoView) h.scrollIntoView({block: "center", inline: "nearest"});
      var r = h.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
      if (!(r.width > 0 && r.height > 0) || x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) return {ok: false, why: "offscreen"};
      var pe = document.elementFromPoint(x, y);
      if (!pe || !h.contains(pe)) return {ok: false, why: "covered", top: pe ? (pe.tagName.toLowerCase() + (pe.getAttribute("role") ? "/" + pe.getAttribute("role") : "")).slice(0, 20) : ""};
      for (var e = pe; e && e !== h.parentElement; e = e.parentElement) { var de = denied(e); if (de) return {ok: false, why: "point_" + de}; }
      return {ok: true, x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10, vw: window.innerWidth, vh: window.innerHeight};
    },
    // MEASURE (BREAKOUT-NATIVE-TAP finding a): after the group row expands, the child row shows the side as TEXT
    // ("Short") and "1 TP / 1 SL". Its shape, so childRows() can be fixed from evidence if the read still misses it:
    // the exact side words in the Positions panel, and for each the row it sits in -- that row's cell count against the
    // header's, its depth below the panel, and whether childRows() would pick it. No values (texts are fixed words).
    childShape: function () {
      var g = gridHeaders(POS_COLS); if (g.length !== 1) return {ok: false, why: "no_grid", n: g.length};
      var h = g[0], panel = gridPanel(h) || h.parentElement, n = kids(h).length;
      var groups = gridRowsOf(h).rows.filter(isGroupRow), ch = childRows(h, groups.length ? groups : [h]);
      var sides = textEls(panel, /^(long|short)$/i, 6).filter(function (e) { return !h.contains(e); });
      return {ok: true, hdr: n, groups: groups.length, child_rows: ch.length, sides: sides.slice(0, 4).map(function (e) {
        var row = e, depth = 0;
        for (var a = e.parentElement; a && a !== panel; a = a.parentElement) { depth++; if (kids(a).length >= 4) { row = a; break; } }
        var rd = 0; for (var b = row; b && b !== panel; b = b.parentElement) rd++;
        var role = row.getAttribute ? (row.getAttribute("role") || "") : "";
        return {side: t(e).toLowerCase(), cells: kids(row).length, depth: depth, row_depth: rd, role: role, picked: ch.indexOf(row) >= 0,
          tpsl: textEls(row, SHEET_TAB.tpsl).length};
      })};
    },
    // MEASURE (BREAKOUT-SHEET-PROBE): what the page shows before / after a tap. domMark remembers every visible element;
    // domDiff returns the viewport size, the dialog count and the NEW visible leaf texts (digits masked, max 15), and
    // the tapped cell's ancestor chain (tag/role/first classes, masked) so the row's handler can be located.
    domMark: function () { window.__exMarkSet = typeof Set === "function" ? new Set(all("*")) : null; return window.__exMarkSet ? window.__exMarkSet.size : -1; },
    domDiff: function () {
      var m = window.__exMarkSet, nw = m ? all("*").filter(function (e) { return !m.has(e); }) : [];
      var mk = function (s) { return (s || "").replace(/\d/g, "#"); };
      var leaves = innermost(nw).map(function (e) { return mk(t(e)).slice(0, 36); }).filter(Boolean);
      var chain = [], e = window.__exTap;
      for (var k = 0; e && k < 7 && e !== document.body; e = e.parentElement, k++) {
        var cls = (typeof e.className === "string" ? e.className : (e.getAttribute("class") || "")).split(/\s+/).slice(0, 2).join(".");
        chain.push(mk(e.tagName.toLowerCase() + (e.getAttribute("role") ? "/" + e.getAttribute("role") : "") + (cls ? "." + cls : "") +
          (e.hasAttribute("tabindex") ? " ti" : "") + (e.getAttribute("data-state") ? " ds=" + e.getAttribute("data-state") : "")).slice(0, 40));
      }
      return {w: window.innerWidth, h: window.innerHeight, n_new: nw.length, dialogs: dialogs().length, leaves: leaves.slice(0, 15),
        details_tabs: textEls(document, SHEET_TAB.details).length, tpsl_tabs: textEls(document, SHEET_TAB.tpsl).length, chain: chain};
    },
    // The open sheet: {open, n, title_ok (a title equal to the symbol), subtitle (digits masked), tabs present}
    sheetInfo: function (sym) {
      var rs = sheetRoots(); if (rs.length !== 1) return {open: false, n: rs.length};
      var r = rs[0], b = base(sym).replace(/[^A-Z0-9]/g, "");
      var title = textEls(r, new RegExp("^" + b + "(USD[TC]?|PERP)?$", "i"), 16), sub = textEls(r, SUBTITLE_RE, 60);
      return {open: true, n: 1, title_ok: title.length >= 1, subtitle: sub.length ? t(sub[0]).replace(/\d/g, "#").slice(0, 30) : "",
        tabs: {details: textEls(r, SHEET_TAB.details).length, trades: textEls(r, SHEET_TAB.trades).length, tpsl: textEls(r, SHEET_TAB.tpsl).length}};
    },
    // Click one of the sheet's tabs: details | trades | tpsl. clicked | no_sheet | none | ambiguous | deny_*
    sheetTab: function (which) {
      var r = sheetRoot(); if (!r) return "no_sheet";
      var ts = textEls(r, SHEET_TAB[which] || /^$/); if (!ts.length) return "none"; if (ts.length > 1) return "ambiguous";
      return safeClick(ts[0]);
    },
    // [Position details] read: side / qty / entry / mark as the sheet SHOWS them (MEASURED); side only from an exact
    // "Long" / "Short" value. upnl_signed: whether the uPnL text carries a sign (a sign-less loss is never a MEASURED
    // number; the caller drops an unsigned upnl). upnl_shape is the text, digits masked (the open sign flag).
    sheetDetails: function () {
      var r = sheetRoot(); if (!r) return {ok: false, why: "no_sheet"};
      var o = {ok: true, miss: []};
      Object.keys(DETAIL_LABELS).forEach(function (k) {
        var v = detailValue(r, DETAIL_LABELS[k]);
        o[k + "_raw_n"] = v.n; o[k + "_text"] = v.value == null ? null : v.value.slice(0, 40);
        if (v.value == null && k !== "margin") o.miss.push(k + (v.why ? ":" + v.why : ":n" + v.n));
      });
      var sv = (o.side_text || "").trim();
      o.side = /^long$/i.test(sv) ? "long" : (/^short$/i.test(sv) ? "short" : null);
      o.qty = o.qty_text == null ? null : firstNumber(o.qty_text);
      o.qty_unit = o.qty_text == null ? "" : (o.qty_text.replace(/[^A-Za-z]/g, "") || "").slice(0, 8);
      o.entry = o.entry_text == null ? null : firstNumber(o.entry_text);
      o.mark = o.mark_text == null ? null : firstNumber(o.mark_text);
      o.upnl_signed = o.upnl_text != null && /^\s*[-+−]/.test(o.upnl_text);
      o.upnl = o.upnl_text == null ? null : firstNumber(o.upnl_text);
      o.upnl_shape = o.upnl_text == null ? "" : o.upnl_text.replace(/\d/g, "d").slice(0, 24);
      delete o.margin_text; delete o.upnl_text;
      if (o.side == null || o.qty == null || o.entry == null) o.ok = false;
      return o;
    },
    // [n TP / n SL] read: the order rows {type stop|take, trigger, closing side word, icons (masked dump)}. The SIDE
    // column is the CLOSING order's side and is returned only as `close_side`, never as the position's side.
    sheetOrders: function () {
      var r = sheetRoot(); if (!r) return {ok: false, why: "no_sheet"};
      var rows = sheetOrderRows(r).map(function (o) {
        if (!o.el) return {type: o.type, trigger: null, why: "no_row"};
        var ic = rowIcons(o.el, [o.typeEl].concat(o.px)).map(iconDump);
        return {type: o.type, trigger: o.px.length === 1 ? firstNumber(t(o.px[0])) : null, n_px: o.px.length,
          close_side: sideOf(wt(o.el)) || "", icons: ic, icon_lines: iconLines(ic), pencils: ic.filter(function (c) { return c.pencil; }).length};
      });
      return {ok: true, n_stop: rows.filter(function (x) { return x.type === "stop"; }).length,
        n_take: rows.filter(function (x) { return x.type === "take"; }).length, rows: rows};
    },
    // Click the ONE pencil of the ONE Stop row (never the ⓧ next to it). clicked | no_sheet | stops_<n> | no_edit |
    // ambiguous | deny_*
    sheetEditStop: function () {
      if (!hostOk()) return "bad_host";
      var r = sheetRoot(); if (!r) return "no_sheet";
      var st = sheetOrderRows(r).filter(function (o) { return o.type === "stop"; });
      if (st.length !== 1 || !st[0].el) return "stops_" + st.length;
      var p = rowIcons(st[0].el, [st[0].typeEl].concat(st[0].px)).filter(isPencil);
      if (!p.length) return "no_edit"; if (p.length > 1) return "ambiguous";
      return safeClick(p[0]);
    },
    // The edit modal: {n, kind stop|take, side, qty, unit, entry (from the "Short 0.54 ETH @ 2,461.52 USD" header),
    // inputs [{label, value}], triggers (count of the Trigger input), buttons (masked)}
    modalInfo: function () {
      var ms = modalRoot(); if (ms.length !== 1) return {n: ms.length};
      var r = ms[0].root, kind = /stop/i.test(t(ms[0].title)) ? "stop" : "take";
      var h = textEls(r, MODAL_HDR, 60), m = h.length === 1 ? MODAL_HDR.exec(t(h[0])) : null;
      return {n: 1, kind: kind, title: t(ms[0].title).slice(0, 30), hdr_n: h.length,
        side: m ? m[1].toLowerCase() : null, qty: m ? parseFloat(m[2].replace(/,/g, "")) : null, unit: m ? (m[3] || "") : "",
        entry: m ? parseFloat(m[4].replace(/,/g, "")) : null, inputs: modalInputs(r), triggers: triggerInput(r).length,
        buttons: Array.prototype.slice.call(r.querySelectorAll("button,[role=button]")).filter(vis).map(function (x) { return t(x).replace(/\d/g, "#").slice(0, 20); }).slice(0, 10)};
    },
    // Set ONLY the modal's ONE Trigger input. {n, value} (n = how many Trigger inputs matched; nothing typed unless 1)
    modalSetTrigger: function (v) {
      var ms = modalRoot(); if (ms.length !== 1) return {n: -ms.length};
      var ins = triggerInput(ms[0].root); if (ins.length !== 1) return {n: ins.length};
      var i = ins[0]; i.focus(); setVal(i, String(v)); i.blur(); return {n: 1, value: i.value};
    },
    modalTrigger: function () {
      var ms = modalRoot(); if (ms.length !== 1) return {n: -ms.length};
      var ins = triggerInput(ms[0].root); return ins.length === 1 ? {n: 1, value: ins[0].value} : {n: ins.length};
    },
    // The modal's ONE "Confirm" button. clicked | none | ambiguous | disabled | deny_*
    modalConfirm: function () {
      var ms = modalRoot(); if (ms.length !== 1) return "none";
      var b = Array.prototype.slice.call(ms[0].root.querySelectorAll("button,[role=button]")).filter(function (x) { return vis(x) && /^confirm$/i.test(t(x)); });
      if (!b.length) return "none"; if (b.length > 1) return "ambiguous";
      if (b[0].disabled || b[0].getAttribute("aria-disabled") === "true") return "disabled";
      return safeClick(b[0]);
    },
    // Dismiss the modal without saving: its ONE button whose text is exactly "Cancel" (a text button inside the modal,
    // never an icon), else Escape. clicked | escape | none
    modalCancel: function () {
      var ms = modalRoot(); if (ms.length !== 1) return "none";
      var b = Array.prototype.slice.call(ms[0].root.querySelectorAll("button,[role=button]")).filter(function (x) { return vis(x) && /^cancel$/i.test(t(x)); });
      if (b.length === 1) { b[0].click(); return "clicked"; }
      document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", keyCode: 27, bubbles: true}));
      return "escape";
    },
    // Close the sheet: Escape first (the caller re-checks sheetInfo); sheetCloseX is the fallback.
    sheetClose: function () {
      var r = sheetRoot(); if (!r) return "none";
      [r, document].forEach(function (x) { x.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", keyCode: 27, bubbles: true})); });
      return "escape";
    },
    // The sheet's header ✕: an icon whose glyph or words say close, ABOVE the tabs (document order before the
    // [Position details] tab), never a text button ("Close position" is below the tabs and has text). clicked | none |
    // ambiguous | no_sheet
    sheetCloseX: function () {
      var r = sheetRoot(); if (!r) return "no_sheet";
      var tab = textEls(r, SHEET_TAB.details)[0];
      var c = rowIcons(r, []).filter(function (x) {
        var s = t(x); return tab && before(x, tab) && !/position/i.test(s) && (DENY_GLYPH.test(s) || /^close$/i.test((x.getAttribute("aria-label") || x.getAttribute("title") || "").trim()) || (!s && /\bclose\b/i.test(ctlWords(x))));
      });
      if (!c.length) return "none"; if (c.length > 1) return "ambiguous";
      c[0].click(); return "clicked";
    },
    // the denylist, exposed for the test: why `sel`'s ONE element must never be clicked, or "" when it may be
    deniedSel: function (sel) { var e = document.querySelectorAll(sel); return e.length === 1 ? (denied(e[0]) || "") : "n" + e.length; },
    safeClickSel: function (sel) { var e = document.querySelectorAll(sel); return e.length === 1 ? safeClick(e[0]) : "n" + e.length; },
    rowClose: function (sym, sideRe) {
      var n = norm(sym); var rx = new RegExp(sideRe, "i");
      var r = all("tr,[role=row]").filter(function (r) { var s = t(r); return norm(s).indexOf(n) >= 0 && rx.test(s); })[0];
      if (!r) return "none";
      var b = Array.prototype.slice.call(r.querySelectorAll("button,[role=button]")).filter(function (x) { return vis(x) && /close/i.test(t(x) + " " + (x.getAttribute("aria-label") || "")); })[0];
      if (!b) return "no_close"; b.click(); return "clicked";
    }
  };
})();
