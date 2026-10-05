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
  await b.close(); console.log("exec_check: all passed");
})().catch((e) => { console.error(e); process.exit(1); });
