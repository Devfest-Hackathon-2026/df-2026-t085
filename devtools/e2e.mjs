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
const D_ASK = "このまま検査を続けてもいいですか？";
const D_FINE = "没事。";
const F_TEXT = "この薬は空腹時に飲まないでください。";
const G_TEXT = "検査の前は8時間何も食べないでください。";
const E_DOSE = "この薬は1日2回、朝と夜に1錠ずつ飲んでください。";
const E_WRONG = "早上吃两片就可以了，对吗？";

async function doctorSays(text) {
  type($("#dinput"), text);
  await click(".d-input .send", "送る");
}
// その文の医師の吹き出しが「送信済み」になったら返す（直前の吹き出しを見て先に進まないように、文で探す）
const sentBubble = (t) => $$(".d-mine").find((e) => e.textContent.includes(t) && e.textContent.includes("送信済み"));

async function doctorFlow() {
  await open(`${BASE}/doctor.html`, "doctor");
  await click("button", "問診を始める");
  await waitFor(() => $(".qr-overlay svg"), "QR");
  const sid = new URLSearchParams(location.search).get("s");
  check(sid && sid.length >= 16, "問診を作り、QR を出す");
  parentPort.postMessage({ sid });
  await waitFor(() => !$(".qr-overlay") && $(".d-app"), "患者が参加して QR が閉じる");
  check(text(".d-head").includes("中国語"), "患者（中国語）と接続中");

  // ── シナリオ A: 1回の送信操作 → 意図の解析・照合を通って届く → 問診メモ・確認候補 ──
  await waitFor(() => text(".d-log").includes("駅で突然めまい"), "患者の発言（翻訳）が届く");
  check(text(".d-pt.latest .d-meta").includes("訳を照合済み"), "訳の照合の状態を表示");
  await waitFor(() => $(".d-cands"), "確認候補");
  check(text(".d-memo").includes("めまい") && byText(".d-memo .tag", "未確認"), "問診メモ: めまい［未確認］");
  check(text(".d-memo").includes("不安の訴え（本人の言葉）"), "本人の言葉の不安を残す");
  check(!text(".d-memo").includes("貧血"), "原文に無い情報（貧血）は出さない");
  check(text(".d-memo").includes("言及なし: 既往歴・服薬・アレルギー"), "言及なしを区別する");
  check(text(".d-cands-head").includes("1件") && text(".d-cand").includes("头晕"), "確認候補 1件（头晕）");
  await click(".d-fact", "めまい");
  const ev = await waitFor(() => $(".d-evidence"), "根拠");
  check(ev.querySelector("mark")?.textContent === "头晕" && ev.textContent.includes("AIが曖昧さを指摘"), "原文の根拠（头晕）と履歴");
  await click(".d-fact", "めまい");
  await click(".d-cand .btn-small", "質問を送る");
  await waitFor(() => byText(".d-memo .tag", "患者回答"), "問診メモが患者の答えで更新される");
  check(text(".d-memo").includes("めまい：周囲がぐるぐる回る（回転性）") && !$(".d-memo .check"), "患者回答あり（医師確認済みではない）");
  await click(".d-fact", "めまい");
  await click(".d-evidence .btn-small", "確認済みにする");
  await waitFor(() => $('.d-memo .check[aria-label="医師確認済み"]'), "医師確認済み");
  check(true, "医師が確認済みにした");

  // ── シナリオ D: 「没事」の意味が文脈で決まらない → 患者本人が選ぶ。医師は未検証の内容を見られる ──
  await doctorSays(D_ASK);
  await waitFor(() => sentBubble(D_ASK), "D の質問を送信");
  await waitFor(() => text(".d-pending").includes("意味を確認しています"), "患者が意味を確認中");
  check(!text(".d-log").includes("検査を続けてください"), "意図が決まるまで訳は届かない");
  await click(".d-pending .btn-link", "未検証の内容を見る");
  const rv = await waitFor(() => $(".d-reveal"), "未検証の原文");
  check(rv.textContent.includes(D_FINE) && rv.textContent.includes("未検証・意味は未確定"), "緊急時: 未検証と分かる形で原文を見られる");
  await waitFor(() => text(".d-log").includes("はい、検査を続けてください（同意）。"), "本人が選んだ意味で届く");
  check(text(".d-pt.latest .d-meta").includes("本人が意味を確認"), "本人が意味を確認（AI の解釈と区別）");

  // ── シナリオ F: 否定が落ちた訳 → 照合で見つけて訳し直してから届く ──
  await doctorSays(F_TEXT);
  await waitFor(() => sentBubble(F_TEXT), "F を送信（訳し直し済み）");
  check(!$(".d-mine .d-tbres.warn"), "訳し直しで解決したので医師の確認は不要");

  // ── シナリオ G: 数が落ちた訳 → 訳し直しても残る → 自動では送らない → 医師が決める ──
  await doctorSays(G_TEXT);
  const card = await waitFor(() => byText(".d-mine", G_TEXT)?.querySelector(".d-tbres.warn"), "訳の照合で相違");
  check(card.textContent.includes("まだ患者には届いていません") && card.textContent.includes("8"), "相違の内容（8時間）を示し、送っていない");
  await click(".d-mine .d-tbres.warn .btn-link", "未検証として送る");
  await waitFor(() => sentBubble(G_TEXT)?.textContent.includes("訳は未検証"), "未検証として送信");
  check(true, "医師が「未検証として送る」を選んだ（印が付く）");

  // ── シナリオ E: 服薬の理解の相違（Conversation Auditor）──
  await doctorSays(E_DOSE);
  await waitFor(() => sentBubble(E_DOSE), "E の説明を送信");
  const iss = await waitFor(() => $$(".d-cands").find((x) => x.textContent.includes("未解決事項")), "未解決事項");
  check(iss.textContent.includes("1件") && iss.textContent.includes("指示の理解の相違") && iss.textContent.includes("tablet"), "未解決事項 1件（服薬の理解の相違）");
  await click(".d-cands .d-more", "根拠（2件の発言）");
  const ev2 = await waitFor(() => $$(".d-cands .d-evidence").at(-1), "未解決事項の根拠");
  check(ev2.textContent.includes(E_DOSE) && ev2.textContent.includes(E_WRONG), "根拠の発言（説明と患者の言い直し）を開ける");
  await click(".d-cands .btn-link", "解決済み");
  await waitFor(() => !$$(".d-cands").some((x) => x.textContent.includes("未解決事項")), "解決済みにすると消える");
  check(true, "医師が解決済みにした");

  // ── シナリオ B: 理解の不一致 → 再説明 → 再確認 ──
  await doctorSays(B_EXPLAIN);
  await waitFor(() => byText(".d-mine", B_EXPLAIN)?.textContent.includes("送信済み"), "説明が送信済み");
  await click(".d-mine.last .tb", "理解を確認");
  const warn = await waitFor(() => $(".d-pt .d-tbres.warn"), "認識の相違");
  check(warn.textContent.includes("認識の相違の可能性") && warn.textContent.includes("明日また来ればよい"), "不一致を検出（今日待つ ⇔ 明日来る）");
  await waitFor(() => $$(".d-cands").some((x) => x.textContent.includes("理解の相違")), "未解決事項にも載る");
  check(true, "理解の相違が未解決事項に載る");
  await click(".d-pt .d-tbres.warn .btn-link", "再説明する");
  await waitFor(() => $("#dinput").value.includes("今日は帰らないでください"), "再説明の文が入力欄に入る");
  await click(".d-input .send", "送る");
  await waitFor(() => byText(".d-mine.last", "今日は帰らないでください")?.textContent.includes("送信済み"), "再説明を送信");
  await click(".d-mine.last .tb", "理解を確認");
  const ok = await waitFor(() => $$(".d-tbres.ok").at(-1), "再確認で一致");
  check(ok.textContent.includes("説明と一致"), "再確認: 説明と一致");
  await click(".d-tbres.ok .btn-link", "理解を確認済みにする");
  await waitFor(() => byText(".d-tbres.ok .badge-ok", "医師が理解を確認"), "医師が理解を確認");
  await waitFor(() => !$$(".d-cands").some((x) => x.textContent.includes("未解決事項")), "前の相違も解決になる");
  check(true, "理解を確認済みにすると、再説明の前の相違も解決");

  // ── シナリオ C: 正しい理解には警告を出さない ──
  await doctorSays(C_EXPLAIN);
  await waitFor(() => byText(".d-mine.last", C_EXPLAIN)?.textContent.includes("送信済み"), "説明 C を送信");
  await click(".d-mine.last .tb", "理解を確認");
  await waitFor(() => $$(".d-pt .d-tbres").length === 3, "C の結果");
  check($$(".d-pt .d-tbres.warn").length === 1 && $$(".d-pt .d-tbres.ok").length === 2, "正しい理解には警告を出さない");

  await click("button", "カルテにコピー");
  const k = (await waitFor(() => $("dialog.d-sheet textarea"), "カルテ")).value;
  check(k.includes("■主訴") && k.includes("めまい：周囲がぐるぐる回る（回転性）［医］"), "カルテ: 主訴・医師確認済み");
  check(k.includes("認識の相違の可能性（AI判定）／医師: 再説明が必要") && k.includes("説明と一致（AI判定）／医師が理解を確認"), "カルテ: 理解の確認");
  check(!k.includes("貧血"), "カルテ: 原文に無いものは無い");
  type($("dialog.d-sheet textarea"), k + "\n医師メモ: 追記");
  await click("dialog .btn-primary", "コピー");
  check(clipboard.endsWith("医師メモ: 追記"), "医師が編集した文をコピーできる");

  await click("button", "診察を終える");
  parentPort.postMessage({ log: "診察を終えた" });
  await new Promise((r) => setTimeout(r, 1500));
}

