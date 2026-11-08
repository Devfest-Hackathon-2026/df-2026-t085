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
  for (const c of children.flat()) if (c != null && c !== false) e.append(c);
  return e;
}

export const MIC_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
export const STOP_SVG = '<svg viewBox="0 0 24 24" fill="#fff" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>';

// 原文のうち target の所を <mark> にする
export function highlight(original, target) {
  const i = target ? original.indexOf(target) : -1;
  if (i < 0) return [original];
  return [original.slice(0, i), h("mark", {}, target), original.slice(i + target.length)];
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
