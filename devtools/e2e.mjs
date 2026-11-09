// 画面ごとの通しテスト（ヘッドレス）。devtools/devserver.py に対して、web/doctor.html と web/patient.html の
// 本物の画面のスクリプトを jsdom の中で動かし、医師と患者（別のユーザー）としてボタンを押して DEMO.md のシナリオ A・B・C を通す。
//
//   .venv/bin/python -m devtools.devserver &          # 偽の AI・メモリ上のデータ
//   npm i --no-save jsdom@24 && node devtools/e2e.mjs  # （jsdom を別の場所に入れたときは NODE_PATH で指す）
//
// 本物のブラウザではない（見た目・レイアウトは確かめない）。画面の流れ・表示される文言・画面のエラーが無いことを確かめる。
import { Worker, isMainThread, parentPort, workerData } from "node:worker_threads";
import { createRequire } from "node:module";
import { mkdtempSync, cpSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const BASE = process.env.UNSAID_DEV_URL ?? "http://localhost:8765";

// ───────── メインスレッド: 2つの画面を別々のスレッドで動かし、session id だけを渡す ─────────
if (isMainThread) {
  const dir = mkdtempSync(join(tmpdir(), "unsaid-e2e-"));
  cpSync(join(ROOT, "web"), dir, { recursive: true });
  cpSync(join(ROOT, "devtools/web/unsaid-local.js"), join(dir, "unsaid.js"));
  for (const page of ["doctor", "patient"]) {
    const html = readFileSync(join(ROOT, "web", `${page}.html`), "utf8");
    writeFileSync(join(dir, `${page}.page.mjs`), html.match(/<script type="module">([\s\S]*?)<\/script>/)[1]);
  }
  const run = (role) => new Worker(fileURLToPath(import.meta.url), { workerData: { role, dir } });
  const doctor = run("doctor");
  const patient = run("patient");
  const results = {};
  const done = new Promise((ok) => {
    for (const [name, w] of [["doctor", doctor], ["patient", patient]]) {
      w.on("message", (m) => {
        if (m.sid) patient.postMessage({ sid: m.sid });
        if (m.log) console.log(`[${name}] ${m.log}`);
        if (m.done) { results[name] = m; if (Object.keys(results).length === 2) ok(); }
      });
      w.on("error", (e) => { results[name] = { ok: false, error: String(e) }; if (Object.keys(results).length === 2) ok(); });
    }
  });
  const timer = setTimeout(() => { console.log("TIMEOUT"); process.exit(1); }, 120000);
  await done;
  clearTimeout(timer);
  let fail = 0;
  for (const [name, r] of Object.entries(results)) {
    console.log(`${r.ok ? "PASS" : "FAIL"} ${name}${r.error ? `: ${r.error}` : ""}`);
    for (const e of r.pageErrors ?? []) console.log(`  画面のエラー: ${e}`);
    fail += !r.ok || (r.pageErrors ?? []).length > 0;
  }
  console.log(fail ? `\n${fail} 件 FAIL` : "\nすべて PASS");
  process.exit(fail ? 1 : 0);
}

// ───────── 各スレッド: jsdom に画面を作り、本物の画面のスクリプトを読み込む ─────────
const { JSDOM } = createRequire(import.meta.url)("jsdom");
const { role, dir } = workerData;
const log = (s) => parentPort.postMessage({ log: s });
const pageErrors = [];
let clipboard = "";

async function open(url, page) {
  const body = page === "patient" ? `<main id="app"></main>` : `<div id="app"></div>`;
  const dom = new JSDOM(`<!doctype html><html><body>${body}</body></html>`, { url, pretendToBeVisual: true });
  const w = dom.window;
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event("close")); };
  w.confirm = () => true;
  Object.defineProperty(w.navigator, "clipboard", { value: { writeText: async (t) => { clipboard = t; } } });
  w.addEventListener("error", (e) => pageErrors.push(String(e.error ?? e.message)));
  for (const k of ["window", "document", "location", "history", "sessionStorage", "localStorage", "confirm",
    "HTMLElement", "Event", "getComputedStyle"]) {
    Object.defineProperty(globalThis, k, { value: k === "window" ? w : w[k], configurable: true, writable: true });
  }
  Object.defineProperty(globalThis, "navigator", { value: w.navigator, configurable: true });
  const realFetch = globalThis.fetch;
  globalThis.fetch = (u, o) => realFetch(new URL(u, BASE), o);
  const origError = console.error;
  // jsdom はページの移動（診察終了後の location.href = "doctor.html"）を実装していない。それだけは画面のエラーとして数えない
  console.error = (...a) => {
    const s = a.map(String).join(" ");
    if (!/Not implemented: navigation/.test(s)) { pageErrors.push(s); origError(...a); }
  };
  process.on("unhandledRejection", (e) => pageErrors.push(`unhandled: ${e?.stack ?? e}`));
  await import(pathToFileURL(join(dir, `${page}.page.mjs`)).href);
  return w;
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const text = (sel = "body") => $(sel)?.textContent ?? "";
const byText = (sel, t) => $$(sel).find((e) => e.textContent.includes(t));
async function waitFor(fn, what, ms = 20000) {
  const t0 = Date.now();
  for (;;) {
    let v;
    try { v = fn(); } catch { v = null; }
    if (v) return v;
    if (Date.now() - t0 > ms) throw new Error(`待ちきれない: ${what}\n--- 画面 ---\n${text().slice(0, 1500)}`);
    await new Promise((r) => setTimeout(r, 100));
  }
}
const click = async (sel, t, what) => (await waitFor(() => (t ? byText(sel, t) : $(sel)), what ?? `${sel} ${t ?? ""}`)).click();
function type(el, value) {
  el.value = value;
  el.dispatchEvent(new window.Event("input", { bubbles: true }));
}
function check(cond, what) {
  if (!cond) throw new Error(`確認できない: ${what}\n--- 画面 ---\n${text().slice(0, 1500)}`);
  log(`ok  ${what}`);
}

