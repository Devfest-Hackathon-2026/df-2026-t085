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
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { transcribe as postWav } from "./stt.js";
import { firebaseConfig, API_URL } from "./firebase-config.js";
import qrcode from "./vendor/qrcode.js";

export { Recorder } from "./stt.js";
export { API_URL };
export const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db = getFirestore(app);

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

// 医師の端末（doctorLogin の後）: session を作り、id を返す。作った人が医師（members[0]）になる（firestore.rules）。
// patientLang は患者が QR から参加するときに決まる（それまで null）
export async function createSession() {
  const user = await login();
  const ref = await addDoc(collection(db, "sessions"), {
    patientLang: null, doctorLang: "ja", members: [user.uid], createdAt: serverTimestamp(),
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
  await updateDoc(doc(db, "sessions", sid), change);
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

// 発言の一覧を古い順に。status / clarification / translation / facts が書き足されるたびに cb が呼ばれる。
// facts は status が変わった少し後に届くので、「届いたら出す」書き方にする。戻り値を呼ぶと止まる
export function watchMessages(sid, cb, onError = console.error) {
  const q = query(collection(db, "sessions", sid, "messages"), orderBy("createdAt"));
  return onSnapshot(q, (snap) => {
    cb(snap.docs.map((d) => ({ id: d.id, ...d.data({ serverTimestamps: "estimate" }) })));
  }, onError);
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(`${status}: ${detail}`);
    this.status = status;
  }
}

async function callApi(path, sid, mid) {
  const token = await (await login()).getIdToken();
  const res = await fetch(API_URL + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(mid ? { sessionId: sid, messageId: mid } : { sessionId: sid }),
  });
  if (res.ok || res.status === 409) return; // 409 は二重押し。無視してよい
  const body = await res.json().catch(() => ({}));
  throw new ApiError(res.status, body.detail ?? res.statusText);
}

// 発言する（from は "patient" / "doctor"）。吹き出しは addDoc の時点で watchMessages に出る。
// await の後に画面を変えないこと（確認質問は onSnapshot の方が早く届く）
// API の呼び出しだけが失敗したとき（発言はもうある）、エラーに e.messageId が付く。
// その発言は画面で「もう一度」を出す（retry）。e.messageId が無ければ発言自体を作れていない
export async function say(sid, from, text) {
  const ref = await addDoc(collection(db, "sessions", sid, "messages"), {
    from, original: text, status: "analyzing", createdAt: serverTimestamp(),
  });
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

// 患者: 逆翻訳を見て「送信」（status confirmed のときだけ）
export const send = (sid, mid) => updateDoc(msgRef(sid, mid), { status: "sent", sentAt: serverTimestamp() });

// 医師: 患者の発言を表示したら「受け取った」（status sent のときだけ）
export const receive = (sid, mid) => updateDoc(msgRef(sid, mid), { status: "received", receivedAt: serverTimestamp() });

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

// 音声 → 文字（Firestore には書かない）。返った text を入力欄に入れ、本人が確認してから say() する
export async function transcribe(sid, speaker, wav) {
  const token = await (await login()).getIdToken();
  return postWav(API_URL, token, sid, speaker, wav);
}
