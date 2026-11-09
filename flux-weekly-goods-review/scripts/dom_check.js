// 渲染自检：用假 DOM 跑看板内联 JS，断言 6 个页签的关键块都写出了内容。
// 用法：node dom_check.js "C:\...\商品周复盘看板.html"
const fs = require("fs");

const htmlPath = process.argv[2];
if (!htmlPath) {
  console.error("用法: node dom_check.js <看板.html 绝对路径>");
  process.exit(2);
}
const html = fs.readFileSync(htmlPath, "utf8");

const dataMatch = html.match(/<script id="embedded-data" type="application\/json">([\s\S]*?)<\/script>/);
if (!dataMatch) {
  console.error("FATAL: 找不到 embedded-data");
  process.exit(1);
}
const dataText = dataMatch[1];

const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const js = scripts[scripts.length - 1];

const outputs = {};
function node(id) {
  if (!outputs[id]) {
    outputs[id] = { id, innerHTML: "", _text: "", classList: { toggle() {} }, addEventListener() {}, querySelectorAll() { return []; } };
    Object.defineProperty(outputs[id], "textContent", {
      get() { return this._text; },
      set(v) { this._text = String(v); },
    });
  }
  return outputs[id];
}

global.document = {
  getElementById(id) { return node(id); },
  querySelectorAll() { return []; },
  querySelector() { return node("h1"); },
};

node("embedded-data").textContent = dataText;
global.fetch = () => Promise.reject(new Error("file:// fallback"));

const errors = [];
try {
  eval(js);
} catch (e) {
  errors.push("sync: " + e.message);
}

setTimeout(() => {
  const required = ["kpis", "funnel", "overview-insight", "newTitleCount", "newBars", "new-note", "newCats", "newRules",
                    "actBars", "act-insight", "catBars", "priceBars", "price-insight", "qualityBlock",
                    "targetRows", "targetNote", "weekSel", "weekHint"];
  const empty = [];
  for (const id of required) {
    const v = outputs[id];
    const filled = v && (v.innerHTML || v._text);
    if (!filled) empty.push(id);
  }
  const d = JSON.parse(dataText);
  console.log("week:", d.week, d.period && d.period.label);
  console.log("errors:", errors.length ? errors : "none");
  console.log("rendered blocks:", (required.length - empty.length) + "/" + required.length);
  if (empty.length) console.log("EMPTY:", empty.join(", "));
  console.log("新品标题:", (outputs["newTitleCount"] || {})._text);
  console.log("targetNote:", (outputs["targetNote"] || {}).innerHTML.slice(0, 80));
  console.log("weekHint:", (outputs["weekHint"] || {})._text);
  process.exit(errors.length || empty.length ? 1 : 0);
}, 900);