// DEMO.md の発言（devtools/fake_ai.py と同じ）
const A_TEXT = "医生，我是来日本旅游的，昨天刚到。今天在车站突然觉得头晕，眼前发黑，差点晕倒。坐下来休息了一会儿好一些，但现在还是有点不舒服，我有点害怕。";
const B_EXPLAIN = "今日は検査の結果が出るまで、帰らずに待ってください。";
const B_WRONG = "明天再来就可以了，是吗？";
const B_RIGHT = "今天不回家，在医院等检查结果出来。";
const C_EXPLAIN = "この薬は1日3回、食後に飲んでください。";
const C_RIGHT = "一天三次，吃完饭以后吃。";

async function doctorFlow() {
  await open(`${BASE}/doctor.html`, "doctor");
  await click("button", "問診を始める");
  await waitFor(() => $(".qr-overlay svg"), "QR");
  const sid = new URLSearchParams(location.search).get("s");
  check(sid && sid.length >= 16, "問診を作り、QR を出す");
  parentPort.postMessage({ sid });
  await waitFor(() => !$(".qr-overlay") && $(".d-app"), "患者が参加して QR が閉じる");
  check(text(".d-head").includes("中国語"), "患者（中国語）と接続中");

  // ── シナリオ A ──
  await waitFor(() => text(".d-log").includes("駅で突然めまい"), "患者の発言（翻訳）が届く");
  await waitFor(() => $(".d-cands"), "確認候補");
  check(text(".d-memo").includes("めまい") && byText(".d-memo .tag", "未確認"), "問診メモ: めまい［未確認］");
  check(text(".d-memo").includes("不安の訴え（本人の言葉）"), "本人の言葉の不安を残す");
  check(text(".d-memo").includes("旅行で来日、昨日到着"), "背景（旅行・昨日到着）");
  check(!text(".d-memo").includes("貧血"), "原文に無い情報（貧血）は出さない");
  check(text(".d-memo").includes("言及なし: 既往歴・服薬・アレルギー"), "言及なしを区別する");
  check(text(".d-cands-head").includes("1件") && text(".d-cand").includes("头晕"), "確認候補 1件（头晕）");
  check(text(".d-cand").includes("回転性") && text(".d-cand").includes("「头晕（めまい）」は"), "理由と日本語の質問");
  // 根拠を開く
  await click(".d-fact", "めまい");
  const ev = await waitFor(() => $(".d-evidence"), "根拠");
  check(ev.querySelector("mark")?.textContent === "头晕" && ev.textContent.includes("AIが曖昧さを指摘"), "原文の根拠（头晕）と履歴");
  await click(".d-fact", "めまい"); // 閉じる
  await click(".d-cand .btn-small", "質問を送る");
  await waitFor(() => text(".d-log").includes("確認質問"), "確認質問が会話に出る");
  check(text(".d-cands").includes("回答待ち") || text(".d-log").includes("確認質問への答え"), "回答待ち（または回答済み）");
  await waitFor(() => text(".d-log").includes("確認質問への答え"), "患者の答えが届く");
  await waitFor(() => byText(".d-memo .tag", "患者回答"), "問診メモが更新される");
  check(text(".d-memo").includes("めまい：周囲がぐるぐる回る（回転性）"), "めまい：周囲がぐるぐる回る（回転性）［患者回答］");
  check(!text(".d-memo").includes("医師確認済み") && !$(".d-memo .check"), "患者が答えただけでは医師確認済みにならない");
  await click(".d-fact", "めまい");
  await click(".d-evidence .btn-small", "確認済みにする");
  await waitFor(() => $('.d-memo .check[aria-label="医師確認済み"]'), "医師確認済み");
  check(text(".d-evidence").includes("患者が回答「周囲がぐるぐる回る（回転性）」") && text(".d-evidence").includes("医師が確認"), "確認の履歴");

  // カルテ
  await click("button", "カルテにコピー");
  const ta = await waitFor(() => $("dialog.d-sheet textarea"), "カルテ用の文章");
  const k = ta.value;
  check(k.includes("■主訴") && k.includes("めまい：周囲がぐるぐる回る（回転性）［医］"), "カルテ: 主訴・医師確認済み");
  check(k.includes("「头晕」（めまい）→ 周囲がぐるぐる回る（回転性）"), "カルテ: 確認した内容");
  check(k.includes("言及なし：既往歴・服薬・アレルギー") && !k.includes("貧血"), "カルテ: 言及なし・原文に無いものは無い");
  type(ta, k + "\n医師メモ: 追記");
  await click("dialog .btn-primary", "コピー");
  check(clipboard.endsWith("医師メモ: 追記"), "医師が編集した文をコピーできる");

  // ── シナリオ B ──
  const input = $("#dinput");
  type(input, B_EXPLAIN);
  await click(".d-input .send", "送る");
  await waitFor(() => byText(".d-mine", B_EXPLAIN)?.textContent.includes("送信済み"), "説明が送信済み");
  await click(".d-mine.last .tb", "理解を確認");
  await waitFor(() => byText(".d-mine", "理解の確認"), "理解の確認を依頼");
  const warn = await waitFor(() => $(".d-tbres.warn"), "認識の相違");
  check(warn.textContent.includes("認識の相違の可能性") && warn.textContent.includes("明日また来ればよい"), "不一致を検出（今日待つ ⇔ 明日来る）");
  check(warn.textContent.includes("AI の判定です。医師が確認してください"), "AI の判定は確定扱いにしない");
  await click(".d-tbres.warn .btn-link", "再説明する");
  await waitFor(() => $("#dinput").value.includes("今日は帰らないでください"), "再説明の文が入力欄に入る");
  check(text(".d-tbres.warn").includes("再説明が必要"), "再説明が必要");
  await click(".d-input .send", "送る");
  await waitFor(() => byText(".d-mine.last", "今日は帰らないでください")?.textContent.includes("送信済み"), "再説明を送信");
  await click(".d-mine.last .tb", "理解を確認");
  const ok = await waitFor(() => $$(".d-tbres.ok").at(-1), "再確認で一致");
  check(ok.textContent.includes("説明と一致"), "再確認: 説明と一致");
  await click(".d-tbres.ok .btn-link", "理解を確認済みにする");
  await waitFor(() => byText(".d-tbres.ok .badge-ok", "医師が理解を確認"), "医師が理解を確認");
  check(true, "医師が理解を確認済みにした");

  // ── シナリオ C ──
  type($("#dinput"), C_EXPLAIN);
  await click(".d-input .send", "送る");
  await waitFor(() => byText(".d-mine.last", C_EXPLAIN)?.textContent.includes("送信済み"), "説明 C を送信");
  await click(".d-mine.last .tb", "理解を確認");
  await waitFor(() => $$(".d-tbres").length === 3, "C の結果");
  check($$(".d-tbres.warn").length === 1 && $$(".d-tbres.ok").length === 2, "正しい理解には警告を出さない");

  await click("button", "カルテにコピー");
  const k2 = (await waitFor(() => $("dialog.d-sheet textarea"), "カルテ2")).value;
  check(k2.includes("■説明と理解の確認") && k2.includes("認識の相違の可能性（AI判定）／医師: 再説明が必要")
    && k2.includes("説明と一致（AI判定）／医師が理解を確認") && k2.includes("説明と一致（AI判定）／医師未確認"), "カルテ: 理解の確認（AI の判定と医師の判断を分ける）");
  await click("dialog .btn-quiet", "閉じる");

  await click("button", "診察を終える");
  parentPort.postMessage({ log: "診察を終えた" });
  await new Promise((r) => setTimeout(r, 1500));
}

