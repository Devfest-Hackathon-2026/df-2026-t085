// devtools/devserver.py が web/unsaid.js の代わりに配る部品。関数の名前と動きは web/unsaid.js と同じ。
// Firebase の代わりに devserver の /_dev/...（メモリ上の Firestore もどき）と /api/...（main.py そのもの）を使う。
// ローカルの確認専用（本番の Hosting には載らない。web/ の外に置いている）。
import qrcode from "./vendor/qrcode.js";

export { Recorder } from "./stt.js";
export const API_URL = `${location.origin}/api`;
export const PATIENT_LANGS = ["en", "zh-CN", "vi"];
const TS = { __serverTimestamp: true };

// タブごとに別の人（医師のタブと患者のタブを分けられる）
export async function login() {
  let uid = sessionStorage.getItem("unsaid:local-uid");
  if (!uid) {
    uid = (await (await fetch("/_dev/login", { method: "POST" })).json()).uid;
    sessionStorage.setItem("unsaid:local-uid", uid);
  }
  return { uid, getIdToken: async () => `local:${uid}` };
}

async function dev(path, body) {
  const { uid } = await login();
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", Authorization: `Bearer local:${uid}` },
    body: body ? JSON.stringify(body) : undefined,
  });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(out.detail ?? res.statusText), { code: res.status === 403 ? "permission-denied" : "unknown" });
  return out;
}

// 独立ステージングの参加コード（web/unsaid.js）。ローカルの開発サーバーには無いので何もしない
export async function unlockStaging() {}

export async function createSession() {
  const user = await login();
  const r = await dev("/_dev/add", { path: "sessions", data: { patientLang: null, doctorLang: "ja", members: [user.uid], createdAt: TS } });
  return r.id;
}

export async function mySession(sid) {
  const user = await login();
  try {
    const { data } = await dev(`/_dev/get?path=${encodeURIComponent(`sessions/${sid}`)}`);
    return data && data.members?.includes(user.uid) ? data : null;
  } catch (e) {
    if (e.code === "permission-denied") return null;
    throw e;
  }
}

export async function joinSession(sid, patientLang) {
  const user = await login();
  if (await mySession(sid)) return;
  const data = { members: { __arrayUnion: [user.uid] } };
  if (patientLang) data.patientLang = patientLang;
  await dev("/_dev/update", { path: `sessions/${sid}`, data });
}

export function joinUrl(sid, page = "try.html") {
  const u = new URL(page, location.href);
  u.search = new URLSearchParams({ role: "patient", s: sid }).toString();
  return u.toString();
}

export function qrSvg(text) {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  return qr.createSvgTag({ cellSize: 4, margin: 4, scalable: true });
}

// ── onSnapshot の代わり（ロングポーリング）──
let online = true;
const onlineCbs = new Set();
function setOnline(v) {
  if (v === online) return;
  online = v;
  onlineCbs.forEach((cb) => cb(v));
}
export function watchConnection(cb) {
  onlineCbs.add(cb);
  cb(online);
  return () => onlineCbs.delete(cb);
}

function poll(path, kind, cb, onError) {
  let stopped = false;
  let since = -1;
  let last;
  (async () => {
    while (!stopped) {
      try {
        const { uid } = await login();
        const res = await fetch(`/_dev/watch?path=${encodeURIComponent(path)}&kind=${kind}&since=${since}`,
          { headers: { Authorization: `Bearer local:${uid}` } });
        if (res.status === 403) { stopped = true; onError({ code: "permission-denied" }); return; }
        const j = await res.json();
        setOnline(true);
        since = j.version;
        const s = JSON.stringify(j.data);
        if (!stopped && s !== last) { last = s; cb(j.data); }
      } catch (e) {
        setOnline(false);
        await new Promise((ok) => setTimeout(ok, 1000));
      }
    }
  })();
  return () => { stopped = true; };
}

export const watchSession = (sid, cb, onError = console.error) => poll(`sessions/${sid}`, "doc", cb, onError);
export const watchMessages = (sid, cb, onError = console.error) =>
  poll(`sessions/${sid}/messages`, "collection", (rows) => cb(rows ?? []), onError);

export class ApiError extends Error {
  constructor(status, detail) {
    super(`${status}: ${detail}`);
    this.status = status;
  }
}

