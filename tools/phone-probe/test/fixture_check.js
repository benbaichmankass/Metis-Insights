// Local check of the probe's page-side logic in headless Chromium (no Android needed):
//   node tools/phone-probe/test/fixture_check.js     (needs `playwright` resolvable, e.g. NODE_PATH=/opt/node-tools/node_modules)
// Proves (1) the React-style typing quirk the fixture models, (2) the redaction contract, (3) iframe capture.
// It proves mechanics in Chromium, NOT behaviour inside an Android WebView — that is what the APK measures.
const fs = require("fs"), path = require("path");
const { chromium } = require("playwright");
const A = path.join(__dirname, "../app/src/main/assets");
const probeJs = fs.readFileSync(path.join(A, "probe.js"), "utf8");
let fails = 0;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };

const SECRET_PAGE = `<!doctype html><title>John Q Smith Account 12345678</title><body>
<h2>Welcome John Smith</h2><div role="button" style="cursor:pointer">Balance $98,765.43</div>
<button aria-label="Account 87654321 menu">user@example.com</button>
<input type="password" aria-label="Password"><input aria-label="Email address" value="user@example.com">
<table><tr><th>Symbol</th><th>Qty</th></tr><tr><td>XAUUSD</td><td>4.20 secret</td></tr></table>
<div role="button">Buy</div><div role="button">Sell</div><button>Send Order</button>`;

(async () => {
  const b = await chromium.launch(Object.assign({ args: ["--no-sandbox"] }, process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}));
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
  const msgs = [];
  await ctx.exposeFunction("__pp", (s) => msgs.push(JSON.parse(s)));
  await ctx.addInitScript("window.PhoneProbe={postMessage:function(s){window.__pp(s)}};");
  await ctx.addInitScript(probeJs);
  await ctx.route("https://fixture-a.test/**", r => r.fulfill({ contentType: "text/html", body: fs.readFileSync(path.join(A, "fixtures/a.html")) }));
  await ctx.route("https://fixture-b.test/**", r => r.fulfill({ contentType: "text/html", body: fs.readFileSync(path.join(A, "fixtures/b.html")) }));
  await ctx.route("https://secret.test/**", r => r.fulfill({ contentType: "text/html", body: SECRET_PAGE }));

  // ---- typing / tap mechanics on the React-style fixture
  const p = await ctx.newPage();
  await p.goto("https://fixture-a.test/a.html");
  await p.waitForTimeout(500);
  await p.evaluate("__fx.reset()");
  ok(await p.evaluate("__fx.naive()") === "", "naive el.value + input event is NOT registered (React quirk modelled)");
  ok(await p.evaluate("__fx.nativeSetter()") === "2.5", "native prototype setter + input event IS registered");
  await p.evaluate("__fx.focusQty()");
  await p.keyboard.type("3.5");
  ok(JSON.parse(await p.evaluate("__fx.state()")).qty === "3.5", "real key events registered");
  await p.click("#buy"); await p.click("#slsw"); await p.click("#send");
  const st = JSON.parse(await p.evaluate("__fx.state()"));
  ok(st.side === "buy" && st.sl === true && st.sent === 1, "taps registered (side, switch, send)");
  ok((await p.evaluate("__fx.frameDirect()")).startsWith("blocked"), "parent cannot read cross-origin iframe DOM");

  // ---- iframe: doc-start script runs in the cross-origin frame, parent postMessage drives it, shape reaches the listener
  msgs.length = 0;
  await p.evaluate("document.getElementById('fr').contentWindow.postMessage({__probe:'fx',op:'type',val:'4.5',id:'x'},'*')");
  await p.waitForTimeout(400);
  ok(msgs.some(m => m.kind === "fx" && m.res && m.res.qty === "4.5" && !m.top), "iframe typed via parent postMessage, reply came from the iframe frame");
  msgs.length = 0;
  await p.evaluate("__probeCapture('cap1')");
  await p.waitForTimeout(600);
  const top = msgs.find(m => m.top && m.id === "cap1"), fr = msgs.find(m => !m.top && m.id === "cap1");
  ok(!!top && !!fr, "shape capture arrives from BOTH main frame and iframe");
  ok(fr && fr.shape.controls.some(c => /Quantity/.test(c.l)) && fr.shape.controls.some(c => /Send Order/.test(c.l)), "iframe controls seen with labels");
  ok(top.shape.markers.buy && top.shape.markers.sell && top.shape.markers.send, "terminal markers (buy/sell/send) detected on fixture");
  ok(top.shape.iframes.length === 1 && top.shape.iframes[0].host === "fixture-b.test", "iframe listed by host only");

  // ---- redaction contract
  msgs.length = 0;
  const s = await ctx.newPage();
  await s.goto("https://secret.test/");
  await s.evaluate("__probeCapture('sec')");
  await s.waitForTimeout(500);
  const blob = JSON.stringify(msgs);
  for (const leak of ["John", "Smith", "12345678", "87654321", "98,765", "user@example.com", "XAUUSD", "4.20 secret", "4.20", "98765"])
    ok(!blob.includes(leak), `report does not contain "${leak}"`);
  const sh = msgs.find(m => m.id === "sec").shape;
  ok(sh.markers.pw === true, "password field detected as a boolean marker");
  ok(sh.controls.some(c => c.t === "input" && c.ty === "password"), "password input recorded by type only");
  ok(!/value/.test(Object.keys(sh.controls[0]).join()), "no value field in any control record");
  ok(sh.tables.length === 1 && sh.tables[0].cols.join("|") === "Symbol|Qty", `table headers kept only if vocabulary (${sh.tables[0] && sh.tables[0].cols})`);
  const R = await s.evaluate(() => ["Balance $98,765.43", "Send Order", "John Smith", "Stop Loss (price)", "x@y.com", "Qty 0.01 lots"].map(t => window.__probeRedact(t)));
  console.log("redact():", JSON.stringify(R));
  await b.close();
  console.log(fails ? `${fails} FAILED` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