async function patientSays(text) {
  type($("#draft"), text);
  await click(".p-input button", "发送");
}

async function patientFlow(sid) {
  await open(`${BASE}/patient.html?role=patient&s=${sid}`, "patient");
  await click(".p-option", "中文");
  await waitFor(() => text().includes("开始之前"), "同意の画面");
  check(text().includes("Google Gemini") && text().includes("问诊结束后，对话内容会被删除"), "同意の画面（AI の利用・記録の削除を説明）");
  await click(".btn-primary", "同意并开始");
  await waitFor(() => $("#draft"), "話す画面");

  // ── A: 送信は1回だけ。届いた後に、医師が読んだ内容を自分の言語で見られる ──
  await patientSays(A_TEXT);
  await waitFor(() => text().includes("医生看到的是") || text().includes("医生想确认"), "送信して医師に届いた");
  check(!text().includes("医生会看到"), "訳の承認（2回目の送信）は求めない");
  const q = await waitFor(() => byText(".p-doctor", "医生想确认"), "医師の確認質問");
  check(q.querySelectorAll(".p-option").length === 3 && byText(".p-links .btn-link", "我不知道") && byText(".p-links .btn-link", "都不是"),
    "確認質問・選択肢・分からない・どれでもない");
  await click(".p-doctor .p-option", "天旋地转");
  await waitFor(() => text().includes("你的回答"), "答えを送った");

  // ── D: 「没事」→ 自分の発言の意味を選ぶ ──
  await waitFor(() => byText(".p-doctor", "我们可以继续检查吗"), "医師の質問 D");
  await patientSays(D_FINE);
  await waitFor(() => text().includes("您说的“没事”是什么意思？"), "意図の確認");
  check($$(".p-option").length === 3 && byText(".p-links .btn-link", "都不是，我重新说") && byText(".p-links .btn-link", "用我自己的话说明"),
    "意味の選択肢・どれでもない・自分の言葉で");
  await new Promise((r) => setTimeout(r, 600)); // 医師が「未検証の内容を見る」を押す間
  await click(".p-option", "可以，请继续检查。");
  await waitFor(() => text().includes("已发送") || text().includes("医生已读"), "送信済み");

  // ── F: 訳し直された正しい訳が届く ／ G: 未検証の印つきで届く ──
  await waitFor(() => byText(".p-doctor", "请不要空腹吃这个药"), "訳し直された F");
  check(!text().includes("请空腹时吃这个药"), "否定が落ちた訳は届かない");
  const g = await waitFor(() => byText(".p-doctor", "检查前请不要吃东西"), "G（未検証）");
  check(g.textContent.includes("这句翻译未能完全核对"), "未検証の訳には患者の言語で注意を出す");

  // ── E: 服薬の理解を言い直す（食い違いは医師の画面の未解決事項に出る）──
  await waitFor(() => byText(".p-doctor", "这个药一天吃两次"), "E の説明");
  await patientSays(E_WRONG);
  const read = await waitFor(() => $(".p-sent-read"), "送信後に医師が読んだ内容");
  check(read.textContent.includes("医生看到的是") && read.textContent.includes("早上吃两片"), "送った後に、医師に届いた内容を自分の言語で見られる");

  // ── B: 理解の確認（不一致 → 再説明 → 再確認）──
  const tb = await waitFor(() => byText(".p-doctor", "确认理解"), "理解の確認の依頼");
  check(tb.textContent.includes("今天请先不要回家") && tb.textContent.includes("用你自己的话"), "元の説明と依頼文");
  await patientSays(B_WRONG);
  await waitFor(() => byText(".p-doctor", "确认理解") && text().includes("今天请不要回家。"), "再説明後の依頼");
  await patientSays(B_RIGHT);
  // ── C ──
  await waitFor(() => byText(".p-doctor", "确认理解") && text().includes("这个药一天吃三次"), "C の依頼");
  await patientSays(C_RIGHT);
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