async function post(path, body, { allow409 = false } = {}) {
  const { uid } = await login();
  let res;
  try {
    res = await fetch(API_URL + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer local:${uid}` },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(100000),
    });
  } catch (e) {
    throw new ApiError(0, e.name === "TimeoutError" ? "timeout" : "network");
  }
  if (allow409 && res.status === 409) return null;
  const out = await res.json().catch(() => ({}));
  if (res.ok) return out;
  throw new ApiError(res.status, out.detail ?? res.statusText);
}
const callApi = (path, sid, mid) => post(path, mid ? { sessionId: sid, messageId: mid } : { sessionId: sid }, { allow409: true });

export async function say(sid, from, text, reply = null, onCreated = null) {
  const r = await dev("/_dev/add", { path: `sessions/${sid}/messages`,
    data: { from, original: text, status: "analyzing", createdAt: TS, ...(reply ?? {}) } });
  onCreated?.(r.id);
  await callApi(from === "patient" ? "/analyze" : "/translate", sid, r.id).catch((e) => {
    e.messageId = r.id;
    throw e;
  });
  return r.id;
}

export const reply = (sid, questionId, text, type = "free_text", index, onCreated = null) =>
  say(sid, "patient", text, { replyTo: questionId, answer: index == null ? { type } : { type, index } }, onCreated);

export async function answer(sid, mid, type, text = "") {
  await dev("/_dev/update", { path: `sessions/${sid}/messages/${mid}`, data: { "clarification.answer": { type, text } } });
  await callApi("/translate", sid, mid).catch((e) => {
    e.messageId = mid;
    throw e;
  });
}

export const send = (sid, mid) => callApi("/send", sid, mid);
// web/unsaid.js と同じ: 端末の中で二重に始めず、サーバーで status が sent のときだけ書く（トランザクションの代わりに expect）
const receiving = new Set();
export async function receive(sid, mid) {
  if (receiving.has(mid)) return;
  receiving.add(mid);
  try {
    await dev("/_dev/update", { path: `sessions/${sid}/messages/${mid}`, data: { status: "received", receivedAt: TS }, expect: { status: "sent" } });
  } catch (e) {
    receiving.delete(mid);
    throw e;
  }
}
export function retry(sid, m) {
  const path = m.from === "patient" && !m.clarification?.answer ? "/analyze" : "/translate";
  return callApi(path, sid, m.id);
}
export const endSession = (sid) => callApi("/end", sid);

export const sendQuestion = (sid, q) => post("/clarify/send", { sessionId: sid, ...q });
export const updateCandidate = (sid, messageId, candidateId, action) =>
  post("/clarify/update", { sessionId: sid, messageId, candidateId, action });
export const updateFact = (sid, messageId, factId, action) =>
  post("/fact/update", { sessionId: sid, messageId, factId, action });
export const startTeachback = (sid, messageId, previousId = null) =>
  post("/teachback/start", { sessionId: sid, messageId, previousId });
export const resolveTeachback = (sid, messageId, action) =>
  post("/teachback/resolve", { sessionId: sid, messageId, action });
export const retryMeaning = (sid, messageId) => post("/meaning/retry", { sessionId: sid, messageId }, { allow409: true });

// ── USSA: 発言者の確認・取り消し・未検証の共有・未解決事項（すべてサーバーが状態を変える）──
// 自分の発言の意味を選ぶ（choice: "i1" など）・自分の言葉で説明する（"free_text" + text）・どれでもない（"none" → 取り消し）
export const chooseIntent = (sid, messageId, choice, text = "") => post("/intent", { sessionId: sid, messageId, choice, text });
// 訳の検証で食い違いが残った自分の発言: "send_unverified"（未検証として送る）/ "rephrase"（言い直す）
export const reviewMessage = (sid, messageId, action) => post("/review", { sessionId: sid, messageId, action });
// 送信を取り消す（解析中・確認中）。返り値の original を入力欄に戻せる
export const cancelMessage = (sid, messageId) => post("/cancel", { sessionId: sid, messageId });
// 医師: まだ届いていない患者の発言を、未検証と分かる形で見る（見たことは記録に残る）
export const reveal = (sid, messageId) => post("/reveal", { sessionId: sid, messageId });
// 医師: 未解決事項を "hold" / "unhold" / "resolve" / "dismiss" / "reopen"
export const updateIssue = (sid, issueId, action, note = "") => post("/issue/update", { sessionId: sid, issueId, action, note });

export async function transcribe(sid, speaker, wav) {
  const { uid } = await login();
  const res = await fetch(`${API_URL}/transcribe?${new URLSearchParams({ sessionId: sid, speaker })}`,
    { method: "POST", headers: { "Content-Type": "audio/wav", Authorization: `Bearer local:${uid}` }, body: wav });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `transcribe ${res.status}`);
  return body;
}
