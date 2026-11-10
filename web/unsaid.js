// Kumu の画面と裏側（Firebase + Cloud Run）をつなぐ部品。画面はこのファイルの関数だけを呼べばよい。
// 中身は docs/api.md の約束そのまま。つなぎ方の手順は docs/connect-ui.md。
//
//   import * as unsaid from "./unsaid.js";
//   await unsaid.login();
//   const sid = await unsaid.createSession();            // 医師の端末。joinUrl(sid) を qrSvg() で QR にして見せる
//   await unsaid.joinSession(sid, "en");                 // 患者のスマホ（QR を読み、言語を選んだ後）
//   const stop = unsaid.watchMessages(sid, (msgs) => render(msgs));   // 表示はすべてここから
//   await unsaid.say(sid, "patient", "I've had a headache for a while.");
//
// npm の firebase を使う（Vite / React など）場合は、下の import の URL を "firebase/app" などに替えるだけ。
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, signInAnonymously, signInWithEmailAndPassword, signOut } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import {
  getFirestore, collection, doc, addDoc, getDoc, updateDoc, query, orderBy, onSnapshot, serverTimestamp, arrayUnion,
  runTransaction, getDocFromServer,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { transcribe as postWav } from "./stt.js";
import { firebaseConfig, API_URL, FIRESTORE_DB, ACCESS_GATE } from "./firebase-config.js";
import qrcode from "./vendor/qrcode.js";

export { Recorder } from "./stt.js";
export { API_URL };
export const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db = FIRESTORE_DB === "(default)" ? getFirestore(app) : getFirestore(app, FIRESTORE_DB);

// 匿名ログイン。同じブラウザなら再読み込みしても同じ uid のまま（session に入り直さなくてよい）。
// 同じ PC で患者と医師を試すときは、片方をシークレットウィンドウにすると別の uid になる。
export async function login() {
  await auth.authStateReady();
  return auth.currentUser ?? (await signInAnonymously(auth)).user;
}

// 医師のログイン（メール＋パスワード）。患者はログインなし（上の匿名ログイン）のまま。
// session を作れるのはこのログインをした医師だけ（firestore.rules の isDoctorAccount）
export async function doctorSignedIn() {
  await auth.authStateReady();
  return auth.currentUser?.providerData.some((p) => p.providerId === "password") ?? false;
}

export async function doctorLogin(email, password) {
  return (await signInWithEmailAndPassword(auth, email, password)).user;
}

export const logout = () => signOut(auth);

const msgRef = (sid, mid) => doc(db, "sessions", sid, "messages", mid);

// 患者が選べる言語（unsaid_ai.py の LANGS / firestore.rules と同じ）
export const PATIENT_LANGS = ["en", "zh-CN", "vi"];

// 消える期限（Firestore の TTL）。端末が作る文書にも最初から付ける（firestore.rules の expiresSoon）。サーバーが書くたびに延ばす
const TTL_MS = 24 * 3600 * 1000;
const expireAt = () => new Date(Date.now() + TTL_MS);

// 医師の端末（doctorLogin の後）: session を作り、id を返す。作った人が医師（members[0]）になる（firestore.rules）。
// patientLang は患者が QR から参加するときに決まる（それまで null）
export async function createSession() {
  const user = await login();
  const ref = await addDoc(collection(db, "sessions"), {
    patientLang: null, doctorLang: "ja", members: [user.uid], createdAt: serverTimestamp(), expireAt: expireAt(),
  });
  return ref.id;
}

// 自分がメンバーの session なら中身を、まだ参加していなければ null を返す（再読み込みで入り直さないため）
export async function mySession(sid) {
  const user = await login();
  try {
    const s = await getDoc(doc(db, "sessions", sid));
    return s.exists() && s.data().members?.includes(user.uid) ? { id: s.id, ...s.data() } : null;
  } catch (e) {
    if (e.code === "permission-denied") return null; // メンバーでないと読めない
    throw e;
  }
}

// QR / URL の id で参加する。患者のスマホは patientLang を渡す（null の session に1回だけ書ける）。
// 参加できるのは作ってから 30 分以内・2人目まで（firestore.rules）。参加済みなら何もしない
export async function joinSession(sid, patientLang) {
  const user = await login();
  if (await mySession(sid)) return;
  const change = { members: arrayUnion(user.uid) };
  if (patientLang) change.patientLang = patientLang;
  try {
    await updateDoc(doc(db, "sessions", sid), change);
  } catch (e) {
    // 二重押し・別のタブで先に参加できていた（patientLang はもう書けないのでルールが断る）なら成功とみなす
    if (e.code === "permission-denied" && (await mySession(sid))) return;
    throw e;
  }
}

// 患者がスマホで開く URL。page は患者の画面（既定は try.html）。この URL を qrSvg() で QR にする
export function joinUrl(sid, page = "try.html") {
  const u = new URL(page, location.href);
  u.search = new URLSearchParams({ role: "patient", s: sid }).toString();
  return u.toString();
}

// 文字列 → QR の <svg> 文字列（端末の中で作る。外部サービスには送らない）。el.innerHTML = qrSvg(url) で表示
export function qrSvg(text) {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  return qr.createSvgTag({ cellSize: 4, margin: 4, scalable: true });
}

// session の中身（patientLang など）。メンバーになってから呼ぶ
export function watchSession(sid, cb, onError = console.error) {
  return onSnapshot(doc(db, "sessions", sid), (s) => cb(s.exists() ? { id: s.id, ...s.data() } : null), onError);
}

// 発言の一覧を古い順に。status / clarification / translation / meaning が書き足されるたびに cb が呼ばれる。
// meaning（問診メモ・確認候補）は status が変わった少し後に届くので、「届いたら出す」書き方にする。戻り値を呼ぶと止まる
export function watchMessages(sid, cb, onError = console.error) {
  const q = query(collection(db, "sessions", sid, "messages"), orderBy("createdAt"));
  let first = true;
  return onSnapshot(q, { includeMetadataChanges: true }, (snap) => {
    setOnline(!snap.metadata.fromCache);
    if (!first && !snap.docChanges().length) return; // 同期の状態（オンライン・書き込み済みか）だけが変わった
    first = false;
    cb(snap.docs.map((d) => ({ id: d.id, ...d.data({ serverTimestamps: "estimate" }) })));
  }, onError);
}

// つながっているか（端末の通信と Firestore の同期）。切れても Firestore は自分で再接続し、書いたものは後から届く。
// cb(true | false) は状態が変わったときだけ呼ばれる
let online = navigator.onLine;
const onlineCbs = new Set();
function setOnline(v) {
  v = v && navigator.onLine;
  if (v === online) return;
  online = v;
  onlineCbs.forEach((cb) => cb(v));
}
addEventListener("online", () => setOnline(true));
addEventListener("offline", () => setOnline(false));
export function watchConnection(cb) {
  onlineCbs.add(cb);
  cb(online);
  return () => onlineCbs.delete(cb);
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(`${status}: ${detail}`);
    this.status = status;
  }
}

