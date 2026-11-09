// patient.html / doctor.html で共通の小さな部品（DOM を作る関数など）。Firebase には触らない。

// h("div", {class: "x", onclick}, "text", child) → 要素。属性の値が false / null なら付けない
export function h(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c != null && c !== false) e.append(c);
  return e;
}

import { iconSvg } from "./icons.js";
export { icon } from "./icons.js";

export const MIC_SVG = iconSvg("mic");
export const STOP_SVG = iconSvg("square", { fill: true });

// 原文のうち target の所を <mark> にする
export function highlight(original, target) {
  const i = target ? original.indexOf(target) : -1;
  if (i < 0) return [original];
  return [original.slice(0, i), h("mark", {}, target), original.slice(i + target.length)];
}

// ── 待ちのまま止まった発言を見つける ──
// 通信が切れる・サーバーが途中で落ちると status が analyzing（や回答済みの clarifying）のまま変わらず、
// 画面が回り続けてしまう。同じ発言・同じ待ち状態を最初に見た時刻を覚え、STUCK_MS を過ぎるか、
// API の呼び出しが失敗したと分かったら（fail）「もう一度」を出す。onTick は数秒ごとに呼ぶ（画面を描き直す）
export const STUCK_MS = 25000;
export function stuckWatch(onTick, everyMs = 3000) {
  const since = new Map();
  const failed = new Set();
  const waitingAs = (m) => (m.status === "analyzing" ? "analyzing"
    : m.status === "clarifying" && m.clarification?.answer ? "answered" : null);
  setInterval(onTick, everyMs);
  return {
    isStuck(m) {
      const w = waitingAs(m);
      if (!w) return false;
      if (failed.has(m.id)) return true;
      const k = `${m.id}:${w}`;
      if (!since.has(k)) since.set(k, Date.now());
      return Date.now() - since.get(k) > STUCK_MS;
    },
    fail(id) { failed.add(id); },
    // 「もう一度」を押したとき: もう一度 STUCK_MS 待つ
    reset(m) {
      failed.delete(m.id);
      const w = waitingAs(m);
      if (w) since.set(`${m.id}:${w}`, Date.now());
    },
  };
}

let toastTimer;
export function toast(text, ms = 4000) {
  let t = document.querySelector(".toast");
  if (!t) document.body.append((t = h("div", { class: "toast", role: "status" })));
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

// 押して話す（タップで開始・もう一度タップで止める）。onText(text) に文字起こしを渡す。
// 状態が変わるたびに onChange("idle" | "recording" | "transcribing") を呼ぶ
export function micController({ Recorder, transcribe, onText, onChange, onError }) {
  const rec = new Recorder();
  let state = "idle";
  const set = (s) => { state = s; onChange(s); };
  return {
    get state() { return state; },
    async toggle() {
      try {
        if (state === "idle") { await rec.start(); set("recording"); return; }
        if (state !== "recording") return;
        set("transcribing");
        const wav = await rec.stop();
        const text = wav ? (await transcribe(wav)).text : "";
        set("idle");
        onText(text ?? "");
      } catch (e) {
        await rec.stop().catch(() => {});
        set("idle");
        onError(e);
      }
    },
    close: () => rec.close(),
  };
}

// ── 問診メモ（医師画面と患者の「自分のメモ」で共通）──
// 症状ごとに「いつから・頻度・程度」をまとめる。いつから等は同じ発言の症状に付け、
// 症状の無い発言（例「1週間くらい」）は直前の症状に付ける。言い直したら最新を出し、前の値は prev に残す。
// 薬・アレルギー・その他は症状とは別に並べる。
//   item(f, m) → {text, confirmed, negated, uncertain}（表示する文字。医師は日本語、患者は本人の言語）
//   key(text)  → 同じ内容かを比べる鍵
export const ATTRS = ["onset", "frequency", "severity"];
export function buildMemo(msgs, item, key) {
  const symptoms = [];   // [{...item, attrs: {onset: {...item, prev: [text]}}}]
  const others = new Map(); // kind → [item]
  const findSymptom = (it) => symptoms.find((s) => key(s.text) === key(it.text));
  for (const m of msgs) {
    if (m.from !== "patient" || !["sent", "received"].includes(m.status)) continue;
    const facts = m.facts ?? [];
    const mine = []; // この発言で出た症状
    for (const f of facts.filter((f) => f.kind === "symptom")) {
      const it = item(f, m);
      let s = findSymptom(it);
      if (s) Object.assign(s, { ...it, attrs: s.attrs }); // 同じ症状を言い直した: 新しい言い方で
      else symptoms.push((s = { ...it, attrs: {} }));
      mine.push(s);
    }
    for (const f of facts.filter((f) => ATTRS.includes(f.kind))) {
      const targets = mine.length ? mine : symptoms.slice(-1);
      const it = item(f, m);
      if (!targets.length) { // 症状がまだ無い: その他として出す
        others.set("other", [...(others.get("other") ?? []).filter((x) => key(x.text) !== key(it.text)), it]);
        continue;
      }
      for (const s of targets) {
        const old = s.attrs[f.kind];
        const prev = old && key(old.text) !== key(it.text) ? [...old.prev, old.text] : old?.prev ?? [];
        s.attrs[f.kind] = { ...it, prev };
      }
    }
    for (const f of facts.filter((f) => f.kind !== "symptom" && !ATTRS.includes(f.kind))) {
      const it = item(f, m);
      others.set(f.kind, [...(others.get(f.kind) ?? []).filter((x) => key(x.text) !== key(it.text)), it]);
    }
  }
  return { symptoms, others, count: symptoms.length + [...others.values()].flat().length };
}

// 確認した語を含む項目か（本人の答えで置き換える対象）
export const isConfirmed = (f, m) => {
  const c = m.clarification;
  return Boolean(c?.answer && c.answer.type !== "unsure" && c.target && f.text.includes(c.target));
};