async function patientFlow(sid) {
  await open(`${BASE}/patient.html?role=patient&s=${sid}`, "patient");
  await click(".p-option", "中文");
  await waitFor(() => text().includes("开始之前"), "同意の画面");
  check(text().includes("Google Gemini") && text().includes("问诊结束后，对话内容会被删除"), "同意の画面（AI の利用・記録の削除を説明）");
  await click(".btn-primary", "同意并开始");
  await waitFor(() => $("#draft"), "話す画面");

  // ── シナリオ A ──
  type($("#draft"), A_TEXT);
  await click(".p-input button", "下一步");
  await waitFor(() => text().includes("医生会看到"), "逆翻訳の確認");
  check(text(".p-big").includes("头晕"), "医師にどう伝わるかを患者の言語で確認");
  await click(".btn-primary", "发送");
  const q = await waitFor(() => byText(".p-doctor", "医生想确认"), "医師の確認質問");
  check(q.textContent.includes("您说的“头晕”，是哪一种感觉？") && q.querySelectorAll(".p-option").length === 3, "確認質問と選択肢（中国語）");
  check(byText(".p-links .btn-link", "我不知道") && byText(".p-links .btn-link", "都不是"), "「分からない」「どれでもない」");
  await click(".p-doctor .p-option", "天旋地转");
  await waitFor(() => text().includes("你的回答"), "答えを送った");
  check(byText(".p-answered .btn-link", "修改回答"), "回答を直せる");

  // ── シナリオ B（不一致 → 再説明 → 再確認）──
  const tb = await waitFor(() => byText(".p-doctor", "确认理解"), "理解の確認の依頼");
  check(tb.textContent.includes("今天请先不要回家") && tb.textContent.includes("用你自己的话"), "元の説明と依頼文");
  check(text(".p-speak .hint").includes("用你自己的话说"), "自分の言葉で話すよう案内");
  for (const answer of [B_WRONG, B_RIGHT]) {
    if (answer === B_RIGHT) await waitFor(() => byText(".p-doctor", "确认理解") && text().includes("今天请不要回家。"), "再説明後の依頼");
    type($("#draft"), answer);
    await click(".p-input button", "下一步");
    await click(".btn-primary", "发送");
    await waitFor(() => text().includes("已发送") || text().includes("医生已读"), "送信済み");
  }
  // ── シナリオ C ──
  await waitFor(() => byText(".p-doctor", "确认理解") && text().includes("这个药一天吃三次"), "C の依頼");
  type($("#draft"), C_RIGHT);
  await click(".p-input button", "下一步");
  await click(".btn-primary", "发送");
  await waitFor(() => text().includes("问诊已结束"), "診察終了で記録が消えた画面", 60000);
  check(true, "診察終了: 患者の画面も終了になる");
}

try {
  if (role === "doctor") await doctorFlow();
  else await patientFlow(await new Promise((ok) => parentPort.once("message", (m) => ok(m.sid))));
  parentPort.postMessage({ done: true, ok: true, pageErrors });
} catch (e) {
  parentPort.postMessage({ done: true, ok: false, error: e.message, pageErrors });
}
process.exit(0);
