// Page-side check for tools/phone-executor/app/src/main/assets/exec.js against a SYNTHETIC ticket built to the
// shape read on Breakout's terminal (design § 7.9: Market/Limit/Trigger tabs, Buy/Sell tabs, "Limit price" and
// "Quantity" inputs, a TP/SL checkbox revealing fields, a separate "Long (buy) ..." submit disabled until a
// quantity is entered). It proves the helpers' mechanics, NOT Breakout's real labels (the dry run measures those).
const { chromium } = require("playwright");
const fs = require("fs");
const src = fs.readFileSync("tools/phone-executor/app/src/main/assets/exec.js", "utf8");
const page = `<!doctype html><html><body>
<button>Positions</button><button>Open orders</button><button>Order</button>
<h2>ETHUSD</h2>
<form id=f>
 <div role=tablist><button type=button role=tab aria-selected=false>Market</button><button type=button role=tab aria-selected=true>Limit</button><button type=button role=tab aria-selected=false>Trigger</button></div>
 <div role=tablist><button type=button role=tab aria-selected=true>Buy</button><button type=button role=tab aria-selected=false>Sell</button></div>
 <label for=lp>Limit price</label><input id=lp type=text inputmode=numeric>
 <label for=q>Quantity</label><input id=q type=text inputmode=numeric>
 <div role=alert>Quantity ETH available to trade 1.23</div>
 <label><input type=checkbox id=cb> TP/SL</label>
 <div id=tpsl style="display:none"><label for=tp>Take profit price</label><input id=tp type=text><label for=tpp>Take profit %</label><input id=tpp type=text>
 <label for=sl>Stop loss price</label><input id=sl type=text><label for=slp>Stop loss %</label><input id=slp type=text></div>
 <button type=submit id=sub disabled>Long (buy) ETHUSD</button>
</form>
<script>
document.querySelectorAll('[role=tab]').forEach(function(b){b.onclick=function(){b.parentElement.querySelectorAll('[role=tab]').forEach(function(x){x.setAttribute('aria-selected','false')});b.setAttribute('aria-selected','true');
 if(b.textContent==='Sell')document.getElementById('sub').textContent='Short (sell) ETHUSD'; if(b.textContent==='Buy')document.getElementById('sub').textContent='Long (buy) ETHUSD';}});
document.getElementById('cb').onchange=function(){document.getElementById('tpsl').style.display=this.checked?'block':'none'};
document.getElementById('q').addEventListener('input',function(){document.getElementById('sub').disabled=!this.value});
document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};
</script></body></html>`;
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) { console.error("FAIL", m, JSON.stringify(a), "!=", JSON.stringify(b)); process.exit(1); } console.log("ok", m); }
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  await p.setContent(page); await p.addScriptTag({ content: src });
  const r = (code) => p.evaluate(code);
  eq(await r("__ex.state().ticketOpen"), true, "ticket open");
  eq(await r("__ex.tab('Sell')"), "clicked", "sell tab");
  eq(await r("__ex.tabSelected('Sell')"), true, "sell selected");
  eq(await r("/short/i.test(__ex.ticket().submit.text)"), true, "submit label follows side");
  await r("__ex.tab('Buy')");
  let tk = await r("__ex.ticket()");
  const k = (re) => tk.inputs.filter((i) => new RegExp(re, "i").test(i.label)).map((i) => i.k);
  eq(k("limit price").length, 1, "one limit price field");
  eq(await r(`__ex.setInput(${k("limit price")[0]}, '2500.10')`), "2500.10", "price read back");
  eq(await r("__ex.ticket().submit.disabled"), true, "submit disabled before qty");
  eq(await r(`__ex.setInput(${k("quantity")[0]}, '0.01')`), "0.01", "qty read back");
  eq(await r("__ex.ticket().submit.disabled"), false, "submit enabled after qty (input event registered)");
  eq(await r("__ex.setTpsl(true)"), "ok", "tp/sl ticked");
  tk = await r("__ex.ticket()");
  eq(tk.tpsl, true, "tpsl read back on");
  eq(k("take ?profit").length, 2, "two TP fields (price + %): app must prefer 'price'");
  eq(k("take ?profit.*price|price.*take ?profit").length, 1, "one TP price field");
  eq(/ETH/.test(JSON.stringify(tk.alerts)), true, "quantity unit alert names the base asset");
  eq(await r("window.__submitted || 0"), 0, "nothing submitted by the fill path");
  eq(await r("__ex.loginNumber()"), "", "no login number on the ticket page");
  // LANDING STATES on a routed trade.breakoutprop.com origin (fix 2026-10-05 ~20:20Z): the logged-in landing has
  // no tabs and no exact "Positions" button, and must still read as logged in, never as "other".
  const p2 = await b.newPage();
  const pages = {
    "/en/account/A1/trade": "<div><span>Trade</span><div role=button>Portfolio</div><div>Open orders</div><div>Positions</div><canvas></canvas></div>",
    "/en/account/A1/overview": "<div><span>Portfolio</span><div>Positions</div><p>Balance</p></div>",
    "/en/account/A1/trade-ready": "<div><button>Order</button><div role=tablist><button role=tab>Buy</button><button role=tab>Sell</button></div></div>",
    "/": "<div><h1>Your accounts</h1><a href='/en/account/A1/trade'>Turbo 5K</a></div>",
    "/two": "<div><a href='/en/account/A1/trade'>One</a><a href='/en/account/B2/trade'>Two</a></div>",
    "/en/account/A2/trade": "<div><div role=tablist><button role=tab>Positions</button><button role=tab>Open orders</button><button role=tab>History</button></div><span>Buy</span> 85,225 <span>Sell</span> 85,220</div>",
    "/en/account/A3/trade": "<div><span>Buy</span> <span>Sell</span></div>",
    "/login": "<form><input type=email><button>Continue</button></form>",
  };
  await p2.route("https://trade.breakoutprop.com/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><html><body>" + (pages[path] || "<p>?</p>") + "</body></html>" });
  });
  const st = async (path) => { await p2.goto("https://trade.breakoutprop.com" + path); await p2.addScriptTag({ content: src }); return p2.evaluate("__ex.state()"); };
  let s1 = await st("/en/account/A1/trade");
  eq([s1.loggedIn, s1.onAccount], [true, true], "landing inside an account reads logged in (no tabs, no exact button)");
  s1 = await st("/");
  eq([s1.loggedIn, s1.onAccount, s1.accountLinkCount, s1.singleAccountLink], [true, false, 1, "https://trade.breakoutprop.com/en/account/A1/trade"], "account list with ONE account: logged in, single link");
  s1 = await st("/two");
  eq([s1.loggedIn, s1.accountLinkCount, s1.singleAccountLink], [true, 2, ""], "two accounts: no single link (app waits for a human tap)");
  s1 = await st("/en/account/A1/overview");
  eq(s1.loggedIn, true, "account overview reads logged in");
  eq(await p2.evaluate("__ex.terminal().ready"), false, "account overview is NOT the terminal (the 05:21Z claim page): no claim");
  eq(await p2.evaluate("__ex.terminalHref()"), "https://trade.breakoutprop.com/en/account/A1/trade", "terminal URL derived from the current account path");
  await st("/en/account/A1/trade-ready");
  eq(await p2.evaluate("__ex.terminal().ready"), true, "terminal with Order control + Buy/Sell tabs is ready");
  // TERMINAL WITH THE TICKET CLOSED (fix 06:17Z, "terminal did not load" on the real terminal): no Order control,
  // no Buy/Sell tabs, but the 1a probe classifier (trade host, /trade path, buy+sell markers, tabs>=3) says ready.
  await st("/en/account/A2/trade");
  eq(await p2.evaluate("[__ex.terminal().ready, __ex.terminal().probe, __ex.terminal().orderControl]"), [true, true, false], "terminal with the ticket closed is ready via the probe classifier");
  eq(await p2.evaluate("__ex.controls()"), ["Positions", "Open orders", "History"], "controls(): the page's control texts for the miss event");
  await st("/en/account/A3/trade");
  eq(await p2.evaluate("__ex.terminal().ready"), false, "buy/sell words alone (no tabs, no inputs) are not the terminal");
  s1 = await st("/login");
  eq([s1.loggedIn, s1.email], [false, true], "login form is never logged in");
  // LABEL-BASED FIELDS (fix 2026-10-05 ~22:40Z): price and quantity are set and read back BY LABEL, never by index,
  // and the two labels must resolve to two different inputs with their own values.
  const p3 = await b.newPage(); await p3.setContent(page); await p3.addScriptTag({ content: src });
  const r3 = (code) => p3.evaluate(code);
  let sp = await r3("__ex.setByLabel('limit price', '', '2500.10')");
  eq([sp.n, sp.label, sp.value], [1, "Limit price", "2500.10"], "price set by label");
  let sq = await r3("__ex.setByLabel('quantity', '', '0.01')");
  eq([sq.n, sq.label, sq.value], [1, "Quantity", "0.01"], "quantity set by label");
  eq(sp.k !== sq.k, true, "price and quantity are different inputs");
  eq(await r3("__ex.readByLabel('limit price', '').value"), "2500.10", "price read back by label is still the price, not the quantity");
  eq(await r3("__ex.readByLabel('quantity', '').value"), "0.01", "quantity read back by label");
  eq(await r3("__ex.readByLabel('nosuchlabel', '').n"), 0, "missing label: n=0, nothing set");
  await r3("__ex.setTpsl(true)");
  let amb = await r3("__ex.setByLabel('take ?profit', '', '1')");
  eq([amb.n, amb.labels], [2, ["Take profit price", "Take profit %"]], "ambiguous label: n=2 with the labels, nothing set");
  eq(await r3("__ex.readByLabel('take ?profit', '').value === undefined"), true, "ambiguous label returns no value");
  eq((await r3("__ex.setByLabel('take ?profit', 'price', '2600')")).value, "2600", "prefer narrows TP to the price field");
  eq(await r3("__ex.readByLabel('take ?profit', '%').value"), "", "TP % field untouched");
  eq(await r3("__ex.ticket().symbol"), "ETHUSD", "ticket() reports the asset on the submit label");
  eq(await r3("window.__submitted || 0"), 0, "label-based fill submits nothing");

  // INSTRUMENT SELECTION (fix 2026-10-05 ~22:40Z): the terminal starts on BTC and the ticket is ETH. The executor
  // must select ETH (through whichever route the page offers) or refuse with a named reason; it never submits.
  // Fixture A: a symbol strip of buttons (the exact-text route).
  const strip = (cur) => `<!doctype html><html><body>
<div id=strip><button>BTCUSD</button><button>ETHUSD</button><button>SOLUSD</button></div>
<button>Order</button><h2 id=h>${cur}</h2>
<form id=f><div role=tablist><button type=button role=tab aria-selected=true>Market</button><button type=button role=tab aria-selected=false>Limit</button></div>
<div role=tablist><button type=button role=tab aria-selected=true>Buy</button><button type=button role=tab aria-selected=false>Sell</button></div>
<label for=lp>Limit price</label><input id=lp type=text value="85,778.8"><label for=q>Quantity</label><input id=q type=text value="85,778.8">
<div role=alert>Quantity exceeds available to trade</div>
<button type=submit id=sub>Long (buy) ${cur.replace('USD','')}</button></form>
<script>document.querySelectorAll('#strip button').forEach(function(b){b.onclick=function(){var s=b.textContent;document.getElementById('h').textContent=s;document.getElementById('sub').textContent='Long (buy) '+s.replace('USD','');}});
document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};</script></body></html>`;
  // symbolStep() refuses ("bad_host") off breakoutprop.com, so these fixtures are served on the trade host.
  const onHost = async (html) => { const pg = await b.newPage(); await pg.route("https://trade.breakoutprop.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: html }));
    await pg.goto("https://trade.breakoutprop.com/en/account/A1/trade"); await pg.addScriptTag({ content: src }); return pg; };
  const pA = await onHost(strip("BTCUSD"));
  const rA = (code) => pA.evaluate(code);
  eq(await r3("__ex.symbolStep('ETHUSD')"), "bad_host", "symbol routes refuse off breakoutprop.com");
  eq(await rA("__ex.symbolOnTicket()"), "BTC", "ticket starts on BTC (the 2026-10-05 22:20Z dump)");
  eq(await rA("__ex.symbolStep('ETHUSD')"), "clicked_symbol", "strip: exact-text ETHUSD button clicked");
  eq(await rA("__ex.symbolOnTicket()"), "ETH", "strip: submit label now names ETH");
  eq(await rA("__ex.symbolStep('ETHUSD')"), "done", "strip: second step reports done");
  eq(await rA("__ex.symbolStep('Market')"), "not_a_symbol", "a word that is not a symbol clicks nothing (Market tab is not a symbol control)");
  eq(await rA("__ex.tabSelected('Market')"), true, "Market tab untouched by the symbol routes");
  eq(await rA("window.__submitted || 0"), 0, "strip: nothing submitted");
  // Fixture B: a current-symbol picker (BTC/USD) that opens a search box; results are rows that START with the symbol.
  const picker = `<!doctype html><html><body>
<button id=cur aria-haspopup=listbox>BTC/USD</button><div id=pk style="display:none"><input id=s placeholder="Search symbol"><ul id=res></ul></div>
<form id=f><label for=lp>Limit price</label><input id=lp type=text><label for=q>Quantity</label><input id=q type=text>
<button type=submit id=sub>Long (buy) BTC</button></form>
<script>var ALL=['BTCUSD Bitcoin','ETHUSD Ethereum','ETHUSDT Ethereum Tether','SOLUSD Solana'];
document.getElementById('cur').onclick=function(){document.getElementById('pk').style.display='block'};
document.getElementById('s').addEventListener('input',function(){var v=this.value.toUpperCase();var u=document.getElementById('res');u.innerHTML='';ALL.filter(function(a){return a.indexOf(v)===0}).forEach(function(a){var li=document.createElement('li');li.textContent=a;li.onclick=function(){var sym=a.split(' ')[0];document.getElementById('cur').textContent=sym.slice(0,3)+'/'+sym.slice(3);document.getElementById('sub').textContent='Long (buy) '+sym.replace(/USDT?$/,'');document.getElementById('pk').style.display='none'};u.appendChild(li)})});
document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};</script></body></html>`;
  const pB = await onHost(picker);
  const rB = (code) => pB.evaluate(code);
  eq(await rB("__ex.symbolOnTicket()"), "BTC", "picker: ticket starts on BTC");
  eq(await rB("__ex.symbolStep('ETHUSD')"), "opened_picker", "picker: current-symbol control opened");
  eq(await rB("__ex.symbolStep('ETHUSD')"), "typed_search", "picker: symbol typed into the search box");
  eq(await rB("__ex.symbolStep('ETHUSD')"), "ambiguous", "picker: ETHUSD and ETHUSDT both start with ETHUSD and neither is exact-only -> refuse, click nothing");
  eq(await rB("__ex.symbolOnTicket()"), "BTC", "picker: ambiguous result changed nothing");
  await rB("ALL.splice(2,1); document.getElementById('s').dispatchEvent(new Event('input'))");
  eq(await rB("__ex.symbolStep('ETHUSD')"), "clicked_result", "picker: with one result left, its row (starts with the symbol) is clicked");
  eq(await rB("__ex.symbolOnTicket()"), "ETH", "picker: submit label now names ETH");
  eq(await rB("window.__submitted || 0"), 0, "picker: nothing submitted");
  // Fixture C: no symbol control at all -> "none", so the app refuses with the route in the reason.
  const pC = await onHost(strip("BTCUSD").replace(/<div id=strip>.*?<\/div>/, ""));
  eq(await pC.evaluate("__ex.symbolStep('ETHUSD')"), "none", "no selector: none (app refuses 'symbol ETH not on the submit label')");
  eq(await pC.evaluate("__ex.symbolOnTicket()"), "BTC", "no selector: ticket unchanged");
  // Fixture D (fix 06:24Z, am-2 "symbol route: none"): a watchlist whose rows are plain divs with the symbol in a
  // leaf span and the click handler on the row. Route 1b clicks the ONE exact leaf; the read-back verifies.
  const wl = strip("BTCUSD").replace(/<div id=strip>.*?<\/div>/, "<div id=wl><div class=r data-s=BTCUSD><span>BTCUSD</span><span>85,225.0</span></div><div class=r data-s=ETHUSD><span>ETHUSD</span><span>2,612.4</span></div></div>")
    .replace("document.querySelectorAll('#strip button').forEach(function(b){b.onclick=function(){var s=b.textContent;", "document.querySelectorAll('#wl .r').forEach(function(b){b.onclick=function(){var s=b.getAttribute('data-s');");
  const pD = await onHost(wl);
  eq(await pD.evaluate("__ex.symbolStep('ETHUSD')"), "clicked_label", "watchlist: exact leaf label clicked, row handler fires");
  eq(await pD.evaluate("__ex.symbolOnTicket()"), "ETH", "watchlist: submit label now names ETH");
  eq(await pD.evaluate("__ex.symbolStep('ETHUSD')"), "done", "watchlist: then done");
  const cands = await pD.evaluate("__ex.symbolCandidates().map(function(c){return c.text})");
  eq(cands.includes("BTCUSD") && cands.includes("ETHUSD") && !cands.some(function (c) { return /\d/.test(c); }), true, "symbolCandidates lists the watchlist symbols, digits masked");
  eq(await pD.evaluate("Array.isArray(__ex.ticket().symbolCandidates)"), true, "refusal dump carries symbolCandidates");
  eq(await pD.evaluate("window.__submitted || 0"), 0, "watchlist: nothing submitted");
  // Fixture E (MEASURED 2026-10-06 08:58Z terminal_miss): the real PANEL-layout terminal with the order form CLOSED:
  // no tabs, no inputs, no buy/sell text; an "Order form" toggle, Positions / Open orders panels, an open-instrument
  // chip "BTC x" and a watchlist of "<BASE> <signed %>" buttons. Opening the form shows the usual ticket.
  const panel = `<!doctype html><html><body>
<button>Trade</button><button id=chip>BTC x</button><button>Market chart</button><button id=of>Order form</button>
<button>Open orders</button><button>Positions</button><button>Order book</button>
<div id=wl><button data-s=BTC>BTC -1.2%</button><button data-s=ETH>ETH -0.5%</button><button data-s=SOL>SOL +2.0%</button></div>
<div id=panel></div>
<script>var cur='BTC';
function form(){return '<form id=f><div role=tablist><button type=button role=tab aria-selected=false>Market</button><button type=button role=tab aria-selected=true>Limit</button></div><div role=tablist><button type=button role=tab aria-selected=true>Buy</button><button type=button role=tab aria-selected=false>Sell</button></div><label for=lp>Limit price</label><input id=lp type=text value=1><label for=q>Quantity</label><input id=q type=text value=0><button type=button id=qu aria-label=\"Toggle quantity unit\">USD</button><div id=tps>TP/SL</div><div id=tpbox></div><button type=submit id=sub>Long (buy) '+cur+'</button></form>';}
document.getElementById('of').onclick=function(){document.getElementById('panel').innerHTML=form();document.getElementById('qu').onclick=function(){this.textContent=this.textContent==='USD'?cur:'USD';};document.getElementById('tps').onclick=function(){document.getElementById('tpbox').innerHTML='<label for=tp>Take profit price</label><input id=tp type=text><label for=sl>Stop loss price</label><input id=sl type=text>';};document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};};
document.querySelectorAll('#wl button').forEach(function(b){b.onclick=function(){cur=b.getAttribute('data-s');document.getElementById('chip').textContent=cur+' x';var sb=document.getElementById('sub');if(sb)sb.textContent='Long (buy) '+cur;};});
</script></body></html>`;
  const pE = await onHost(panel);
  const rE = (code) => pE.evaluate(code);
  eq(await rE("[__ex.terminal().ready, __ex.terminal().panels, __ex.terminal().tabs, __ex.terminal().inputs]"), [true, true, 0, 0], "panel terminal, form closed: ready via panels (the 08:58Z miss)");
  eq(await rE("__ex.symbolShown()"), "BTC", "panel: shown symbol read from the one 'BTC x' chip");
  eq(await rE("__ex.symbolStep('ETHUSD')"), "clicked_watch", "panel: the ONE 'ETH -0.5%' watchlist button clicked");
  eq(await rE("__ex.symbolStep('ETHUSD')"), "done", "panel: chip now 'ETH x' -> done");
  eq(await rE("__ex.openTicket()"), "clicked", "panel: 'Order form' opens the ticket");
  eq(await rE("__ex.symbolOnTicket()"), "ETH", "panel: submit label names ETH");
  // QUANTITY UNIT + TP/SL (MEASURED am-3 09:16Z): "Toggle quantity unit" button reads USD; "TP/SL" control hides the TP/SL inputs.
  eq(await rE("__ex.qtyUnit()"), "USD", "panel: quantity unit toggle reads USD");
  eq(await rE("__ex.toggleQtyUnit()"), "clicked", "panel: unit toggled once");
  eq(await rE("__ex.qtyUnit()"), "ETH", "panel: quantity unit now ETH");
  eq(await rE("__ex.readByLabel('take ?profit|\\btp\\b','price').n"), 0, "panel: TP field hidden before the TP/SL control");
  eq(await rE("__ex.openTpsl(0)"), "expanded", "panel: TP/SL control clicked");
  eq(await rE("__ex.openTpsl()"), "ok", "panel: TP/SL now open");
  eq(await rE("[__ex.readByLabel('take ?profit|\\btp\\b','price').n, __ex.readByLabel('stop ?loss|\\bsl\\b','price').n]"), [1, 1], "panel: TP and SL fields unique by label");
  eq(await rE("window.__submitted || 0"), 0, "panel: nothing submitted");
  // Fixture F (MEASURED am-4 09:48Z): TP/SL checkbox already CHECKED, section still collapsed behind a "TP/SL" header.
  const tpF = `<!doctype html><html><body><form id=f><div role=tablist><button type=button role=tab aria-selected=true>Buy</button></div>
<label for=lp>Limit price</label><input id=lp type=text><label><input type=checkbox id=cb checked>TP/SL</label>
<div id=hdr aria-expanded=false>TP/SL</div><div id=box></div><button type=submit>Long (buy) ETH</button></form>
<script>document.getElementById('hdr').onclick=function(){document.getElementById('box').innerHTML='<label for=tp>Take profit price</label><input id=tp type=text><label for=sl>Stop loss price</label><input id=sl type=text>';};
document.getElementById('cb').onclick=function(){window.__cbClicks=(window.__cbClicks||0)+1};</script></body></html>`;
  const pF = await onHost(tpF);
  eq(await pF.evaluate("__ex.openTpsl(0)"), "expanded", "checked box + collapsed section: header expanded, box untouched");
  eq(await pF.evaluate("[__ex.openTpsl(), document.getElementById('cb').checked, window.__cbClicks || 0]"), ["ok", true, 0], "then ok; checkbox never toggled off");
  // Fixture G: the "TP/SL" text is a plain span; the handler sits on a text-less role=switch beside it.
  const tpG = `<!doctype html><html><body><form id=f><label for=lp>Limit price</label><input id=lp type=text>
<div><span id=txt>TP/SL</span><button type=button role=switch id=sw aria-checked=false></button></div><div id=box></div>
<button type=submit>Long (buy) ETH</button></form>
<script>document.getElementById('sw').onclick=function(){this.setAttribute('aria-checked','true');document.getElementById('box').innerHTML='<label for=tp>Take profit price</label><input id=tp type=text><label for=sl>Stop loss price</label><input id=sl type=text>';};</script></body></html>`;
  const pG = await onHost(tpG);
  eq(await pG.evaluate("__ex.openTpsl(0)"), "expanded", "switch layout: text clicked first (no effect)");
  eq(await pG.evaluate("__ex.openTpsl(1)"), "switched", "switch layout: the text-less switch beside it is clicked");
  eq(await pG.evaluate("__ex.openTpsl(2)"), "ok", "switch layout: TP/SL inputs now shown");
  eq(await pG.evaluate("__ex.tpslArea()[0].node.text"), "TP/SL", "tpslArea dump names the TP/SL element");
  // POSITION AMEND (PROP-TRAIL-PHONE): SYNTHETIC Positions table + edit dialog. Proves the helpers' mechanics, NOT
  // Breakout's real edit UI (never captured): unique row, never a close control, dialog fields by label, one save.
  const posH = (cols, rowBtns, extraRow) => `<!doctype html><html><body><button>Positions</button><button>Open orders</button>
<table><thead><tr>${cols.map((c) => "<th>" + c + "</th>").join("")}</tr></thead><tbody>
<tr id=r1><td>ETHUSD</td><td>Buy</td><td>0.50</td><td>2,500.00</td>${cols.includes("SL") ? "<td id=csl>2,450.00</td><td id=ctp>2,800.00</td>" : ""}<td>${rowBtns}</td></tr>${extraRow || ""}
</tbody></table><div id=dl></div>
<script>window.__closed=0;window.__saved=0;
document.querySelectorAll('[data-close]').forEach(function(x){x.onclick=function(){window.__closed++}});
document.querySelectorAll('[data-edit]').forEach(function(x){x.onclick=function(){
 var sl=(document.getElementById('csl')||{}).textContent||window.__sl||'2450.00', tp=(document.getElementById('ctp')||{}).textContent||window.__tp||'2800.00';
 document.getElementById('dl').innerHTML='<div role=dialog><h3>Edit position</h3><label for=dsl>Stop loss price</label><input id=dsl type=text value="'+sl.replace(/,/g,'')+'"><label for=dslp>Stop loss %</label><input id=dslp type=text><label for=dtp>Take profit price</label><input id=dtp type=text value="'+tp.replace(/,/g,'')+'"><button id=sv>Save</button><button id=cx>Cancel</button><button>Close position</button></div>';
 document.getElementById('sv').onclick=function(){window.__saved++;var a=document.getElementById('dsl').value,b2=document.getElementById('dtp').value;if(document.getElementById('csl')){document.getElementById('csl').textContent=a;document.getElementById('ctp').textContent=b2}else{window.__sl=a;window.__tp=b2}document.getElementById('dl').innerHTML=''};
 document.getElementById('cx').onclick=function(){document.getElementById('dl').innerHTML=''};}});
</script></body></html>`;
  const COLS = ["Symbol", "Side", "Qty", "Entry", "SL", "TP", ""];
  const pP = await onHost(posH(COLS, "<button data-edit aria-label='Edit'>✎</button><button data-close>Close</button>"));
  const rP = (c) => pP.evaluate(c);
  let pi = await rP("__ex.posInfo('ETHUSD','long|buy')");
  eq([pi.n, pi.sl, pi.tp], [1, "2,450.00", "2,800.00"], "amend: one ETH long row, SL/TP columns read");
  eq((await rP("__ex.posInfo('ETHUSD','short|sell')")).n, 0, "amend: no short row");
  eq(await rP("__ex.posEdit('ETHUSD','long|buy')"), "clicked", "amend: the row's ONE edit control clicked");
  eq(await rP("window.__closed"), 0, "amend: close control never clicked");
  eq((await rP("__ex.editDialog()")).n, 1, "amend: one dialog");
  eq((await rP("__ex.dlgRead('stop ?loss|\\bsl\\b','')")).n, 2, "amend: SL label ambiguous (price + %) without prefer");
  eq((await rP("__ex.dlgRead('stop ?loss|\\bsl\\b','price')")).value, "2450.00", "amend: dialog SL read by label");
  eq((await rP("__ex.dlgSet('stop ?loss|\\bsl\\b','price','2530.00')")).value, "2530.00", "amend: dialog SL typed + read back");
  eq((await rP("__ex.dlgRead('take ?profit|\\btp\\b','price')")).value, "2800.00", "amend: kept TP unchanged");
  eq(await rP("__ex.dlgCancel()"), "clicked", "amend (dry): cancelled, not saved");
  eq([await rP("window.__saved"), (await rP("__ex.editDialog()")).n, (await rP("__ex.posInfo('ETHUSD','long|buy')")).sl], [0, 0, "2,450.00"], "amend (dry): nothing saved, row unchanged");
  await rP("__ex.posEdit('ETHUSD','long|buy')");
  await rP("__ex.dlgSet('stop ?loss|\\bsl\\b','price','2530.00')");
  eq(await rP("__ex.dlgSave()"), "clicked", "amend (live): the ONE save button (never 'Close position')");
  eq([await rP("window.__saved"), await rP("window.__closed"), (await rP("__ex.posInfo('ETHUSD','long|buy')")).sl], [1, 0, "2530.00"], "amend (live): saved, row read back with the new SL");
  // no SL/TP columns: the read-back re-opens the dialog; a row with only a close control has no edit route
  const pQ = await onHost(posH(["Symbol", "Side", "Qty", "Entry", ""], "<button data-edit title='Modify TP/SL'></button><button data-close>Close</button>"));
  eq([(await pQ.evaluate("__ex.posInfo('ETHUSD','long|buy')")).sl, await pQ.evaluate("__ex.posEdit('ETHUSD','long|buy')")], [null, "clicked"], "amend: no SL column -> null; edit found by title");
  const pR = await onHost(posH(["Symbol", "Side", "Qty", "Entry", ""], "<button data-close>Close</button><button data-close aria-label='Close position'>x</button>"));
  eq([await pR.evaluate("__ex.posEdit('ETHUSD','long|buy')"), await pR.evaluate("window.__closed")], ["no_edit", 0], "amend: only close controls -> no_edit, nothing clicked");
  const pS = await onHost(posH(COLS, "<button data-edit>Edit</button>", "<tr><td>ETHUSD</td><td>Buy</td><td>0.10</td><td>2,400</td><td>2,300</td><td>2,900</td><td><button data-edit>Edit</button></td></tr>"));
  eq(await pS.evaluate("__ex.posEdit('ETHUSD','long|buy')"), "rows_2", "amend: two ETH long rows -> refused, nothing clicked");
  eq(await pS.evaluate("__ex.dlgSave()"), "none", "amend: no dialog -> no save");

  // ACCOUNT PANEL READ (PHONE-BALANCE-READ): label-anchored, one distinct number or null.
  const acctA = `<!doctype html><html><body><button>Positions</button><button>Open orders</button>
<button id=pf>Portfolio 98,123.45 USD</button><button>Turbo Eval 1</button><div><span>Balance</span><span>97,000.10</span></div><div>Equity: 98,123.45</div></body></html>`;
  const acctpA = await onHost(acctA);
  eq(await acctpA.evaluate("__ex.accountPanel()"), {balance: 97000.1, equity: 98123.45, portfolio: 98123.45, unrealized: null, n: {balance: 1, equity: 1, portfolio: 1, unrealized: 0}}, "account panel: balance/equity/portfolio read by label");
  const acctpB = await onHost(`<!doctype html><html><body><button>Portfolio 10,000.00 USD</button><button>Turbo Eval 5</button></body></html>`);
  eq(await acctpB.evaluate("__ex.accountPanel()"), {balance: null, equity: null, portfolio: 10000, unrealized: null, n: {balance: 0, equity: 0, portfolio: 1, unrealized: 0}}, "account panel: only Portfolio shown -> balance/equity absent (null), portfolio read");
  const acctpC = await onHost(`<!doctype html><html><body><button>Portfolio 1.00 USD</button><span>Portfolio 2.00 USD</span><button>Balance</button></body></html>`);
  eq(await acctpC.evaluate("__ex.accountPanel()"), {balance: null, equity: null, portfolio: null, unrealized: null, n: {balance: 0, equity: 0, portfolio: 2, unrealized: 0}}, "account panel: two different values or no value -> null, never guessed");
  eq(await acctpC.evaluate("window.__submitted || 0"), 0, "account panel: read-only");

  // Fixture H (SOL-PICKER, MEASURED 2026-10-06 dry-fill dumps): the real "Select market" chip (div role=button,
  // aria-label "Select market", text "ETH 20x"), watchlist buttons WITH a price ("SOL 150.23 -1.23%"), the ticket open on
  // ETH. The list the chip opens is UNMEASURED: H = a role=dialog of plain div rows (handler on the row, symbol in a
  // leaf span, a "SOLV" decoy); I = the same rows with NO popup role, SOL first (found as "appeared after the click");
  // J = the chip opens nothing -> watchlist fallback; K = two SOL rows -> ambiguous, nothing picked, watchlist fallback.
  const market = (mode) => `<!doctype html><html><body>
<div role=button id=chip aria-label="Select market">ETH 20x</div><button>Order form</button><button>Open orders</button><button>Positions</button>
<div id=wl><button data-s=BTC>BTC 62,410.5 -1.20%</button><button data-s=ETH>ETH 2,612.40 -0.50%</button><button data-s=SOL>SOL 150.23 -1.23%</button></div>
<div id=list></div>
<form id=f><div role=tablist><button type=button role=tab aria-selected=false>Market</button><button type=button role=tab aria-selected=true>Limit</button></div>
<div role=tablist><button type=button role=tab aria-selected=true>Buy</button><button type=button role=tab aria-selected=false>Sell</button></div>
<label for=lp>Limit price</label><input id=lp type=text><label for=q>Quantity</label><input id=q type=text>
<button type=button aria-label="Toggle quantity unit">USD</button><button type=submit id=sub>Long (buy) ETH</button></form>
<script>var MODE='${mode}';var LEV={BTC:50,ETH:20,SOL:10,SOLV:5,XRP:5};
function pick(s){document.getElementById('chip').textContent=s+' '+LEV[s]+'x';document.getElementById('sub').textContent='Long (buy) '+s;close();}
function close(){document.getElementById('list').innerHTML='';}
function row(s,name){var d=document.createElement('div');d.className='r';d.innerHTML='<span>'+s+'</span><span>'+name+'</span>';d.onclick=function(){window.__picks=(window.__picks||0)+1;pick(s.split(/[-\/]/)[0])};return d;}
document.getElementById('chip').onclick=function(){if(MODE==='J')return;var l=document.getElementById('list');var box=l;
 if(MODE!=='I'){box=document.createElement('div');box.setAttribute('role','dialog');l.appendChild(box);}
 var rs=MODE==='I'?[['SOL','Solana'],['BTC','Bitcoin'],['ETH','Ethereum']]:[['BTC','Bitcoin'],['ETH','Ethereum'],['SOLV','Solv'],['SOL','Solana'],['XRP','Ripple']];
 if(MODE==='K')rs.push(['SOL-PERP','Solana perp']);
 rs.forEach(function(r){box.appendChild(row(r[0],r[1]))});};
document.addEventListener('keydown',function(e){if(e.key==='Escape')close();});
document.querySelectorAll('#wl button').forEach(function(b){b.onclick=function(){window.__wl=(window.__wl||0)+1;pick(b.getAttribute('data-s'));};});
document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};</script></body></html>`;
  for (const [mode, route] of [["H", ["opened_market", "picked_market"]], ["I", ["opened_market", "picked_market"]],
    ["J", ["opened_market", "market_no_row", "clicked_watch"]], ["K", ["opened_market", "market_ambiguous", "clicked_watch"]]]) {
    const pM = await onHost(market(mode)); const rM = (code) => pM.evaluate(code);
    eq([await rM("__ex.marketShown()"), await rM("__ex.symbolShown()")], ["ETH", "ETH"], `market ${mode}: chip 'ETH 20x' read as ETH, ticket on ETH`);
    eq(await rM("__ex.symbolReset()"), "ok", `market ${mode}: picker state reset per ticket`);
    const got = []; for (let i = 0; i < route.length; i++) got.push(await rM("__ex.symbolStep('SOLUSD')"));
    eq(got, route, `market ${mode}: route ${route.join(">")}`);
    eq(await rM("__ex.symbolStep('SOLUSD')"), "done", `market ${mode}: then done`);
    eq([await rM("__ex.symbolOnTicket()"), await rM("__ex.marketShown()")], ["SOL", "SOL"], `market ${mode}: submit label AND chip name SOL`);
    eq([await rM("window.__picks || 0"), await rM("window.__wl || 0")], mode === "J" || mode === "K" ? [0, 1] : [1, 0], `market ${mode}: exactly one selection click (${mode === "J" || mode === "K" ? "watchlist" : "picker row"})`);
    eq(await rM("window.__submitted || 0"), 0, `market ${mode}: nothing submitted`);
    eq(await rM("[__ex.symbolStep('ETHUSD'), __ex.symbolStep('ETHUSD')]"), ["opened_market", mode === "J" ? "market_no_row" : "picked_market"], `market ${mode}: and back to ETH from SOL`);
  }
  // PHONE-FGS-EXEC step 2 (MEASURED 1b.233 10:48Z, phone-test-60715df76a4d: from a SOL page the ETH pick saw 2 rows and
  // refused market_ambiguous). L = the open position's row ("ETH 1" in the Positions div grid) sits inside the same
  // popup root as the picker's plain "ETH Ethereum" row: the Positions panel row is excluded, the market row picked.
  // P = two ETH rows in the picker, only one in the picker's own "<BASE> <lev>x" shape: that one is picked.
  const fgs = (mode) => `<!doctype html><html><body>
<div role=button id=chip aria-label="Select market">SOL 10x</div><button>Order form</button><button>Open orders</button><button>Positions1</button>
<div role=dialog id=dlg>
<div id=pos><div class=hdr><div>Market</div><div>UP&L</div><div>Side</div><div>Value</div><div>Opening price</div><div>Current price</div></div>
<div class=row><div class=pr><span>ETH 1</span></div><div>Sum 50.49USD</div><div></div><div>Sum 1,278.70USD</div><div>2,461.52USD</div><div>2,368.01USD</div></div></div>
<div id=list></div></div>
<form id=f><div role=tablist><button type=button role=tab aria-selected=false>Market</button><button type=button role=tab aria-selected=true>Limit</button></div>
<label for=lp>Limit price</label><input id=lp type=text><label for=q>Quantity</label><input id=q type=text>
<button type=submit id=sub>Short (sell) SOL</button></form>
<script>var MODE='${mode}';
function pick(s){document.getElementById('chip').textContent=s+' 20x';document.getElementById('sub').textContent='Long (buy) '+s;document.getElementById('list').innerHTML='';}
document.querySelector('.pr').onclick=function(){window.__posclick=(window.__posclick||0)+1;};
function row(txt,s){var d=document.createElement('div');d.className='r';d.innerHTML='<span>'+txt+'</span>';d.onclick=function(){window.__picks=(window.__picks||0)+1;pick(s)};return d;}
document.getElementById('chip').onclick=function(){var l=document.getElementById('list');
 var rs=MODE==='L'?[['BTC Bitcoin','BTC'],['ETH Ethereum','ETH']]:[['ETH','ETH'],['ETH 20x - 2,368.01USD -1.20%','ETH'],['BTC 50x - 62,410.5USD -1.20%','BTC']];
 rs.forEach(function(r){l.appendChild(row(r[0],r[1]))});};
document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};</script></body></html>`;
  for (const mode of ["L", "P"]) {
    const pF = await onHost(fgs(mode)); const rF = (code) => pF.evaluate(code);
    eq(await rF("__ex.symbolReset()"), "ok", `fgs ${mode}: reset`);
    eq(await rF("__ex.escape()"), "ok", `fgs ${mode}: escape (keys only)`);
    eq(await rF("[__ex.symbolStep('ETHUSD'), __ex.symbolStep('ETHUSD'), __ex.symbolStep('ETHUSD')]"), ["opened_market", "picked_market", "done"], `fgs ${mode}: SOL -> ETH picked, not ambiguous`);
    eq([await rF("__ex.symbolOnTicket()"), await rF("window.__picks || 0"), await rF("window.__posclick || 0"), await rF("window.__submitted || 0")],
      ["ETH", 1, 0, 0], `fgs ${mode}: one picker click, the Positions row never clicked, nothing submitted`);
  }
  // PHONE-FGS-EXEC step 2 (manager 11:03Z): TP/SL lookups never leave the order form. Q = no ticket form on the page,
  // a Positions grid with a "TP / SL" column and an "Edit Take profit" dialog (Cancel / Confirm) open: openTpsl finds
  // nothing to click, the grid's TP / SL cells are never clicked, cancelModals clicks Cancel and never Confirm.
  const tpq = `<!doctype html><html><body><button>Positions1</button>
<div id=pos><div class=hdr><div>Market</div><div>UP&L</div><div>Side</div><div>Value</div><div>Opening price</div><div>Current price</div><div id=th>TP / SL</div></div>
<div class=row><div>ETH 1</div><div>50.49USD</div><div></div><div>1,278.70USD</div><div>2,461.52USD</div><div>2,368.01USD</div><div id=tc>TP / SL</div></div></div>
<div role=dialog id=dlg><h2>Edit Take profit</h2><label><input type=checkbox>TP/SL</label><button id=cn>Cancel</button><button id=cf>Confirm</button></div>
<script>['th','tc'].forEach(function(i){document.getElementById(i).onclick=function(){window.__tpcell=(window.__tpcell||0)+1;};});
document.getElementById('cn').onclick=function(){window.__cancel=(window.__cancel||0)+1;document.getElementById('dlg').remove();};
document.getElementById('cf').onclick=function(){window.__confirm=(window.__confirm||0)+1;};</script></body></html>`;
  const pTq = await onHost(tpq); const rQ = (code) => pTq.evaluate(code);
  eq([await rQ("__ex.openTpsl(0)"), await rQ("__ex.openTpsl(1)")], ["none", "none"], "tpsl Q: no order form -> nothing found, nothing clicked");
  eq(await rQ("window.__tpcell || 0"), 0, "tpsl Q: the Positions 'TP / SL' header and cell are never clicked");
  eq(await rQ("__ex.cancelModals()"), 1, "tpsl Q: the Edit Take profit dialog is cancelled");
  eq([await rQ("window.__cancel || 0"), await rQ("window.__confirm || 0"), await rQ("__ex.cancelModals()")], [1, 0, 0], "tpsl Q: Cancel clicked once, Confirm never; nothing left to cancel");
  // PHONE-FGS-EXEC (12:56Z, account_status stale since 11:38): a finished ticket left the order form open on the engine
  // page, so terminal().ticketOpen stayed true and readAccount read "busy". closeTicket clicks the ONE "Order form"
  // toggle outside the form (never the submit, never inside the form); terminal() then reads the form closed.
  const idleForm = `<!doctype html><html><body><button id=tg>Order form</button><button>Open orders</button><button>Positions1</button>
<form id=f><div role=tablist><button type=button role=tab aria-selected=false>Market</button><button type=button role=tab aria-selected=true>Limit</button></div>
<label for=lp>Limit price</label><input id=lp type=text value="2,295.99"><label for=q>Quantity</label><input id=q type=text value="0.01">
<button type=submit id=sub>Long (buy) ETH</button></form>
<script>document.getElementById('tg').onclick=function(){window.__toggle=(window.__toggle||0)+1;var f=document.getElementById('f');f.style.display=f.style.display==='none'?'':'none';};
document.getElementById('sub').onclick=function(){window.__subclick=(window.__subclick||0)+1;};
document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};</script></body></html>`;
  const pI = await onHost(idleForm); const rI = (code) => pI.evaluate(code);
  eq(await rI("__ex.terminal().ticketOpen"), true, "idle form: the left-open order form reads as a ticket");
  eq(await rI("__ex.closeTicket()"), "clicked", "idle form: its panel toggle is clicked");
  eq([await rI("__ex.terminal().ticketOpen"), await rI("__ex.terminal().ready"), await rI("__ex.closeTicket()")], [false, true, "closed"], "idle form: closed, terminal still ready, nothing left to close");
  eq([await rI("window.__toggle || 0"), await rI("window.__subclick || 0"), await rI("window.__submitted || 0")], [1, 0, 0], "idle form: one toggle click, the submit never clicked, nothing submitted");
  // manager 13:03Z: never while a stray modal is open, never a toggle inside one. An Edit Take profit dialog holding its
  // own "Order form" button is open: closeTicket refuses (modal_open, nothing clicked); dismissModals cancels it by the
  // shared rule, then only the panel tab is clicked.
  const pIm = await onHost(idleForm.replace('<form id=f>', '<div role=dialog id=dm><h3>Edit Take profit</h3><button id=dmo>Order form</button><button id=dmc>Cancel</button><button id=dmf>Confirm</button></div><form id=f>')
    .replace('</script>', "['dmo','dmf'].forEach(function(i){document.getElementById(i).onclick=function(){window.__dlgclick=(window.__dlgclick||0)+1;};});document.getElementById('dmc').onclick=function(){window.__cancel=(window.__cancel||0)+1;document.getElementById('dm').remove();};</script>"));
  const rIm = (code) => pIm.evaluate(code);
  eq([await rIm("__ex.closeTicket()"), await rIm("window.__toggle || 0"), await rIm("window.__dlgclick || 0")], ["modal_open", 0, 0], "idle form + modal: refused, nothing clicked");
  eq([(await rIm("__ex.dismissModals()")).cancelled, await rIm("__ex.closeTicket()")], [1, "clicked"], "idle form + modal: Cancel first, then the panel toggle");
  eq([await rIm("window.__cancel || 0"), await rIm("window.__dlgclick || 0"), await rIm("window.__toggle || 0"), await rIm("window.__subclick || 0"), await rIm("__ex.terminal().ticketOpen")],
    [1, 0, 1, 0, false], "idle form + modal: the dialog's Order form / Confirm never clicked, the submit never clicked, form closed");
  // PHONE-FGS-EXEC (manager 12:47Z): the 10:48Z refusal's DOM shape (phone-test-60715df76a4d dump). Ticket on SOL, the
  // "Select market" picker in its own popup, AND an "Edit Take profit" dialog already open with the position's "ETH"
  // label, a "Take profit Quantity" and a "Distance for Trigger" input, Cancel / Confirm. That dialog's "ETH" is the
  // second picker candidate 1b.233 saw (market_ambiguous). Nothing in the dialog may be clicked by the symbol route; the
  // step-2 reset cancels it (Cancel once, Confirm never) and the pick then lands on the one market row.
  const r1048 = `<!doctype html><html><body>
<div role=button id=chip aria-label="Select market">SOL 20x</div><button>Order form</button><button>Open orders1</button><button>Positions1</button>
<div role=dialog id=pk style="display:none"><input type=search role=searchbox placeholder=Search><button>All</button><div id=list></div></div>
<div role=dialog id=tp><h3>Edit Take profit</h3><div>ETH</div><input aria-label="Take profit Quantity" type=text><input type=text><input aria-label="Distance for Trigger" type=text>
<label><input type=checkbox>TP/SL</label><button id=cn>Cancel</button><button id=cf>Confirm</button></div>
<form id=f><div role=tablist><button type=button role=tab aria-selected=false>Market</button><button type=button role=tab aria-selected=true>Limit</button></div>
<label for=lp>Limit price</label><input id=lp type=text value="102.1"><label for=q>Quantity</label><input id=q type=text value="1.00">
<button type=submit id=sub>Short (sell) SOL</button></form>
<script>
document.getElementById('tp').addEventListener('click',function(e){if(e.target.id!=='cn')window.__tpclick=(window.__tpclick||0)+1;},true);
document.getElementById('cn').onclick=function(){window.__cancel=(window.__cancel||0)+1;document.getElementById('tp').remove();};
document.getElementById('cf').onclick=function(){window.__confirm=(window.__confirm||0)+1;};
function pick(s){document.getElementById('chip').textContent=s+' 20x';document.getElementById('sub').textContent='Long (buy) '+s;document.getElementById('pk').style.display='none';}
function row(txt,s){var d=document.createElement('div');d.innerHTML='<span>'+txt+'</span>';d.onclick=function(){window.__picks=(window.__picks||0)+1;pick(s)};return d;}
document.getElementById('chip').onclick=function(){var l=document.getElementById('list');l.innerHTML='';document.getElementById('pk').style.display='block';
 [['ETH 20x - 2,369.47USD -1.20%','ETH'],['BTC 50x - 62,410.5USD -1.20%','BTC']].forEach(function(r){l.appendChild(row(r[0],r[1]))});};
document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};</script></body></html>`;
  // without the reset (the 1b.233 entry): the dialog's "ETH" is a candidate, but only the picker row has the market shape
  const pW0 = await onHost(r1048); const rW0 = (code) => pW0.evaluate(code);
  eq(await rW0("[__ex.symbolReset(), __ex.symbolStep('ETHUSD'), __ex.symbolStep('ETHUSD')]"), ["ok", "opened_market", "picked_market"], "10:48 shape: the picker row is picked, not the dialog's ETH label");
  eq([await rW0("window.__tpclick || 0"), await rW0("window.__confirm || 0"), await rW0("window.__cancel || 0"), await rW0("window.__picks || 0")], [0, 0, 0, 1], "10:48 shape: the symbol route clicks nothing in the Edit Take profit dialog");
  // the step-2 background order: cancelModals, escape, then the symbol route
  const pW = await onHost(r1048); const rW = (code) => pW.evaluate(code);
  eq(await rW("[__ex.cancelModals(), __ex.escape(), __ex.symbolReset()]"), [1, "ok", "ok"], "10:48 reset: the Edit Take profit dialog is cancelled");
  eq(await rW("[__ex.symbolStep('ETHUSD'), __ex.symbolStep('ETHUSD'), __ex.symbolStep('ETHUSD')]"), ["opened_market", "picked_market", "done"], "10:48 reset: SOL -> ETH picked");
  eq([await rW("window.__cancel || 0"), await rW("window.__confirm || 0"), await rW("window.__tpclick || 0"), await rW("window.__submitted || 0")],
    [1, 0, 0, 0], "10:48 reset: Cancel once, Confirm never, no other dialog click, nothing submitted");
  // SERVER READ-BACK (PHONE-GO-TOKEN, design 3.4): the app builds the read-back from these page reads (MainActivity.kt
  // readBack: SELECTED tabs, the submit label + disabled state, the TP/SL box, the by-label values) and hashes
  // "key=value" lines in READBACK_FIELDS order (== src/prop/phone_executor.py). Mirrors the app; proves the page reads
  // it depends on, and that any change to the form between verify and click changes the hash (no click).
  const crypto = require("crypto");
  const FIELDS = ["ticket_id", "symbol", "side", "order_type", "price", "qty", "qty_unit", "tp", "sl", "submit_label", "submit_disabled", "tpsl"];
  await r("__ex.setByLabel('take ?profit', 'price', '2600.00')"); await r("__ex.setByLabel('stop ?loss', 'price', '2450.00')");
  const readBack = async () => {
    const tk = await r("__ex.ticket()");
    const sel = (re) => { const h = tk.tabs.filter((x) => x.selected && re.test(x.text.trim())); return h.length === 1 ? h[0].text.trim() : ""; };
    const val = async (re, pref) => ((await r(`__ex.readByLabel(${JSON.stringify(re)}, ${JSON.stringify(pref)})`)).value || "").trim();
    return {ticket_id: "t1", symbol: tk.symbol.trim(), side: sel(/^(buy|sell)$/i), order_type: sel(/^(market|limit|trigger|stop)$/i),
      price: await val("limit price", "limit price"), qty: await val("quantity", "quantity"), qty_unit: "ETH",
      tp: await val("take ?profit|\\btp\\b", "price"), sl: await val("stop ?loss|\\bsl\\b", "price"),
      submit_label: tk.submit.text.trim(), submit_disabled: String(tk.submit.disabled), tpsl: tk.tpsl === null ? "" : String(tk.tpsl)};
  };
  const hash = (rb) => crypto.createHash("sha256").update(FIELDS.map((k) => k + "=" + String(rb[k]).trim()).join("\n")).digest("hex");
  const rb1 = await readBack();
  eq([rb1.symbol, rb1.side, rb1.order_type, rb1.price, rb1.qty, rb1.tp, rb1.sl, rb1.submit_label, rb1.submit_disabled, rb1.tpsl],
    ["ETHUSD", "Buy", "Limit", "2500.10", "0.01", "2600.00", "2450.00", "Long (buy) ETHUSD", "false", "true"], "read-back: what the page shows");
  eq(hash(await readBack()), hash(rb1), "re-read of an unchanged form hashes the same (go allowed)");
  await r("__ex.setByLabel('quantity', '', '0.02')");
  eq(hash(await readBack()) === hash(rb1), false, "a changed quantity changes the hash (no click)");
  await r("__ex.setByLabel('quantity', '', '0.01')"); await r("__ex.tab('Sell')");
  const rbS = await readBack();
  eq([rbS.side, /short/i.test(rbS.submit_label), hash(rbS) === hash(rb1)], ["Sell", true, false], "a flipped side changes side, label and hash");
  await r("__ex.tab('Buy')");
  eq(await r("window.__submitted || 0"), 0, "nothing submitted by the read-back path");

  // LOGIN LINEUP (PHONE-AUTOLOGIN-2, operator 2026-10-10: "choose the correct 2 digit number from a line up of 3").
  // Synthetic shapes, not Breakout's real page: the mechanics of reading the choices and tapping exactly one.
  const lp = await b.newPage();
  await lp.route("https://app.breakoutprop.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body:
    "<!doctype html><html><body>" + ({
      "/lineup": "<p>Check your email and select the matching number</p><div style='font-size:12px'>Step 2</div>" +
        "<div id=c><button style='font-size:32px' onclick='window.__picked=\"a\"'><span>47</span></button>" +
        "<button style='font-size:32px' onclick='window.__picked=\"b\"'><span>12</span></button>" +
        "<button style='font-size:32px' onclick='window.__picked=\"c\"'><span>83</span></button></div>",
      "/single": "<p>Check your email and tap the number</p><h1 style='font-size:48px'>47</h1><p style='font-size:10px'>Expires in 10</p>",
      "/dup": "<p>select the number</p><button>47</button><button>47</button><button>12</button>",
      "/box": "<p>select the number</p><div style='cursor:pointer' onclick='window.__picked=\"box\"'><span>47</span><span>12</span><span>83</span></div>",
    }[new URL(route.request().url()).pathname] || "") + "</body></html>" }));
  const lg = async (path) => { await lp.goto("https://app.breakoutprop.com" + path); await lp.addScriptTag({ content: src }); };
  await lg("/lineup");
  let ch = await lp.evaluate("__ex.loginChoices()");
  eq([ch.n, ch.top.sort(), ch.btn.sort()], [3, ["12", "47", "83"], ["12", "47", "83"]], "lineup: three tappable choices at the top size");
  eq(await lp.evaluate("__ex.state().codeWait"), true, "lineup page reads as the code step");
  eq(await lp.evaluate("__ex.loginNumber()"), "", "lineup: no SINGLE number (the 12:30Z/12:40Z failure shape)");
  eq(await lp.evaluate("__ex.loginPick('12')"), "clicked", "lineup: the matching choice is tapped");
  eq(await lp.evaluate("window.__picked"), "b", "lineup: exactly the matching control was tapped");
  eq(await lp.evaluate("__ex.loginPick('99')"), "none", "lineup: a number not on the page taps nothing");
  await lg("/single");
  ch = await lp.evaluate("__ex.loginChoices()");
  eq([ch.top, ch.btn], [["47"], []], "single-number page: one top number, no tappable lineup");
  eq(await lp.evaluate("__ex.loginNumber()"), "47", "single-number page: loginNumber still reads it");
  await lg("/dup");
  eq(await lp.evaluate("__ex.loginPick('47')"), "ambiguous", "two controls with the same number: nothing tapped");
  await lg("/box");
  eq(await lp.evaluate("__ex.loginChoices().btn"), [], "one clickable box holding all three numbers is not a lineup control");

  // PHONE-AUTOLOGIN-3 (live 2026-10-10 13:52Z/13:56Z: page_n=0 for 90 s on portal.breakoutprop.com). One synthetic
  // page per hypothesis; each must now be READ (or, for (c), described) instead of reading as zero.
  const lp3 = await b.newPage();
  await lp3.route("https://portal.breakoutprop.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body:
    "<!doctype html><html><body>" + ({
      // (b) numbers with an icon child, split digit spans, zero-width characters
      "/icons": "<p>Select the matching number</p>" +
        "<button onclick='window.__picked=\"a\"'>47<svg width=8 height=8></svg></button>" +
        "<button onclick='window.__picked=\"b\"'><span>1</span><span>2</span></button>" +
        "<button onclick='window.__picked=\"c\"'>​83​</button>",
      // (b) radios whose number is only in value / aria-label, with a separate text label
      "/radios": "<p>Choose the number from your email</p>" +
        "<label><input type=radio name=n value=47 onclick='window.__picked=\"a\"'><span>47</span></label>" +
        "<label><input type=radio name=n value=12 onclick='window.__picked=\"b\"'><span>12</span></label>" +
        "<label><input type=radio name=n value=83 onclick='window.__picked=\"c\"'><span>83</span></label>",
      "/aria": "<p>select</p><div role=button aria-label=47 style='width:20px;height:20px' onclick='window.__picked=\"a\"'></div>" +
        "<div role=button aria-label=12 style='width:20px;height:20px' onclick='window.__picked=\"b\"'></div>" +
        "<div role=button aria-label=83 style='width:20px;height:20px' onclick='window.__picked=\"c\"'></div>",
      // (b) a shadow root and a same-origin iframe
      "/shadow": "<p>Select the number</p><x-pick id=h></x-pick><script>var r=document.getElementById('h').attachShadow({mode:'open'});" +
        "r.innerHTML='<button onclick=\"window.__picked=1\">47</button><button onclick=\"window.__picked=2\">12</button><button onclick=\"window.__picked=3\">83</button>';</script>",
      "/frame": "<p>Select the number</p><iframe src='/inner' style='width:300px;height:200px'></iframe>",
      "/inner": "<button onclick='parent.__picked=\"f\"'>47</button><button onclick='parent.__picked=\"g\"'>12</button><button>83</button>",
      // (c) the page only says "check your email": nothing numeric to read, the structure must say so
      "/check": "<h1>Check your email</h1><p>We sent a sign-in link to b***@example.com. It expires in 15 minutes.</p>" +
        "<a href='https://portal.breakoutprop.com/help/42'>Need help?</a><button>Resend email</button>",
      // (a) an email form whose FIRST matching button is a social login; the real submit is in the form
      "/email": "<button onclick='window.__social=1'>Continue with Google</button><form id=f onsubmit='event.preventDefault()'>" +
        "<input type=email><button type=submit onclick='window.__sent=1'>Continue</button></form>",
      // (a) a form with NO button: only Enter submits it
      "/enter": "<form id=f onsubmit='event.preventDefault();window.__sent=1'><input type=email></form>",
    }[new URL(route.request().url()).pathname] || "") + "</body></html>" }));
  const lg3 = async (path) => { await lp3.goto("https://portal.breakoutprop.com" + path); await lp3.waitForTimeout(150); await lp3.addScriptTag({ content: src }); };
  for (const [path, want, pick] of [["/icons", ["12", "47", "83"], "b"], ["/radios", ["12", "47", "83"], "b"], ["/aria", ["12", "47", "83"], "b"],
    ["/shadow", ["12", "47", "83"], 2]]) {
    await lg3(path);
    ch = await lp3.evaluate("__ex.loginChoices()");
    eq(ch.btn.sort(), want, `${path}: three tappable choices read`);
    eq(await lp3.evaluate("__ex.loginPick('12')"), "clicked", `${path}: the one matching choice tapped`);
    eq(await lp3.evaluate("window.__picked"), pick, `${path}: exactly that control`);
  }
  await lg3("/frame");
  eq((await lp3.evaluate("__ex.loginChoices()")).btn.sort(), ["12", "47", "83"], "/frame: choices read inside a same-origin iframe");
  eq(await lp3.evaluate("__ex.loginPick('47')"), "clicked", "/frame: tapped inside the iframe");
  eq(await lp3.evaluate("window.__picked"), "f", "/frame: exactly that control");
  let pr = await lp3.evaluate("__ex.loginProbe()");
  eq([pr.frames, pr.frames_xo], [1, 0], "/frame: probe counts one readable iframe");
  await lg3("/check");
  eq((await lp3.evaluate("__ex.loginChoices()")).n, 0, "/check: nothing numeric on a check-your-email page");
  pr = await lp3.evaluate("__ex.loginProbe()");
  eq([pr.phrases.includes("check_email"), pr.phrases.includes("we_sent"), pr.cands], [true, true, 0], "/check: probe names the shape");
  eq(/\d|@|example|http/.test(JSON.stringify(pr.texts)), false, "/check: probe texts carry no digit, email or link");
  eq(pr.texts.includes("Check your email"), true, "/check: heading text kept (scrubbed)");
  eq(pr.numtok > 0 && typeof pr.sig === "string", true, "/check: numeric tokens COUNTED, sig present");
  await lg3("/email");
  const sig0 = (await lp3.evaluate("__ex.loginProbe()")).sig;
  eq(await lp3.evaluate("__ex.loginEmail('a@b.co')"), "clicked", "/email: submitted");
  eq(await lp3.evaluate("[window.__social || 0, window.__sent || 0]"), [0, 1], "/email: the form's Continue, never the social button");
  eq((await lp3.evaluate("__ex.loginSubmitInfo()")).where, "form", "/email: submit info says form");
  await lp3.evaluate("document.body.insertAdjacentHTML('beforeend', '<p>Check your email</p>')");
  eq((await lp3.evaluate("__ex.loginProbe()")).sig !== sig0, true, "/email: sig changes when the page advances");
  await lg3("/enter");
  eq(await lp3.evaluate("__ex.loginEmail('a@b.co')"), "submitted", "/enter: no button -> form submitted");
  eq(await lp3.evaluate("__ex.loginEnter()"), "enter+submit", "/enter: Enter path available");
  eq(await lp3.evaluate("window.__sent"), 1, "/enter: form submitted");
  // regression: the original lineup still reads exactly as before under the broadened reader
  await lg("/lineup");
  eq((await lp.evaluate("__ex.loginChoices()")).n, 3, "lineup: still exactly three candidates");
  // POSITIONS + CLOSED ORDERS READ (PROP-TRACKING, 2026-10-12). SYNTHETIC tables in the shape the 2026-10-12 operator
  // screenshot described ("ETH 5x ... uPnL +31.27 USD"); they prove the mapping and the fail-closed states, NOT
  // Breakout's real headers (the first live read measures those; a miss posts the masked headers).
  const posTable = (tab, rows, extra) => `<!doctype html><html><head><meta charset=utf-8></head><body><div role=tablist><button role=tab>${tab}</button><button role=tab>Open orders</button><button role=tab>Closed orders</button></div>
<div id=wrap><table><thead><tr><th>Symbol</th><th>Size</th><th>Entry price</th><th>Mark price</th><th>Liq. price</th><th>Unrealized PnL</th><th>TP/SL</th></tr></thead>
<tbody>${rows}</tbody></table>${extra || ""}</div></body></html>`;
  const ROWS2 = "<tr><td>ETH 5x Long</td><td>1.1758 ETH</td><td>2,527.80</td><td>2,554.40</td><td>2,100.00</td><td>+31.27 USD (1.05%)</td><td>2655.4 / 2506.5</td></tr>" +
    "<tr><td>SOL 5x Long</td><td>17.26 SOL</td><td>111.16</td><td>112.30</td><td>90.10</td><td>+19.67 USD</td><td>-</td></tr>";
  const qP = await onHost(posTable("Positions (2)", ROWS2));
  let qpr2 = await qP.evaluate("__ex.positionsRead()");
  eq(qpr2.state, "read", "positions: two rows read");
  eq(qpr2.rows.map((r) => [r.symbol, r.side, r.qty, r.entry, r.mark, r.upnl]),
    [["ETH", "long", 1.1758, 2527.8, 2554.4, 31.27], ["SOL", "long", 17.26, 111.16, 112.3, 19.67]], "positions: symbol/side/qty/entry/mark/uPnL mapped by header");
  eq(qpr2.count, 2, "positions: the tab's own count read");
  eq(Object.keys(qpr2.rows[0]).includes("liq"), false, "positions: liquidation price not read as anything");
  const qP2 = await onHost(posTable("Positions (3)", ROWS2));
  eq((await qP2.evaluate("__ex.positionsRead()")).state, "count_mismatch", "positions: tab count 3 vs 2 rows -> not read");
  const qP3 = await onHost(posTable("Positions", "", ""));
  eq((await qP3.evaluate("__ex.positionsRead()")).state, "empty_unconfirmed", "positions: header with no rows and no empty text is NOT flat");
  const qP4 = await onHost(posTable("Positions", "<tr><td colspan=7>No open positions</td></tr>"));
  let e4 = await qP4.evaluate("__ex.positionsRead()");
  eq([e4.state, e4.rows], ["read", []], "positions: explicit 'No open positions' row reads as flat");
  const qP5 = await onHost(posTable("Positions", "<tr><td>ETH</td><td>1.17</td><td>2527.8</td><td>2554</td><td>2100</td><td>31</td><td>-</td></tr>"));
  let p5 = await qP5.evaluate("__ex.positionsRead()");
  eq([p5.state, p5.rows[0].side], ["side_unread", null], "positions: a row with no readable side is side_unread with side null, never guessed");
  const qP6 = await onHost(`<!doctype html><html><body><div role=tablist><button role=tab>Positions</button></div><div class=card>ETH 5x uPnL +31.27</div></body></html>`);
  let e6 = await qP6.evaluate("__ex.positionsRead()");
  eq([e6.state, e6.controls.includes("Positions")], ["no_table", true], "positions: card layout (no table) -> no_table with the masked controls dump");
  eq(/31/.test(JSON.stringify(e6)), false, "positions miss dump carries no value");
  // PHONE-POSITIONS-READ-CONTEXT: chips counted/clicked incl. plain-div chips with a count badge; miss dump says where we are
  const qC1 = await onHost(`<!doctype html><html><body><div class=bar><div class=chip>Order form</div><div class=chip>Positions 1</div></div><div>Balance 4,930.42 USD</div></body></html>`);
  eq((await qC1.evaluate("__ex.tabChips()")).chips, 1, "tabChips: a plain-div 'Positions 1' chip is counted");
  eq(await qC1.evaluate("__ex.clickTab('^positions\\\\b')"), "clicked", "clickTab: clicks the plain-div chip clickText cannot see");
  eq(await qC1.evaluate("__ex.clickText('^positions\\\\b')"), "none", "clickText alone misses it (why clickTab exists)");
  const qC2 = await onHost(`<!doctype html><html><body><h1>Loading</h1><button>Menu</button><p>Balance 4,930.42 USD</p></body></html>`);
  const md = await qC2.evaluate("__ex.missDump()");
  eq([md.chips, typeof md.trade, typeof md.path_depth, md.controls.includes("Menu"), md.headers.includes("Loading")], [0, "boolean", "number", true, true], "missDump: chips 0, path info, controls and leaf texts present");
  eq(/4,930|930/.test(JSON.stringify(md)), false, "missDump: digits masked, no balance value");
  eq(await qC2.evaluate("__ex.clickTab('^positions\\\\b')"), "none", "clickTab: no chip -> none (no_tab)");
  // BREAKOUT-UNTRACKED-1014: with a position open the count badge is a child span GLUED to the label (innerText
  // "Positions1"): every Positions read was no_tab for 9 h. The badge is dropped for matching, and is the tab's count.
  const qC3 = await onHost(`<!doctype html><html><body><div class=bar><button>Open orders<span>2</span></button><button>Positions<span>1</span></button><button>Closed orders</button><button>Trades<span>3</span></button></div></body></html>`);
  eq(await qC3.evaluate("document.querySelectorAll('button')[1].innerText"), "Positions1", "glued badge: innerText has no space");
  eq(await qC3.evaluate("__ex.clickTab('^positions\\\\b')"), "clicked", "glued badge: clickTab finds 'Positions1'");
  eq(await qC3.evaluate("__ex.clickText('^positions$')"), "clicked", "glued badge: the submit-confirm / amend exact match finds it");
  eq(await qC3.evaluate("__ex.clickText('^open orders$')"), "clicked", "glued badge: 'Open orders2' found by the exact match");
  eq(await qC3.evaluate("__ex.clickTab('^(closed orders|trades)\\\\b')"), "clicked", "glued badge: closed/trades still found");
  eq(await qC3.evaluate("__ex.terminal().panels"), true, "glued badge: the panel-layout terminal gate still recognises the chips");
  eq(await qC3.evaluate("__ex.clickText('^position$')"), "none", "badge drop does not loosen the label itself");
  const qC4 = await onHost(`<!doctype html><html><body><button>Positions<span>1</span></button><button>Positionsx</button></body></html>`);
  eq(await qC4.evaluate("__ex.clickText('^positions$')"), "clicked", "exact match: only the badged chip qualifies");
  const qP7 = await onHost(posTable("Positions", "<tr><td>ETHUSD</td><td>-1.5</td><td>2600</td><td>2550</td><td>3000</td><td>−75.00</td><td>-</td></tr>", "<p>x</p>"));
  let e7 = await qP7.evaluate("__ex.positionsRead()");
  eq([e7.state, e7.rows[0].side, e7.rows[0].qty, e7.rows[0].upnl], ["read", "short", 1.5, -75], "positions: signed size gives the side; unicode minus uPnL");
  // REVIEW OF #17745 (blocking 1): "no positions" text ANYWHERE on the page is not a flat read; only the tab's own
  // count of 0 or the ONE visible tab panel saying so.
  const qF1 = await onHost(`<!doctype html><html><body><div role=tablist><button role=tab>Positions</button></div><p>You have no positions in your watchlist alerts</p></body></html>`);
  eq((await qF1.evaluate("__ex.positionsRead()")).state, "no_table", "flat refused: page-wide 'no positions' text is not the Positions panel");
  const qF2 = await onHost(`<!doctype html><html><body><div role=tablist><button role=tab>Positions (0)</button></div><p>whatever</p></body></html>`);
  let f2 = await qF2.evaluate("__ex.positionsRead()");
  eq([f2.state, f2.rows], ["read", []], "flat accepted: the tab's own count is 0");
  const qF3 = await onHost(`<!doctype html><html><body><div role=tablist><button role=tab>Positions</button></div><div role=tabpanel>No open positions</div></body></html>`);
  eq((await qF3.evaluate("__ex.positionsRead()")).state, "read", "flat accepted: the one visible tab panel says no positions");
  // REVIEW OF #17745 (blocking 2): more rows than the cap are refused, never truncated (a dropped row would read as closed)
  const many = Array.from({length: 16}, (_, i) => `<tr><td>ETH Long</td><td>${i + 1}</td><td>2500</td><td>2501</td><td>2000</td><td>1</td><td>-</td></tr>`).join("");
  const qF4 = await onHost(posTable("Positions", many));
  let f4 = await qF4.evaluate("__ex.positionsRead()");
  eq([f4.state, f4.n_rows, f4.rows], ["too_many_rows", 16, undefined], "16 positions -> too_many_rows, nothing sent");
  // DIV GRID (PHONE-POSITIONS-DIV, 2026-10-12): the LIVE Positions view has no <table>: header labels Market | UP&L |
  // Side | Value | Opening price | Current price | TP / SL | Net P&L (read from the first live diag), no size column.
  // SYNTHETIC rows in that shape; the empty state "Start trading with leverage" appears while flat.
  const gridRow = (m, up, side, val, op, cur, tpsl, net) => `<div class=r><div>${m}</div><div>${up}</div><div>${side}</div><div>${val}</div><div>${op}</div><div>${cur}</div><div>${tpsl}</div><div>${net}</div></div>`;
  const gridPage = (tab, body, aside) => `<!doctype html><html><head><meta charset=utf-8></head><body>
<div id=top><span>Trade</span><div>Portfolio 4,947.72 USD</div></div>
<div id=side><div><button>Market</button><button>Limit</button></div><div>Side</div><div>Value</div>${aside || ""}</div>
<div id=bottom><div id=tabs><button>Open orders</button><button>${tab}</button><button>Portfolio</button><button>Closed orders</button><button>Trades</button></div>
<div id=panel><div id=hdr><div>Market</div><div>UP&amp;L</div><div>Side</div><div>Value</div><div>Opening price</div><div>Current price</div><div>TP / SL</div><div>Net P&amp;L</div></div>${body}</div></div></body></html>`;
  const G2 = gridRow("ETHUSD 5x", "+31.27 USD", "Long", "3,003.30 USD", "2,527.80", "2,554.40", "2655.4 / 2506.5", "+30.10 USD") +
    gridRow("SOLUSD 5x", "-4.50 USD", "Short", "1,921.00 USD", "111.16", "112.30", "-", "-5.60 USD");
  const gP = await onHost(gridPage("Positions", G2));
  let g2 = await gP.evaluate("__ex.positionsRead()");
  eq([g2.state, g2.layout, g2.n_rows], ["read", "div_grid", 2], "div grid: two positions read");
  eq(g2.rows.map((r) => [r.symbol, r.side, r.entry, r.mark, r.upnl, r.value]),
    [["ETHUSD", "long", 2527.8, 2554.4, 31.27, 3003.3], ["SOLUSD", "short", 111.16, 112.3, -4.5, 1921]], "div grid: mapped by header label (UP&L is upnl)");
  eq(g2.rows.map((r) => r.netpnl), [30.1, -5.6], "div grid: Net P&L kept apart from upnl");
  eq(g2.rows.map((r) => [r.qty, r.qty_src]), [[1.17573598, "value/mark"], [17.10596616, "value/mark"]], "div grid: qty derived from Value / Current price, labelled");
  const gP1 = await onHost(gridPage("Positions", gridRow("ETHUSD", "+1.00 USD", "Long", "2,554.40 USD", "2,527.80", "2,554.40", "-", "+0.50 USD")));
  let g1 = await gP1.evaluate("__ex.positionsRead()");
  eq([g1.state, g1.rows.length, g1.rows[0].side], ["read", 1, "long"], "div grid: one position read");
  const gE = await onHost(gridPage("Positions", "<div class=empty><svg></svg><p>Start trading with leverage</p><button>Trade</button></div>"));
  let ge = await gE.evaluate("__ex.positionsRead()");
  eq([ge.state, ge.rows], ["read", []], "div grid: empty-state INSIDE the Positions panel is flat");
  const gE2 = await onHost(gridPage("Positions", "", "<p>Start trading with leverage</p>"));
  eq((await gE2.evaluate("__ex.positionsRead()")).state, "empty_unconfirmed", "div grid: empty-state text elsewhere on the page is NOT flat");
  const gO = await onHost(gridPage("Positions", gridRow("ETHUSD 5x", "+31.27 USD", "Long", "3,003.30 USD", "2,527.80", "2,554.40", "-", "+30.10 USD"), "<p>Start trading with leverage</p>"));
  let go = await gO.evaluate("__ex.positionsRead()");
  eq([go.state, go.rows.length, go.rows[0].symbol], ["read", 1, "ETHUSD"], "div grid: 1 open position + 'Start trading' text elsewhere reads the position, not flat");
  const gO2 = await onHost(gridPage("Positions", gridRow("ETHUSD 5x", "+31.27 USD", "Long", "3,003.30 USD", "2,527.80", "2,554.40", "-", "+30.10 USD") + "<p>Start trading with leverage</p>"));
  eq((await gO2.evaluate("__ex.positionsRead()")).rows.length, 1, "div grid: a row present wins over stray empty text inside the panel");
  const gE3 = await onHost(gridPage("Positions", "", ""));
  eq((await gE3.evaluate("__ex.positionsRead()")).state, "empty_unconfirmed", "div grid: header with no rows and no empty text is NOT flat");
  const gE4 = await onHost(gridPage("Positions (0)", ""));
  let ge4 = await gE4.evaluate("__ex.positionsRead()");
  eq([ge4.state, ge4.rows], ["read", []], "div grid: the tab's own count 0 is flat");
  eq((await (await onHost(gridPage("Positions (3)", G2))).evaluate("__ex.positionsRead()")).state, "count_mismatch", "div grid: tab count 3 vs 2 rows -> not read");
  const gMany = Array.from({length: 16}, () => gridRow("ETHUSD", "+1 USD", "Long", "2,554.40 USD", "2,500", "2,554.40", "-", "+1 USD")).join("");
  let gm = await (await onHost(gridPage("Positions", gMany))).evaluate("__ex.positionsRead()");
  eq([gm.state, gm.n_rows, gm.rows], ["too_many_rows", 16, undefined], "div grid: 16 positions -> too_many_rows, nothing sent");
  const gBad = G2 + "<div class=r><div>XRPUSD 5x</div><div>+1 USD</div><div>Long</div><div>500 USD</div></div>";
  let gb = await (await onHost(gridPage("Positions", gBad))).evaluate("__ex.positionsRead()");
  eq(gb.state, "unparsed", "div grid: a row whose cells do not line up with the header is unparsed, never dropped");
  eq(/\d/.test(JSON.stringify(gb.headers)), false, "div grid: miss dump headers carry no digits");
  const gNoSide = await onHost(gridPage("Positions", gridRow("ETHUSD", "+1 USD", "-", "2,554.40 USD", "2,500", "2,554.40", "-", "+1 USD")));
  let gns = await gNoSide.evaluate("__ex.positionsRead()");
  eq([gns.state, gns.rows[0].side, gns.rows[0].symbol], ["side_unread", null, "ETHUSD"], "div grid: no readable side -> side_unread, side null, never guessed");
  // BREAKOUT-POS-UNPARSED (2026-10-14): 1b.188 found the live Positions tab with the ETH short open and read
  // "unparsed". innerText GLUES adjacent inline spans ("<span>Short</span><span>20x</span>" -> "Short20x"), so the
  // side word was invisible to \bshort\b. Side and symbol words are now read from the cell's text nodes with spaces;
  // numbers still come from innerText, so a price whose digits sit in separate spans stays one number.
  const glued = gridRow("<img alt=''><span>ETH</span><span>x5</span>", "<span>-2.10</span><span>USD</span>", "<span>Short</span><span>20x</span>",
    "1,327.45 USD", "2,4<b>58</b>.24", "2,462.10", "2214.87 / 2504.25", "-2.50 USD");
  const gG = await onHost(gridPage("Positions1", glued));
  eq(await gG.evaluate("document.querySelector('.r').children[2].innerText"), "Short20x", "glued: the side cell's innerText really is glued");
  let gg = await gG.evaluate("__ex.positionsRead()");
  eq([gg.state, gg.n_rows, gg.count], ["read", 1, 1], "glued: the ETH short with glued side/leverage spans reads (was unparsed)");
  eq([gg.rows[0].symbol, gg.rows[0].side, gg.rows[0].entry, gg.rows[0].mark, gg.rows[0].qty],
    ["ETH", "short", 2458.24, 2462.1, 0.53915357], "glued: symbol ETH (x5 dropped), side short, split-digit entry kept whole, qty derived");
  // a miss now says which need key failed and dumps the first failing row's cells (digits masked)
  let gs = await (await onHost(gridPage("Positions1", gridRow("ETHUSD", "-2.10 USD", "S", "1,327.45 USD", "2,458.24", "2,462.10", "-", "-2.50 USD")))).evaluate("__ex.positionsRead()");
  eq([gs.state, gs.detail], ["side_unread", "miss=side bad=0 n=1"], "side-only miss names the missing key");
  eq(gs.cells.slice(0, 4), ["symbol:ETHUSD", "upnl:-d.dd USD", "side:S", "value:d_ddd.dd USD"], "unparsed miss dumps key:shape cells, digits masked");
  eq(/\d/.test(JSON.stringify(gs.cells)), false, "unparsed miss cells carry no digits");
  // LIVE 1b.193 SHAPE (dump 2026-10-14 21:38Z): 9 header children (8 labels + one EMPTY, which the server's diag drops),
  // Market "ETH d" -- the GROUP row's "ETH 1" (operator screenshot 21:41Z: "⌄ ETH 1  Sum ⌄"; corrected from the earlier
  // "ETH 5" leverage-badge reading), read as row_kind "group" because no child row is in this DOM, an EMPTY Side cell (the side is drawn), an empty TP / SL cell, and a 9th
  // empty cell. SYNTHETIC attributes: which one Breakout uses is UNMEASURED -- the side_unread dump below shows it.
  const liveHdr = `<div>Market</div><div>UP&amp;L</div><div>Side</div><div>Value</div><div>Opening price</div><div>Current price</div><div>TP / SL</div><div>Net P&amp;L</div><div></div>`;
  const livePage = (sideCell) => `<!doctype html><html><head><meta charset=utf-8><style>#hdr,.r{display:grid;grid-template-columns:repeat(9,90px);min-height:20px}</style></head><body><div id=tabs><button>Open orders</button><button>Positions1</button><button>Closed orders</button></div>
<div id=panel><div id=hdr>${liveHdr}</div><div class=r><div><span>ETH</span> <span class=lev>1</span></div><div>Sum 12.34USD</div><div>${sideCell}</div><div>Sum 1,327.45USD</div><div>2,458.24USD</div><div>2,436.10USD</div><div></div><div>Sum 11.90USD</div><div><button aria-label="Close">x</button></div></div></div></body></html>`;
  let lv = await (await onHost(livePage("<span class='pos-side pos-side--short'></span>"))).evaluate("__ex.positionsRead()");
  eq([lv.state, lv.rows[0].symbol, lv.rows[0].side, lv.rows[0].entry, lv.rows[0].qty], ["read", "ETH", "short", 2458.24, 0.54490784], "live shape: side read from a class token, symbol 'ETH 1' -> ETH, 9th empty column ignored");
  eq(lv.rows[0].row_kind, "group", "live shape: the 1b.193 row is the GROUP row (count 1, no child in the DOM)");
  lv = await (await onHost(livePage("<svg aria-label='Sell'><use href='#arrow-down'></use></svg>"))).evaluate("__ex.positionsRead()");
  eq([lv.state, lv.rows[0].side], ["read", "short"], "live shape: side read from an svg aria-label");
  lv = await (await onHost(livePage("<span class='badge text-red' style='display:inline-block;width:8px;height:8px'></span>"))).evaluate("__ex.positionsRead()");
  eq([lv.state, lv.rows.length, lv.rows[0].side, lv.rows[0].symbol], ["side_unread", 1, null, "ETH"], "live shape: colour only -> side_unread, rows sent with side null (no guess from colour)");
  eq(lv.cells.filter((c) => c.startsWith("side+")), ["side+div", "side+span class=badge text-red"], "live shape: the empty side cell's attribute shape is dumped");
  lv = await (await onHost(livePage("<i class='icon-long-short'></i>"))).evaluate("__ex.positionsRead()");
  eq([lv.state, lv.rows[0].side], ["side_unread", null], "live shape: a token naming BOTH sides is ambiguous -> no side");
  // a Current price misread as a tiny number ("2 462.10" -> 2) is never a qty divisor: unparsed, not a 663-lot position
  let gm2 = await (await onHost(gridPage("Positions1", gridRow("ETHUSD", "-2.10 USD", "Short", "1,327.45 USD", "2,458.24", "2 462.10", "-", "-2.50 USD")))).evaluate("__ex.positionsRead()");
  eq([gm2.state, gm2.detail], ["unparsed", "miss=qty bad=0 n=1"], "mark 2x away from entry -> qty not derived (unparsed)");
  // AMEND ON THE DIV GRID (BREAKOUT-POS-UNPARSED): posInfo/posEdit searched only tr/[role=row], so every trail amend
  // on the live grid reported no_position while the ETH short was open. The row is now found in the Positions grid,
  // its combined "TP / SL" column is split, and -- MEASURE FIRST -- nothing in a grid row is clicked: posEdit refuses
  // no_edit_unmeasured and posInfo carries the row's control dump (digits masked). SYNTHETIC controls.
  const amendRow = (side) => gridRow("<span>ETH</span><span>x5</span>", "-2.10 USD", side, "1,327.45 USD", "2,458.24", "2,462.10",
    "<span>2214.87 / 2504.25</span><button data-c aria-label='Edit TP/SL' class='icon-btn tpsl-edit'><svg width=8 height=8></svg></button>",
    "-2.50 USD<button data-c>Close</button>");
  const gA = await onHost(gridPage("Positions1", amendRow("Short") + "<script>window.__clicks=0;document.querySelectorAll('[data-c]').forEach(function(b){b.onclick=function(){window.__clicks++}})</script>"));
  let ga = await gA.evaluate("__ex.posInfo('ETHUSD','short|sell')");
  eq([ga.n, ga.layout, ga.sl, ga.tp], [1, "div_grid", "2504.25", "2214.87"], "amend grid: the ETH short row found; TP / SL split into sl + tp");
  eq((await gA.evaluate("__ex.posInfo('ETHUSD','long|buy')")).n, 0, "amend grid: no long row");
  eq(ga.ctls.map((c) => [c.col, c.tag, c.label]).filter((c) => c[1] === "button"), [["TP / SL", "button", "Edit TP/SL"], ["Net P&L", "button", "Close"]], "amend grid: control dump names each control and its column");
  eq(ga.ctls.find((c) => c.tag === "button").cls, "icon-btn tpsl-edit", "amend grid: control dump keeps class names");
  eq([/\d/.test(JSON.stringify(ga.ctls)), ga.ctl_lines.every((x) => x.length <= 40)], [false, true], "amend grid: dump digit-masked, lines fit the server's 40-char scrub");
  eq([await gA.evaluate("__ex.posEdit('ETHUSD','short|sell')"), await gA.evaluate("window.__clicks")], ["no_edit_unmeasured", 0], "amend grid: MEASURE FIRST -- refused, nothing clicked");
  const gA2 = await onHost(gridPage("Positions2", amendRow("Short") + amendRow("Short")));
  eq(await gA2.evaluate("__ex.posEdit('ETHUSD','short|sell')"), "rows_2", "amend grid: two ETH short rows -> refused");
  const gNoTabs = await onHost(`<!doctype html><html><body><div id=hdr><div>Market</div><div>UP&amp;L</div><div>Side</div><div>Value</div><div>Opening price</div><div>Current price</div></div><p>Start trading with leverage</p></body></html>`);
  eq((await gNoTabs.evaluate("__ex.positionsRead()")).state, "empty_unconfirmed", "div grid: no tab bar to bound the panel -> no flat evidence");
  // CLOSED ORDERS as a DIV GRID (layout UNMEASURED; synthetic): the generic reader maps by header label too.
  const cRow = (m, side, typ, qty, px, st, tm, pnl) => `<div class=r><div>${m}</div><div>${side}</div><div>${typ}</div><div>${qty}</div><div>${px}</div><div>${st}</div><div>${tm}</div><div>${pnl}</div></div>`;
  const closedGrid = (tab, body) => `<!doctype html><html><body><div id=tabs><button>Open orders</button><button>Positions</button><button>${tab}</button><button>Trades</button></div>
<div id=panel><div id=hdr><div>Market</div><div>Side</div><div>Type</div><div>Size</div><div>Avg. price</div><div>Status</div><div>Time</div><div>Net P&amp;L</div></div>${body}</div></body></html>`;
  const cg = await onHost(closedGrid("Closed orders", cRow("SOLUSD", "Sell", "Stop", "17.26", "111.39", "Filled", "2026-10-12 16:30:05", "+3.97 USD") +
    cRow("SOLUSD", "Buy", "Limit", "17.26", "111.16", "Filled", "2026-10-11 18:51:24", "-")));
  let cgr = await cg.evaluate("__ex.closedRead()");
  eq([cgr.state, cgr.layout, cgr.rows.length], ["read", "div_grid", 2], "closed div grid: two orders read");
  eq([cgr.rows[0].side, cgr.rows[0].qty, cgr.rows[0].price, cgr.rows[0].status, cgr.rows[0].netpnl, cgr.rows[0].time], ["short", 17.26, 111.39, "Filled", 3.97, "2026-10-12 16:30:05"], "closed div grid: the exit row mapped by header");
  const cgBad = await onHost(closedGrid("Closed orders", cRow("SOLUSD", "Sell", "Stop", "17.26", "111.39", "Filled", "2026-10-12 16:30:05", "+3.97 USD") + "<div class=r><div>SOLUSD 12</div><div>Buy</div></div>"));
  eq((await cgBad.evaluate("__ex.closedRead()")).state, "unparsed", "closed div grid: a misaligned row is unparsed");
  const closedT = `<!doctype html><html><body><div role=tablist><button role=tab>Positions</button><button role=tab>Closed orders</button></div>
<table><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Type</th><th>Filled</th><th>Avg. price</th><th>Status</th><th>Realized PnL</th></tr>
<tr><td>2026-10-12 12:01:05</td><td>ETH</td><td>Sell</td><td>Take profit</td><td>1.1758</td><td>2,655.40</td><td>Filled</td><td>+150.03</td></tr>
<tr><td>2026-10-11 15:50:48</td><td>ETH</td><td>Buy</td><td>Limit</td><td>1.1758</td><td>2,527.80</td><td>Filled</td><td>-</td></tr></table></body></html>`;
  const qC = await onHost(closedT);
  let qcr = await qC.evaluate("__ex.closedRead()");
  eq(qcr.state, "read", "closed orders read");
  eq([qcr.rows[0].symbol, qcr.rows[0].side, qcr.rows[0].qty, qcr.rows[0].price, qcr.rows[0].type, qcr.rows[0].status, qcr.rows[0].pnl, qcr.rows[0].time],
    ["ETH", "short", 1.1758, 2655.4, "Take profit", "Filled", 150.03, "2026-10-12 12:01:05"], "closed orders: the TP fill row mapped");
  eq(qcr.rows[1].pnl, null, "closed orders: '-' P&L stays null, never 0");
  // LIVE CLOSED-ORDERS LAYOUT (operator screenshot 2026-10-13, transcribed; synthetic values): the tab strip scrolls
  // horizontally and "Closed orders" is the LAST, clipped chip; the panel header is Market | Side | Type | ... with
  // more columns off to the right; the Side cell is "Long" + a separate "(Reduce)" span; a bracketed trade leaves a
  // TP and an SL row, one Filled and one Cancelled; the entry row is a plain "Short Limit".
  const reduce = (w) => `<span>${w}</span> <span>(Reduce)</span>`;
  const liveClosed = (withChip) => `<!doctype html><html><body>
<div id=strip style="width:180px;overflow-x:auto;white-space:nowrap"><button>Depth chart</button><button>Favorites</button><button>Order book</button><button>Portfolio</button>${withChip ? "<button>Closed orders</button>" : ""}</div>
<div role=tablist><button role=tab>Open orders 2</button><button role=tab>Positions 1</button></div>
<div id=panel><div id=hdr><div>Market</div><div>Side</div><div>Type</div><div>Size</div><div>Price</div><div>Status</div><div>Time</div><div>Net P&amp;L</div></div>
${cRow("ETH", reduce("Long"), "Stop loss", "0.571", "2535.25", "Filled", "2026-10-13 05:20:00", "-25.57 USD")}
${cRow("ETH", reduce("Long"), "Take profit", "0.571", "2244.81", "Cancelled", "2026-10-13 05:20:00", "-")}
${cRow("ETH", "Short", "Limit", "0.571", "2491.47", "Filled", "2026-10-13 04:10:00", "-")}
${cRow("SOL", reduce("Short"), "Take profit", "17.26", "111.39", "Filled", "2026-10-12 16:30:05", "+3.97 USD")}
</div></body></html>`;
  const lc = await onHost(liveClosed(true));
  eq(await lc.evaluate("__ex.clickTab('^closed orders\\\\b')"), "clicked", "closed orders: the clipped last chip is found and clicked");
  eq(await lc.evaluate("document.getElementById('strip').scrollLeft > 0"), true, "closed orders: the chip was scrolled into view");
  const lcr = await lc.evaluate("__ex.closedRead()");
  eq([lcr.state, lcr.rows.length], ["read", 4], "closed orders: live layout read (div grid)");
  eq(lcr.rows.map((r) => [r.symbol, r.side, r.reduce, r.type, r.status, r.price]),
    [["ETH", "long", true, "Stop loss", "Filled", 2535.25], ["ETH", "long", true, "Take profit", "Cancelled", 2244.81],
     ["ETH", "short", false, "Limit", "Filled", 2491.47], ["SOL", "short", true, "Take profit", "Filled", 111.39]],
    "closed orders: Reduce suffix -> reduce=true with the side word kept; TP/SL pair keeps Filled vs Cancelled; entry row reduce=false");
  const lcNo = await onHost(liveClosed(false));
  eq(await lcNo.evaluate("__ex.clickTab('^closed orders\\\\b')"), "none", "closed orders: no chip -> none (miss, never a silent empty)");
  // the POSITIONS grid still on screen is NOT Closed orders (no Type/Status column): no_table, not an empty list
  const posOnly = await onHost(`<!doctype html><html><body><div role=tablist><button role=tab>Positions</button><button role=tab>Closed orders</button></div><div id=hdr><div>Market</div><div>UP&amp;L</div><div>Side</div><div>Value</div><div>Opening price</div><div>Current price</div><div>TP / SL</div><div>Net P&amp;L</div></div><p>Start trading with leverage</p></body></html>`);
  eq((await posOnly.evaluate("__ex.closedRead()")).state, "no_table", "closed orders: a still-shown Positions grid is not read as an empty Closed orders list");
  // ACCOUNT PANEL on the Portfolio tab (PROP-TRACKING): balance + the terminal's own unrealized P&L, signed
  const qA = await onHost(`<!doctype html><html><head><meta charset=utf-8></head><body><button>Portfolio 5,099.14 USD</button><div role=tablist><button role=tab>Portfolio</button></div>
<dl><dt>Balance</dt><dd>5,048.20</dd><dt>Unrealized PnL</dt><dd>\u221212.06</dd></dl></body></html>`);
  let ap = await qA.evaluate("__ex.accountPanel()");
  eq([ap.balance, ap.portfolio, ap.unrealized], [5048.2, 5099.14, -12.06], "account panel: balance, portfolio and signed unrealized read by label");
  // POSITION SHEET (BREAKOUT-SL-AMEND-CLICK): the operator-MEASURED flow (screenshots 2026-10-14 ~21:41Z) on a
  // SYNTHETIC DOM -- text, tabs and values transcribed; tags, classes and nesting are GUESSES (the live dump measures
  // them). Positions grid with a GROUP row ("ETH 1", Sum) and a CHILD row ("ETH 5x"), each with a row ✕ that closes
  // the position. Tapping a row opens the sheet; [1 TP / 1 SL] rows carry ✎ (edit) and ⓧ (cancels the order).
  const sheetPage = (opts) => { opts = opts || {}; const pen = opts.pen === undefined ? "<button class=ib data-k=edit aria-label='Edit'>✎</button>" : opts.pen;
    return `<!doctype html><html><head><meta charset=utf-8><style>#hdr,.r{display:grid;grid-template-columns:repeat(9,90px);min-height:20px}</style></head><body>
<div id=bottom><div id=tabs><button>Open orders</button><button>Positions1</button><button>Portfolio</button><button>Closed orders</button></div>
<div id=panel><div id=hdr><div>Market</div><div>UP&amp;L</div><div>Side</div><div>Value</div><div>Opening price</div><div>Current price</div><div>TP / SL</div><div>Net P&amp;L</div><div></div></div>
<div class=r id=grp><div><span>⌄</span> ETH 1</div><div>Sum 20.88USD</div><div><i class=dot></i></div><div>Sum 1,318.20USD</div><div>2,461.52USD</div><div>2,440.65USD</div><div></div><div>Sum 20.10USD</div><div><button class=x data-danger>✕</button></div></div>
${opts.noChild ? "" : `<div class=r id=chd><div id=chm>ETH <span>5x</span></div><div>20.88USD</div><div><i class=dot></i></div><div>1,318.20USD</div><div>2,461.52USD</div><div>2,440.65USD</div><div></div><div>20.10USD</div><div><button class=x data-danger>✕</button></div></div>`}
</div></div>
<div id=sheet style="display:none"><div id=sh-hd><b>ETH</b><span>Position – 7f3a9c</span><button id=shx aria-label="Close">✕</button></div>
<div id=sh-tabs><button data-t=d>Position details</button><button data-t=o>Opening trades</button><button data-t=s>1 TP / 1 SL</button></div>
<div id=pd><button data-danger class=red>Close position</button>
<div class=kv><span>Open time</span><span>2026-10-14 13:02</span></div><div class=kv><span>Side</span><span class=red>${opts.side || "Short"}</span></div>
<div class=kv><span>Avg. opening price</span><span>2,461.52 USD</span></div><div class=kv><span>Current price</span><span>2,440.65 USD</span></div>
<div class=kv><span>Initial margin</span><span>265.84 USD</span></div><div class=kv><span>Total open volume</span><span>${opts.qty || "0.54"} ETH</span></div>
<div class=kv><span>uPnL</span><span>${opts.upnl || "20.88 USD"}</span></div><div class=kv><span>RoE</span><span>7.85%</span></div></div>
<div id=ps style="display:none"><div class=th><span>SIDE</span><span>TYPE</span><span>TRIGGER PRICE</span></div>
<div class=or id=slrow><span>Long</span><span>Stop l…</span><span id=slpx>2,504.25 USD</span><span><span>0.54</span><span class=u><span>ETH</span></span></span>${pen}<button class=ib data-danger>ⓧ</button></div>
<div class=or><span>Long</span><span>Take p…</span><span>2,214.87 USD</span><span><span>0.54</span><span class=u><span>ETH</span></span></span><button class=ib aria-label='Edit'>✎</button><button class=ib data-danger>ⓧ</button></div></div></div>
<div id=modal style="display:none"><h3 id=mt>Edit Stop loss</h3><p>Short 0.54 ETH @ 2,461.52 USD</p>
<label for=mq>Quantity</label><input id=mq value="0.54"><label for=mtr>Trigger USD</label><input id=mtr value="2504.25"><label for=md>Entry distance %</label><input id=md value="1.74">
<label for=mr>R:R</label><input id=mr value="2.6"><button id=mc>Cancel</button><button id=mok>Confirm</button></div>
<script>window.__danger=0;window.__saved=0;
document.querySelectorAll('[data-danger]').forEach(function(b){b.onclick=function(){window.__danger++}});
var S=document.getElementById('sheet'),M=document.getElementById('modal');
${opts.groupOpens ? "document.getElementById('grp')" : (opts.noChild ? "document.getElementById('grp')" : "document.getElementById('chd')")}.addEventListener('click',function(e){if(!e.target.closest('[data-danger]'))S.style.display='block'});
document.querySelectorAll('#sh-tabs button').forEach(function(b){b.onclick=function(){document.getElementById('pd').style.display=b.dataset.t==='d'?'block':'none';document.getElementById('ps').style.display=b.dataset.t==='s'?'block':'none'}});
document.getElementById('shx').onclick=function(){S.style.display='none'};
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&${opts.escCloses ? "true" : "false"})S.style.display='none'});
var pe=document.querySelector('[data-k=edit]'); if(pe) pe.onclick=function(){M.style.display='block'};
document.getElementById('mc').onclick=function(){M.style.display='none'};
document.getElementById('mok').onclick=function(){window.__saved++;document.getElementById('slpx').textContent=Number(document.getElementById('mtr').value).toLocaleString('en-US',{minimumFractionDigits:2})+' USD';M.style.display='none'};
</script></body></html>`; };
  const shp = await onHost(sheetPage());
  const ev = (c) => shp.evaluate(c);
  // DENYLIST: "Close position", the ⓧ cancel icons and the row ✕ close icons are refused by the click helper
  eq(await ev("__ex.deniedSel('#pd > button')"), "deny_close_position", "denylist: 'Close position' refused");
  eq(await ev("__ex.deniedSel('#slrow [data-danger]')"), "deny_glyph", "denylist: the SL row's ⓧ (cancels the stop) refused");
  eq(await ev("__ex.deniedSel('#chd .x')"), "deny_glyph", "denylist: the child row's ✕ (closes the position) refused");
  eq(await ev("__ex.deniedSel('#grp .x')"), "deny_glyph", "denylist: the group row's ✕ refused");
  eq([await ev("__ex.safeClickSel('#pd > button')"), await ev("__ex.safeClickSel('#slrow [data-danger]')"), await ev("__ex.safeClickSel('#chd .x')"), await ev("window.__danger")],
    ["deny_close_position", "deny_glyph", "deny_glyph", 0], "denylist: safeClick refuses all three, nothing clicked");
  const dn = await onHost(`<!doctype html><html><body><button id=a aria-label="Cancel order"><svg></svg></button><button id=b title="Close"></button><button id=c><svg><title>remove</title></svg></button><button id=d class="btn-close-icon"></button><button id=e>Confirm</button><button id=f aria-label="Edit">✎</button></body></html>`);
  eq(await dn.evaluate("['#a','#b','#c','#d','#e','#f'].map(function(s){return __ex.deniedSel(s)})"), ["deny_word", "deny_word", "deny_word", "deny_word", "", ""], "denylist: cancel/close/remove words in aria, title, svg title and class are refused; Confirm and Edit are not");
  // 1. the CHILD row opens the sheet (never the ✕); the sheet's title is the symbol
  eq(await ev("__ex.sheetInfo('ETHUSD').open"), false, "sheet: closed at start");
  eq(await ev("__ex.sheetOpen('ETHUSD','child')"), "clicked", "sheet: the child row 'ETH 5x' tapped");
  let si = await ev("__ex.sheetInfo('ETHUSD')");
  eq([si.open, si.title_ok, si.subtitle, si.tabs], [true, true, "Position – #f#a#c", {details: 1, trades: 1, tpsl: 1}], "sheet: open, title ETH, subtitle masked, three tabs");
  eq(await ev("__ex.sheetOpen('ETHUSD','child')"), "sheet_open", "sheet: never opened twice");
  // 2. [Position details]: side, total open volume, avg opening price MEASURED
  eq(await ev("__ex.sheetTab('details')"), "clicked", "sheet: details tab");
  let sd = await ev("__ex.sheetDetails()");
  eq([sd.ok, sd.side, sd.qty, sd.qty_unit, sd.entry, sd.mark, sd.upnl_signed, sd.upnl_shape], [true, "short", 0.54, "ETH", 2461.52, 2440.65, false, "dd.dd USD"], "sheet details: Short 0.54 ETH @ 2461.52 read; an unsigned uPnL is flagged");
  // 3. [1 TP / 1 SL]: ONE stop row, trigger 2504.25; its SIDE word is the closing side, never the position's
  eq(await ev("__ex.sheetTab('tpsl')"), "clicked", "sheet: TP/SL tab");
  let so = await ev("__ex.sheetOrders()");
  const stopRow = so.rows.find((r) => r.type === "stop");
  eq([so.n_stop, so.n_take, stopRow.trigger, stopRow.close_side, stopRow.pencils, stopRow.icons.length], [1, 1, 2504.25, "long", 1, 2], "sheet orders: one stop @ 2504.25 (closing side long), one pencil among two icons");
  // 2026-10-18 #a8: the row ALSO carries the quantity "0.54" + "ETH" (number and unit in separate spans) — a second
  // bare price-shaped leaf. The quote unit (USD) picks the trigger; the coin-unit leaf is never the trigger.
  eq([stopRow.n_px, stopRow.n_usd, stopRow.why === undefined, stopRow.px_shapes], [2, 1, true, ["d,ddd.dd USD", "d.dd ETH"]], "sheet orders: two price-shaped leaves, ONE in the quote unit -> that one is the trigger; shapes carry the units");
  eq(stopRow.icon_lines.every((x) => x.length <= 40) && !/\d/.test(JSON.stringify(stopRow.icons)), true, "sheet orders: icon dump masked, fits the 40-char scrub");
  // 4. ONLY the stop row's pencil; the modal is "Edit Stop loss" with the header side + qty
  eq([await ev("__ex.sheetEditStop()"), await ev("window.__danger")], ["clicked", 0], "edit: the stop row's pencil clicked, never the ⓧ");
  let mi = await ev("__ex.modalInfo()");
  eq([mi.n, mi.kind, mi.side, mi.qty, mi.unit, mi.entry, mi.triggers], [1, "stop", "short", 0.54, "ETH", 2461.52, 1], "modal: Edit Stop loss, header Short 0.54 ETH @ 2461.52, one Trigger input");
  // 5. set ONLY the Trigger; quantity untouched; Cancel (dry) saves nothing
  eq(await ev("__ex.modalSetTrigger('2471.94')"), {n: 1, value: "2471.94"}, "modal: trigger set + read back");
  eq((await ev("__ex.modalInfo()")).inputs.find((i) => /quantity/i.test(i.label)).value, "0.54", "modal: quantity untouched");
  eq([await ev("__ex.modalCancel()"), await ev("window.__saved"), (await ev("__ex.modalInfo()")).n], ["clicked", 0, 0], "modal (dry): Cancel saves nothing");
  // live: Confirm, then 6. re-read the stop trigger
  await ev("__ex.sheetEditStop()"); await ev("__ex.modalSetTrigger('2471.94')");
  eq([await ev("__ex.modalConfirm()"), await ev("window.__saved"), await ev("window.__danger")], ["clicked", 1, 0], "modal (live): Confirm clicked once, nothing denied clicked");
  eq((await ev("__ex.sheetOrders()")).rows.find((r) => r.type === "stop").trigger, 2471.94, "re-read: stop trigger is the new SL");
  // 7. close: Escape does nothing on this synthetic sheet -> the header ✕ (never 'Close position')
  eq(await ev("__ex.sheetClose()"), "escape", "close: Escape sent");
  eq(await ev("__ex.sheetInfo('ETHUSD').open"), true, "close: the sheet ignored Escape");
  eq([await ev("__ex.sheetCloseX()"), await ev("__ex.sheetInfo('ETHUSD').open"), await ev("window.__danger")], ["clicked", false, 0], "close: the header ✕ closed the sheet; no position / order control clicked");
  // no child row in the DOM (the 1b.193 dump read only the group row): child -> none; the group row opens the sheet
  const sg = await onHost(sheetPage({noChild: true}));
  eq([await sg.evaluate("__ex.sheetOpen('ETHUSD','child')"), await sg.evaluate("__ex.sheetOpen('ETHUSD','group')"), await sg.evaluate("__ex.sheetInfo('ETHUSD').open")], ["none", "clicked", true], "sheet: no child row -> the group row 'ETH 1' opens it");
  // no positively identified pencil (icon with no glyph / words): refused, nothing clicked
  const sn = await onHost(sheetPage({pen: "<button class=ib><svg width=8 height=8></svg></button>"}));
  await sn.evaluate("__ex.sheetOpen('ETHUSD','child')"); await sn.evaluate("__ex.sheetTab('tpsl')");
  eq([await sn.evaluate("__ex.sheetEditStop()"), await sn.evaluate("window.__danger"), (await sn.evaluate("__ex.modalInfo()")).n], ["no_edit", 0, 0], "edit: an unidentified icon is never clicked (no_edit)");
  // a signed uPnL is MEASURED with its sign; a side that is not exactly Long/Short is null
  const su = await onHost(sheetPage({upnl: "−12.40 USD", side: "Short 5x"}));
  await su.evaluate("__ex.sheetOpen('ETHUSD','child')");
  let sud = await su.evaluate("__ex.sheetDetails()");
  eq([sud.upnl_signed, sud.upnl, sud.side, sud.ok], [true, -12.4, null, false], "sheet details: signed uPnL kept; a non-exact side word is not read");
  // GROUP vs CHILD rows (manager 2026-10-14 22:11Z: the read took the GROUP row "ETH 1" / "Sum"): the child row is read
  const gc = await (await onHost(sheetPage())).evaluate("__ex.positionsRead()");
  eq([gc.state, gc.rows.length, gc.rows[0].row_kind, gc.rows[0].symbol, gc.rows[0].upnl], ["side_unread", 1, "child", "ETH", 20.88], "group+child: only the CHILD row is read");
  const gOnly = await (await onHost(sheetPage({noChild: true}))).evaluate("__ex.positionsRead()");
  eq([gOnly.state, gOnly.rows.length, gOnly.rows[0].row_kind, gOnly.rows[0].symbol], ["side_unread", 1, "group", "ETH"], "group of 1 with no child in the DOM: the group row stands in, labelled group");
  const gTwo = await (await onHost(sheetPage({noChild: true}).replace("</span> ETH 1<", "</span> ETH 2<").replace("Positions1", "Positions2"))).evaluate("__ex.positionsRead()");
  eq([gTwo.state, gTwo.detail], ["unparsed", "miss=group bad=0 n=1"], "a group of 2 positions is never read as one row");
  const nested = sheetPage().replace('<div class=r id=chd>', '<div class=wrap><div class=r id=chd>').replace('\u2715</button></div></div>\n</div></div>', '\u2715</button></div></div></div>\n</div></div>');
  const gn = await (await onHost(nested)).evaluate("__ex.positionsRead()");
  eq([gn.state, gn.rows.length, gn.rows[0].row_kind], ["side_unread", 1, "child"], "child row nested in a wrapper is still the one read");
  // BREAKOUT-SHEET-PROBE (1b.205 live 05:59Z: click() found both rows, no sheet opened): a row that reacts only to a
  // touch pointer sequence opens with mode "pointer"; domDiff names what appeared and the tapped cell's ancestors
  const pp = await onHost(sheetPage().replace("document.getElementById('chd').addEventListener('click'", "document.getElementById('chd').addEventListener('pointerup'"));
  await pp.evaluate("__ex.domMark()");
  eq([await pp.evaluate("__ex.sheetOpen('ETHUSD','child','click')"), await pp.evaluate("__ex.sheetInfo('ETHUSD').open")], ["clicked", false], "probe: click() alone does not open a pointer-only row");
  let dd = await pp.evaluate("__ex.domDiff()");
  eq([dd.n_new, dd.details_tabs, dd.chain[0].startsWith("div")], [0, 0, true], "probe: nothing new after the click; chain starts at the tapped cell");
  await pp.evaluate("__ex.domMark()");
  eq([await pp.evaluate("__ex.sheetOpen('ETHUSD','child','pointer')"), await pp.evaluate("__ex.sheetInfo('ETHUSD').open"), await pp.evaluate("window.__danger")], ["tapped", true, 0], "probe: the touch-style tap opens it; nothing denied clicked");
  dd = await pp.evaluate("__ex.domDiff()");
  eq([dd.n_new > 0, dd.details_tabs, dd.leaves.some((x) => /Position details/.test(x)), /\d/.test(JSON.stringify(dd.leaves))], [true, 1, true, false], "probe: the new sheet's texts are listed, digits masked");
  // BREAKOUT-NATIVE-TAP (1b.210 08:37Z: click() and a synthetic pointer tap reached the cell, NO synthetic event opened
  // the sheet). A row that opens only on a TRUSTED touch: sheetTarget names the centre of the SAME cell, the app touches
  // it (here Playwright's real input stands in for MotionEvent), and the sheet opens; nothing denied is ever touched.
  const tr = sheetPage().replace("document.getElementById('chd').addEventListener('click',function(e){", "document.getElementById('chd').addEventListener('click',function(e){if(!e.isTrusted)return;");
  const tp = await onHost(tr);
  eq([await tp.evaluate("__ex.sheetOpen('ETHUSD','child','pointer')"), await tp.evaluate("__ex.sheetInfo('ETHUSD').open")], ["tapped", false], "native: a synthetic tap does not open a trusted-only row");
  let nt = await tp.evaluate("__ex.sheetTarget('ETHUSD','child',true)");
  eq([nt.ok, nt.vw > 0, typeof nt.x, await tp.evaluate("document.elementFromPoint(" + nt.x + "," + nt.y + ").closest('#chm') !== null")], [true, true, "number", true], "native: the target is the child row's Market cell centre");
  await tp.mouse.click(nt.x, nt.y);
  eq([await tp.evaluate("__ex.sheetInfo('ETHUSD').open"), await tp.evaluate("window.__danger")], [true, 0], "native: a trusted tap at that point opens the sheet; nothing denied clicked");
  eq((await tp.evaluate("__ex.sheetTarget('ETHUSD','child',false)")).why, "sheet_open", "native: never targeted with the sheet already open");
  // 2026-10-17 (1b.253 touched both rows, no sheet): the final pre-touch check arms a passive record of what the page
  // RECEIVED from the touch (type, pointerType, contact width, isTrusted); domDiff returns it as ev
  const tq = await onHost(tr);
  let nq = await tq.evaluate("__ex.sheetTarget('ETHUSD','child',true)");
  nq = await tq.evaluate("__ex.sheetTarget('ETHUSD','child',false)");
  await tq.evaluate("__ex.domMark()");
  await tq.mouse.click(nq.x, nq.y);
  const rcv = (await tq.evaluate("__ex.domDiff()")).ev;
  eq([nq.ok, rcv.length > 0, rcv.some((x) => /^click:\S+ w\d+ tr1$/.test(x)), rcv.some((x) => /^pdown:mouse w\d+ tr1$/.test(x))], [true, true, true, true], "native: domDiff.ev records the received events (type:pointerType w<size> tr<trusted>)");
  eq(await tq.evaluate("__ex.sheetInfo('ETHUSD').open"), true, "native: the passive record never blocks the trusted tap");
  // a point covered by another element (an overlay, a row ✕ drawn over the cell) is refused, never touched
  const cv = await onHost(sheetPage().replace("</body>", "<div id=ov style='position:fixed;left:0;top:0;width:100%;height:100%'></div></body>"));
  let cvr = await cv.evaluate("__ex.sheetTarget('ETHUSD','child',false)");
  eq([cvr.why, /^p\d+_\d+ v\d+x\d+ r-?\d+_-?\d+_\d+x\d+$/.test(cvr.g1), /^vv\d+x\d+\/\d+_\d+s[\d.]+ e\d+ sc\d+ dpr[\d.]+/.test(cvr.g2), cvr.g1.length <= 40 && cvr.g2.length <= 40],
    ["covered", true, true, true], "native: a covered point is refused, with its geometry (numbers only, fits the 40-char scrub)");
  const dz = await onHost(sheetPage().replace('<div id=chm>ETH <span>5x</span></div>', '<div id=chm style="position:relative">ETH <span>5x</span><i aria-label="Close" style="position:absolute;left:0;top:0;right:0;bottom:0"></i></div>'));
  eq(/^(point_)?deny_/.test((await dz.evaluate("__ex.sheetTarget('ETHUSD','child',false)")).why), true, "native: a close control under the point is refused");
  eq((await (await onHost(sheetPage({noChild: true}))).evaluate("__ex.sheetTarget('ETHUSD','child',false)")).why, "none", "native: no child row -> none (the amend path reads none as no position only with group none too)");
  // 2026-10-17 (1b.253 08:03Z/12:03Z: the Market cell got a TRUSTED finger touch, click:touch tr1, and no sheet opened):
  // the cell was wrong, not the touch. Here the Market cell keeps its own click (stopPropagation, as a market link
  // would) and the sheet opens from (a) the row, reached from any plain cell, or (b) only the "1 TP / 1 SL" text.
  const tpslRow = (s) => s.replace('<div><i class=dot></i></div><div>1,318.20USD</div><div>2,461.52USD</div><div>2,440.65USD</div><div></div><div>20.10USD</div>', '<div><i class=dot></i></div><div>1,318.20USD</div><div>2,461.52USD</div><div>2,440.65USD</div><div><span id=tps>1 TP / 1 SL</span></div><div>20.10USD</div>')
    .replace("</script>", "document.getElementById('chm').addEventListener('click',function(e){e.stopPropagation();window.__mkt=(window.__mkt||0)+1});</script>");
  const rowOpens = await onHost(tpslRow(sheetPage()));
  const tapAt = async (pg, which) => { const a = await pg.evaluate(`__ex.sheetTarget('ETHUSD','${which}',true)`); if (!a.ok) return a.why; const b = await pg.evaluate(`__ex.sheetTarget('ETHUSD','${which}',false)`); if (!b.ok) return b.why; await pg.mouse.click(b.x, b.y); return "touched"; };
  eq([await tapAt(rowOpens, "child"), await rowOpens.evaluate("__ex.sheetInfo('ETHUSD').open"), await rowOpens.evaluate("window.__mkt")], ["touched", false, 1], "targets: a trusted touch on the Market cell is eaten by the cell's own handler, no sheet");
  eq([await tapAt(rowOpens, "child_entry"), await rowOpens.evaluate("__ex.sheetInfo('ETHUSD').open"), await rowOpens.evaluate("window.__danger")], ["touched", true, 0], "targets: the child row's Opening price cell reaches the row handler, sheet opens, nothing denied");
  const tpOnly = await onHost(tpslRow(sheetPage()).replace("document.getElementById('chd').addEventListener('click'", "document.getElementById('tps').addEventListener('click'"));
  await tpOnly.evaluate("Object.defineProperty(document.getElementById('tps'),'__reactProps$t',{value:{onClick:function(){}},enumerable:true});document.querySelectorAll('#chd > div').forEach(function(d){Object.defineProperty(d,'__reactProps$t',{value:{},enumerable:true})})");
  eq([await tapAt(tpOnly, "child_entry"), await tpOnly.evaluate("__ex.sheetInfo('ETHUSD').open")], ["touched", false], "targets: a plain cell does nothing when only the TP / SL text opens the sheet");
  const tpT = await tpOnly.evaluate("__ex.sheetTarget('ETHUSD','child_tpsl',false)");
  eq([tpT.ok, await tpOnly.evaluate(`document.elementFromPoint(${tpT.x},${tpT.y}).id`)], [true, "tps"], "targets: child_tpsl is the '1 TP / 1 SL' text itself");
  await tpOnly.evaluate("__ex.domMark()");
  await tpOnly.mouse.click(tpT.x, tpT.y);
  eq([await tpOnly.evaluate("__ex.sheetInfo('ETHUSD').open"), await tpOnly.evaluate("window.__danger")], [true, 0], "targets: the TP / SL text opens it; nothing denied touched");
  const hdm = (await tpOnly.evaluate("__ex.domDiff()")).hd;
  eq([hdm.includes("upa span Click"), hdm.includes("tpsl in Click") || hdm.includes("tpsl cell Click"), hdm.includes("no_react"), hdm.every((x) => x.length <= 34)], [true, true, false, true], "targets: domDiff.hd names the React handler on the tapped text and its column");
  eq((await (await onHost(sheetPage())).evaluate("__ex.sheetTarget('ETHUSD','child_tpsl',false)")).why, "no_tpsl", "targets: an empty TP / SL cell is never a target");
  eq((await (await onHost(sheetPage({noChild: true}))).evaluate("__ex.sheetTarget('ETHUSD','child_entry',false)")).why, "none", "targets: no child row -> none for the other cells too");
  // 1b.261 20:03Z: child_entry / child_tpsl read no_row on the live grid while handlerMap found the row by walking up
  // from the Market hit. The row is now found the same way, wherever the expanded child row is nested.
  const deep = tpslRow(sheetPage()).replace('<div class=r id=chd>', '<div class=sub><div class=sub2><div class=r id=chd>').replace('✕</button></div></div>\n</div></div>', '✕</button></div></div></div></div>\n</div></div>');
  const dp = await onHost(deep);
  eq([await dp.evaluate("document.querySelector('.sub2 #chd') !== null"), await tapAt(dp, "child_entry"), await dp.evaluate("__ex.sheetInfo('ETHUSD').open"), await dp.evaluate("window.__danger")], [true, "touched", true, 0], "targets: a child row nested two wrappers deep is still found; its Opening price cell opens the sheet");
  // 1b.263 05:00Z (amend #a6 refused): child_entry/native:covered + child_tpsl/native:covered from the SCROLL call (no
  // geometry posted), then child/native touched the Market <a> (hd: "upe a Click" / "uph div Click") and no sheet. Fixture
  // of that shape: the grid in a narrow horizontal scroller, a sticky Market column, the Market content an <a> whose click
  // stops propagation, the ROW holding the opener, and the Opening price cell's CENTRE under a non-control overlay.
  const k5 = sheetPage().replace("#hdr,.r{display:grid;grid-template-columns:repeat(9,90px);min-height:20px}",
      "#panel{width:300px;overflow-x:auto}#hdr,.r{display:grid;grid-template-columns:repeat(9,120px);min-height:24px;width:1080px}#hdr>div:first-child,.r>div:first-child{position:sticky;left:0;background:#fff;z-index:2}.cv{position:absolute;left:-80px;width:50px;top:0;bottom:0;background:#eee;z-index:1}")
    .replace('<div id=chm>ETH <span>5x</span></div>', '<div id=chm><a href="#" id=mk>ETH <span>5x</span></a></div>')
    .replace('<div class=r id=chd><div id=chm><a href="#" id=mk>ETH <span>5x</span></a></div><div>20.88USD</div><div><i class=dot></i></div><div>1,318.20USD</div><div>2,461.52USD</div><div>2,440.65USD</div>',
      '<div class=r id=chd><div id=chm><a href="#" id=mk>ETH <span>5x</span></a></div><div>20.88USD</div><div><i class=dot></i></div><div>1,318.20USD</div><div>2,461.52USD</div><div style="position:relative">2,440.65USD<span class=cv></span></div>')
    .replace("</script>", "document.getElementById('mk').addEventListener('click',function(e){e.preventDefault();e.stopPropagation();window.__mkt=(window.__mkt||0)+1});</script>");
  const kp = await onHost(k5);
  eq(await kp.evaluate("!!document.querySelector('#chd .cv')"), true, "1b.263 fixture: built (an overlay from the next cell over the Opening price centre)");
  const k5s = await kp.evaluate("__ex.sheetTarget('ETHUSD','child_entry',true)");
  const k5t = await kp.evaluate("__ex.sheetTarget('ETHUSD','child_entry',false)");
  const k5hit = k5t.ok ? await kp.evaluate(`(function(){var e=document.elementFromPoint(${k5t.x},${k5t.y});return !!e.closest('#chd') && !e.classList.contains('cv') && e.closest('#chm')===null;})()`) : false;
  eq([k5s.ok, k5t.ok, k5hit], [true, true, true], "1b.263 fixture: the scroll call never refuses on the point; the check call finds an uncovered point of the Opening price cell");
  await kp.mouse.click(k5t.x, k5t.y);
  eq([await kp.evaluate("__ex.sheetInfo('ETHUSD').open"), await kp.evaluate("window.__mkt || 0"), await kp.evaluate("window.__danger")], [true, 0, 0], "1b.263 fixture: that touch reaches the row and opens the sheet; the Market link is never touched; nothing denied");
  const kq = await onHost(k5.replace("left:-80px;width:50px", "left:-120px;width:120px"));
  const kqr = (await kq.evaluate("[__ex.sheetTarget('ETHUSD','child_entry',true), __ex.sheetTarget('ETHUSD','child_entry',false)]"))[1];
  eq([kqr.ok, kqr.why, /^vis -?\d+_-?\d+_\d+x\d+$/.test(kqr.g3 || ""), kqr.g1.length <= 40 && (kqr.g3 || "").length <= 40], [false, "covered", true, true], "1b.263 fixture: a FULLY covered cell is refused with its geometry incl. the visible part (g3)");
  await rowOpens.evaluate("__ex.domMark()");
  eq((await rowOpens.evaluate("__ex.domDiff()")).hd, ["no_react"], "targets: a page without React props says so (no_react), never an empty map read as 'no handler'");
  // finding (a): the EXPANDED child row carries the side as text; childShape measures it and positionsRead reads it
  const ex = await onHost(sheetPage().replace('<div><i class=dot></i></div><div>1,318.20USD</div><div>2,461.52USD</div><div>2,440.65USD</div><div></div><div>20.10USD</div>', '<div>Short</div><div>1,318.20USD</div><div>2,461.52USD</div><div>2,440.65USD</div><div>1 TP / 1 SL</div><div>20.10USD</div>'));
  let cs = await ex.evaluate("__ex.childShape()");
  eq([cs.ok, cs.hdr, cs.groups, cs.child_rows, cs.sides.length, cs.sides[0].side, cs.sides[0].cells, cs.sides[0].picked, cs.sides[0].tpsl], [true, 9, 1, 1, 1, "short", 9, true, 1], "child shape: one Short in a 9-cell child row that childRows picks");
  let er = await ex.evaluate("__ex.positionsRead()");
  eq([er.state, er.rows.length, er.rows[0].side, er.rows[0].row_kind], ["read", 1, "short", "child"], "expanded child row: side read as text, state read");
  // a stray "Edit Take profit" modal (manager 11:02Z: seen open on the engine page) is dismissed by its Cancel, never Confirm
  const tm = await onHost(sheetPage().replace('<h3 id=mt>Edit Stop loss</h3>', '<h3 id=mt>Edit Take profit</h3>').replace('<div id=modal style="display:none">', '<div id=modal style="display:block">'));
  let tmi = await tm.evaluate("__ex.modalInfo()");
  eq([tmi.n, tmi.kind], [1, "take"], "stray modal: the open Edit Take profit modal is seen");
  eq([await tm.evaluate("__ex.modalCancel()"), (await tm.evaluate("__ex.modalInfo()")).n, await tm.evaluate("window.__saved"), await tm.evaluate("window.__danger")], ["clicked", 0, 0, 0], "stray modal: Cancel closes it; Confirm never clicked");
  // ONE dismiss rule (cancelModals / dismissModals share it): an Edit Take profit modal WITHOUT a dialog role is found too
  const tm2 = await onHost(sheetPage().replace('<h3 id=mt>Edit Stop loss</h3>', '<h3 id=mt>Edit Take profit</h3>').replace('<div id=modal style="display:none">', '<div id=modal style="display:block">'));
  eq((await tm2.evaluate("__ex.modalsOpen()")).n, 1, "dismiss: an open edit modal holding Confirm is counted");
  let dm = await tm2.evaluate("__ex.dismissModals()");
  eq([dm.found, dm.cancelled, dm.esc, (await tm2.evaluate("__ex.modalsOpen()")).n, await tm2.evaluate("window.__saved")], [1, 1, false, 0, 0], "dismiss: its Cancel clicked, nothing left, Confirm never clicked");
  eq([(await tm2.evaluate("__ex.dismissModals()")).found, await tm2.evaluate("__ex.cancelModals()")], [0, 0], "dismiss: nothing open -> nothing found / cancelled");
  // PHONE-EXEC-OBSERVABLE-1018: the script carries its version, a same-version re-injection is a no-op and a NEWER
  // version replaces the helpers (RemoteJs swaps the verified remote script in without an APK)
  const vp = await onHost(sheetPage());
  const ver = await vp.evaluate("__ex.version");
  eq(/^\d{4}-\d{2}-\d{2}\.\d+$/.test(ver), true, "version: __ex.version is the YYYY-MM-DD.N pin");
  await vp.evaluate("window.__exMarker = 1; window.__ex.marker = 'old'");
  await vp.addScriptTag({ content: src });
  eq(await vp.evaluate("__ex.marker"), "old", "version: the same version injected again changes nothing");
  await vp.addScriptTag({ content: src.replace(/var EXEC_VERSION = "[^"]+";/, 'var EXEC_VERSION = "2099-01-01.1";') });
  eq([await vp.evaluate("__ex.version"), await vp.evaluate("__ex.marker")], ["2099-01-01.1", undefined], "version: a newer script replaces the helpers");
  // DOM SNAPSHOT (PHONE-EXEC-OBSERVABLE-1018): the sheet, the modal and the Positions grid come back as geometry-carrying
  // subtrees with React handler names; ON-DEVICE REDACTION masks the position id, every P&L / value / margin / balance /
  // portfolio figure and the account path, and KEEPS prices, quantities and triggers (what a fixture needs)
  const snapPg = await onHost(sheetPage({upnl: "20.88 USD"}).replace("<div id=bottom>", "<div id=top><button>Portfolio 4,947.72 USD</button><div>Balance 4,930.42</div></div><div id=bottom>")
    .replace('<div id=sheet style="display:none">', '<div id=sheet>').replace('<div id=modal style="display:none">', '<div id=modal>').replace('<div id=ps style="display:none">', '<div id=ps>'));
  await snapPg.evaluate("Object.defineProperty(document.getElementById('chd'),'__reactProps$x',{value:{onClick:function(){}},enumerable:true})");
  const snap = await snapPg.evaluate("__ex.snapshot('page')");
  const stxt = JSON.stringify(snap);
  eq(snap.roots.map((r) => r.k).sort(), ["body", "grid", "modal", "sheet"], "snapshot: sheet / modal / grid / body subtrees");
  eq([snap.v === ver, snap.nodes > 40, snap.redact.length > 5, snap.vw > 0, snap.path.includes("/account/ACCT")], [true, true, true, true, true], "snapshot: version, node count, redaction rects, viewport, masked account path");
  for (const keep of ["Position – ID", "2,461.52USD", "2,440.65USD", "0.54 ETH", "2,504.25 USD", "Short 0.54 ETH @ 2,461.52 USD", "\"h\":[\"Click\"]"]) {
    eq(stxt.includes(keep), true, `snapshot keeps: ${keep}`);
  }
  for (const keep of ["1,318.20USD", "\"x\":\"1 TP / 1 SL\"", "Positions1"]) {   // Value (= qty x mark, both kept) and the counts the readers key on
    eq(stxt.includes(keep), true, `snapshot keeps: ${keep}`);
  }
  for (const leak of ["7f3a9c", "20.88", "4,947.72", "4,930.42", "265.84", "7.85", "A1/trade", "2026-10-14"]) {
    eq(stxt.includes(leak), false, `snapshot never carries: ${leak}`);
  }
  const grid = snap.roots.find((r) => r.k === "grid").tree;
  const cells = JSON.stringify(grid);
  eq([cells.includes("\"x\":\"Sum ##.##USD\""), cells.includes("\"x\":\"2,461.52USD\"")], [true, true], "snapshot grid: the Sum (P&L) cell is masked, the Opening price cell is kept");
  eq(typeof grid.r[2] === "number" && grid.c.length >= 3, true, "snapshot grid: rects and children present");
  const small = await snapPg.evaluate("__ex.snapshot('roots', 30)");
  eq([small.cut, small.roots.every((r) => r.k !== "body")], [true, true], "snapshot: a node budget cuts and says so; scope roots has no body");
  // TEST-TRADE CLOSE (MD-EXEC-DEV-TEST-TRADES): the sheet's "Close position" is clickable ONLY when armed for that symbol
  const tc = await onHost(sheetPage().replace('<div id=sheet style="display:none">', '<div id=sheet>'));
  eq(await tc.evaluate("__ex.sheetClosePosition('ETHUSD')"), "not_armed", "test close: unarmed -> refused, nothing clicked");
  eq(await tc.evaluate("__ex.deniedSel('#pd [data-danger]')"), "deny_close_position", "test close: every other path still refuses Close position");
  await tc.evaluate("__ex.allowClose('SOLUSD')");
  eq([await tc.evaluate("__ex.sheetClosePosition('ETHUSD')"), await tc.evaluate("window.__danger")], ["not_armed", 0], "test close: an arm for another symbol never fires, and is spent");
  await tc.evaluate("__ex.allowClose('ETHUSD')");
  eq([await tc.evaluate("__ex.sheetClosePosition('ETHUSD')"), await tc.evaluate("window.__danger")], ["clicked", 1], "test close: armed for the sheet's symbol -> the ONE Close position button is clicked");
  eq(await tc.evaluate("__ex.sheetClosePosition('ETHUSD')"), "not_armed", "test close: one arm, one click");
  await b.close(); console.log("exec_check: all passed");
})().catch((e) => { console.error(e); process.exit(1); });