// API の待ち時間の上限。Gemini の再試行をすべて使い切っても返る長さ（サーバーの LOCK_SEC より短く）。
// 超えたら諦めて「もう一度」を出す（発言は Firestore にあるので消えない）
const API_TIMEOUT_MS = 100000; // Cloud Run の --timeout 120 より短く

async function post(path, body, { allow409 = false } = {}) {
  const token = await (await login()).getIdToken();
  let res;
  try {
    res = await fetch(API_URL + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(API_TIMEOUT_MS),
    });
  } catch (e) {
    throw new ApiError(0, e.name === "TimeoutError" ? "timeout" : "network");
  }
  if (allow409 && res.status === 409) return null; // 二重押し。無視してよい
  const out = await res.json().catch(() => ({}));
  if (res.ok) return out;
  throw new ApiError(res.status, out.detail ?? res.statusText);
}

// 独立ステージングだけ: 医師が参加コードを入れて、この問診で Gemini を使えるようにする（main.py の /staging/unlock）。
// コードはこのタブの sessionStorage にだけ置く（次の問診で聞き直さないため）。違えば消して聞き直す。本番では何もしない
const CODE_KEY = "kumu:stagingCode";
export async function unlockStaging(sid, ask = (msg) => window.prompt(msg)) {
  if (!ACCESS_GATE) return;
  let msg = "ステージングの参加コードを入力してください";
  for (let i = 0; i < 3; i++) {
    let code = null;
    try { code = sessionStorage.getItem(CODE_KEY); } catch { /* 使えなくても聞けばよい */ }
    code ||= ask(msg);
    if (!code) throw new ApiError(403, "参加コードが入力されませんでした");
    try {
      await post("/staging/unlock", { sessionId: sid, code: code.trim() });
      try { sessionStorage.setItem(CODE_KEY, code.trim()); } catch { /* 次の問診でもう一度聞く */ }
      return;
    } catch (e) {
      try { sessionStorage.removeItem(CODE_KEY); } catch { /* なし */ }
      if (e.status !== 403) throw e;
      msg = "参加コードが違います。もう一度入力してください";
    }
  }
  throw new ApiError(403, "参加コードが違います");
}

