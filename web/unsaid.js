// UNSAID の画面と裏側（Firebase + Cloud Run）をつなぐ部品。画面はこのファイルの関数だけを呼べばよい。
// 中身は docs/api.md の約束そのまま。つなぎ方の手順は docs/connect-ui.md。
//
//   import * as unsaid from "./unsaid.js";
//   await unsaid.login();
//   const sid = await unsaid.createSession("en");        // 患者の端末（言語を選んだ後）
//   await unsaid.joinSession(sid);                       // 医師の端末（QR / URL の ?s= で sid を受け取る）
//   const stop = unsaid.watchMessages(sid, (msgs) => render(msgs));   // 表示はすべてここから
//   await unsaid.say(sid, "patient", "I've had a headache for a while.");
//
// npm の firebase を使う（Vite / React など）場合は、下の import の URL を "firebase/app" などに替えるだけ。
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, signInAnonymously } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import {
  getFirestore, collection, doc, addDoc, updateDoc, query, orderBy, onSnapshot, serverTimestamp, arrayUnion,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { transcribe as postWav } from "./stt.js";
import { firebaseConfig, API_URL } from "./firebase-config.js";

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

const msgRef = (sid, mid) => doc(db, "sessions", sid, "messages", mid);

// 患者の端末: session を作る。patientLang は en / zh-CN / vi。作った後は変えられない（ルールで members 以外は更新不可）
export async function createSession(patientLang) {
  const user = await login();
  const ref = await addDoc(collection(db, "sessions"), {
    patientLang, doctorLang: "ja", members: [user.uid], createdAt: serverTimestamp(),
  });
  return ref.id;
}

// 医師の端末: session に参加する。メンバーになる前は session を読めないので、読まずに自分を足す
export async function joinSession(sid) {
  const user = await login();
  await updateDoc(doc(db, "sessions", sid), { members: arrayUnion(user.uid) });
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
    body: JSON.stringify({ sessionId: sid, messageId: mid }),
  });
  if (res.ok || res.status === 409) return; // 409 は二重押し。無視してよい
  const body = await res.json().catch(() => ({}));
  throw new ApiError(res.status, body.detail ?? res.statusText);
}

// 発言する（from は "patient" / "doctor"）。吹き出しは addDoc の時点で watchMessages に出る。
// await の後に画面を変えないこと（確認質問は onSnapshot の方が早く届く）
export async function say(sid, from, text) {
  const ref = await addDoc(collection(db, "sessions", sid, "messages"), {
    from, original: text, status: "analyzing", createdAt: serverTimestamp(),
  });
  await callApi(from === "patient" ? "/analyze" : "/translate", sid, ref.id);
  return ref.id;
}

// 患者: 確認質問に答える。type は "option"（text = 選んだ選択肢）/ "free_text"（text = 入力）/ "unsure"（text = ""）
export async function answer(sid, mid, type, text = "") {
  await updateDoc(msgRef(sid, mid), { "clarification.answer": { type, text } });
  await callApi("/translate", sid, mid);
}

// 患者: 逆翻訳を見て「送信」（status confirmed のときだけ）
export const send = (sid, mid) => updateDoc(msgRef(sid, mid), { status: "sent", sentAt: serverTimestamp() });

// 医師: 患者の発言を表示したら「受け取った」（status sent のときだけ）
export const receive = (sid, mid) => updateDoc(msgRef(sid, mid), { status: "received", receivedAt: serverTimestamp() });

// 「もう一度」: status が error の発言を止まった所から呼び直す（m は watchMessages で受け取った1件）
export function retry(sid, m) {
  const path = m.from === "patient" && !m.clarification?.answer ? "/analyze" : "/translate";
  return callApi(path, sid, m.id);
}

// 音声 → 文字（Firestore には書かない）。返った text を入力欄に入れ、本人が確認してから say() する
export async function transcribe(sid, speaker, wav) {
  const token = await (await login()).getIdToken();
  return postWav(API_URL, token, sid, speaker, wav);
}
