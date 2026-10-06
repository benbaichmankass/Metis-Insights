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
function form(){return '<form id=f><div role=tablist><button type=button role=tab aria-selected=false>Market</button><button type=button role=tab aria-selected=true>Limit</button></div><div role=tablist><button type=button role=tab aria-selected=true>Buy</button><button type=button role=tab aria-selected=false>Sell</button></div><label for=lp>Limit price</label><input id=lp type=text value=1><button type=submit id=sub>Long (buy) '+cur+'</button></form>';}
document.getElementById('of').onclick=function(){document.getElementById('panel').innerHTML=form();document.getElementById('f').onsubmit=function(e){e.preventDefault();window.__submitted=(window.__submitted||0)+1};};
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
  eq(await rE("window.__submitted || 0"), 0, "panel: nothing submitted");
  await b.close(); console.log("exec_check: all passed");
})().catch((e) => { console.error(e); process.exit(1); });