const callApi = (path, sid, mid) => post(path, mid ? { sessionId: sid, messageId: mid } : { sessionId: sid }, { allow409: true });

// 発言する（from は "patient" / "doctor"）。吹き出しは addDoc の時点で watchMessages に出る。
// await の後に画面を変えないこと（確認質問は onSnapshot の方が早く届く）
// API の呼び出しだけが失敗したとき（発言はもうある）、エラーに e.messageId が付く。
// その発言は画面で「もう一度」を出す（retry）。e.messageId が無ければ発言自体を作れていない
// reply = 医師の確認質問・理解の確認への答えのとき { replyTo: <質問の id>, answer: { type, index? } }（患者だけ）
// onCreated(id) は発言ができた（＝送信操作が受け付けられた）直後に呼ばれる。API の応答は、届けた後の処理
// （問診メモ・会話全体の見直し）まで待つので、二重押しの防止はここで解いてよい（次の発言をすぐ書ける）
export async function say(sid, from, text, reply = null, onCreated = null) {
  const ref = await addDoc(collection(db, "sessions", sid, "messages"), {
    from, original: text, status: "analyzing", createdAt: serverTimestamp(), expireAt: expireAt(), ...(reply ?? {}),
  });
  onCreated?.(ref.id);
  await callApi(from === "patient" ? "/analyze" : "/translate", sid, ref.id).catch((e) => {
    e.messageId = ref.id;
    throw e;
  });
  return ref.id;
}

// 患者: 確認質問に答える。type は "option"（text = 選んだ選択肢）/ "free_text"（text = 入力）/ "unsure"（text = ""）
// 回答は書けたが API の呼び出しが失敗したときは、say と同じく e.messageId が付く
export async function answer(sid, mid, type, text = "") {
  await updateDoc(msgRef(sid, mid), { "clarification.answer": { type, text } });
  await callApi("/translate", sid, mid).catch((e) => {
    e.messageId = mid;
    throw e;
  });
}

// 患者: 医師の確認質問に答える。type は "option"（index = 選んだ選択肢の番号、text = その文字）/ "unsure" / "none" /
// "free_text"（自分の言葉。逆翻訳を見てから送る）。理解の確認（teach-back）には free_text で答える
export const reply = (sid, questionId, text, type = "free_text", index, onCreated = null) =>
  say(sid, "patient", text, { replyTo: questionId, answer: index == null ? { type } : { type, index } }, onCreated);

// 患者: 逆翻訳を見て「送信」（status confirmed のときだけ）。サーバーが送信済みにして、
// 確認質問・理解の確認への答えなら元の情報に結びつける
export const send = (sid, mid) => callApi("/send", sid, mid);

// 医師: 患者の発言を表示したら「受け取った」（status sent のときだけ）。
// watchMessages は書き込みが反映される前に同じ発言を何度も sent のまま渡すことがあり、医師が2台の端末で開いていることもある。
// ローカルキャッシュの反映に頼らず、トランザクションでサーバーの最新の status を読んで、sent のときだけ書く
// 2台が同時に書くと、後の方は（読み直しより先に）ルールで断られる（Firestore エミュレーターで確認）。そのときは
// サーバーから読み直し（キャッシュは使わない）、もう sent でなければ、ほかの端末が受け取ったので成功とみなす
const receiving = new Set();
export async function receive(sid, mid) {
  if (receiving.has(mid)) return; // この端末ではもう受け取りの処理を始めた
  receiving.add(mid);
  try {
    await runTransaction(db, async (tx) => {
      const snap = await tx.get(msgRef(sid, mid));
      if (snap.exists() && snap.data().status === "sent") {
        tx.update(msgRef(sid, mid), { status: "received", receivedAt: serverTimestamp() });
      }
    });
  } catch (e) {
    if (e.code === "permission-denied") {
      const now = await getDocFromServer(msgRef(sid, mid)).catch(() => null);
      if (now?.exists() && now.data().status !== "sent") return;
    }
    receiving.delete(mid); // 失敗したら、次のスナップショットでやり直せるように
    throw e;
  }
}

// 「もう一度」: status が error の発言、または待ちのまま止まった発言（ui.js の stuckWatch）を、
// 止まった所から呼び直す（m は watchMessages で受け取った1件）。サーバーがまだ処理中なら 409 で何もしない
export function retry(sid, m) {
  const path = m.from === "patient" && !m.clarification?.answer ? "/analyze" : "/translate";
  return callApi(path, sid, m.id);
}

// 医師: 「診察を終える」。この問診の発言と session をサーバーが消す（医師だけ。消したら戻せない）。
// 患者のスマホは session が読めなくなるので、終了の画面に変わる
export async function endSession(sid) {
  await callApi("/end", sid);
}

// ── 医師: 確認候補・情報の確認・理解の確認（すべてサーバーが状態を変える。端末は Firestore の結果を表示するだけ）──
// 確認候補をそのまま送る: { messageId, candidateId }。編集して送る: + questionDoctor。自分で質問を書く: { questionDoctor, messageId?, factId? }
export const sendQuestion = (sid, q) => post("/clarify/send", { sessionId: sid, ...q });
// action: "hold"（保留）/ "dismiss"（無視）/ "restore"（戻す）
export const updateCandidate = (sid, messageId, candidateId, action) =>
  post("/clarify/update", { sessionId: sid, messageId, candidateId, action });
// action: "confirm"（医師確認済み）/ "flag"（要修正）/ "reset"（取り消す）
export const updateFact = (sid, messageId, factId, action) =>
  post("/fact/update", { sessionId: sid, messageId, factId, action });
// 医師の説明（messageId）について「理解を確認」。再説明のときは前の理解の確認の id（previousId）を渡す
export const startTeachback = (sid, messageId, previousId = null) =>
  post("/teachback/start", { sessionId: sid, messageId, previousId });
// action: "confirm"（医師が理解を確認した）/ "reexplain"（再説明が必要）
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

// 音声 → 文字（Firestore には書かない）。返った text を入力欄に入れ、本人が確認してから say() する
export async function transcribe(sid, speaker, wav) {
  const token = await (await login()).getIdToken();
  return postWav(API_URL, token, sid, speaker, wav);
}
