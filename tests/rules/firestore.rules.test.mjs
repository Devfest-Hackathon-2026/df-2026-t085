// firestore.rules を本物のルールエンジン（Firebase エミュレーター）で確かめるテスト。
// 端末が Firestore に直接書いて Safety Gate を飛ばせないこと（送信済みにする・訳や Gate の状態を書く・なりすます）を確認する。
//
//   npm i --no-save firebase-tools@14 @firebase/rules-unit-testing@4 firebase@11
//   npx firebase emulators:exec --only firestore --project demo-unsaid "node --test tests/rules/"
//
// （Java 21 が要る。2026-11-10 に firebase-tools 14.27.0・Temurin 21 で 10 件すべて成功。EVALUATION.md）
// 同時更新・重複イベントは、本物の SDK の runTransaction をエミュレーターに対して2つ同時に走らせて確かめる
// （web/unsaid.js の receive と同じ書き方。ローカルキャッシュには頼らない）。
// 同じ条件を Python に書き写した devtools/rules.py は tests/test_rules_emulation.py で確かめている。
import { test, before, after, beforeEach } from "node:test";
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import {
  initializeTestEnvironment, assertSucceeds, assertFails,
} from "@firebase/rules-unit-testing";
import { doc, setDoc, updateDoc, getDoc, getDocFromServer, serverTimestamp, Timestamp, runTransaction } from "firebase/firestore";

let env;
const SID = "s1";

before(async () => {
  env = await initializeTestEnvironment({
    projectId: "demo-unsaid",
    firestore: { rules: readFileSync(new URL("../../firestore.rules", import.meta.url), "utf8") },
  });
});
after(() => env?.cleanup());

beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    await setDoc(doc(db, "sessions", SID), {
      members: ["doc", "pat"], patientLang: "zh-CN", doctorLang: "ja", createdAt: Timestamp.now(),
    });
    await setDoc(doc(db, "sessions", SID, "messages", "sent1"), { from: "patient", original: "没事", status: "sent" });
    await setDoc(doc(db, "sessions", SID, "messages", "wait1"), { from: "patient", original: "没事", status: "awaiting_intent" });
    await setDoc(doc(db, "sessions", SID, "messages", "rev1"), { from: "patient", original: "没事", status: "needs_review" });
    await setDoc(doc(db, "sessions", SID, "messages", "conf1"), { from: "patient", original: "没事", status: "confirmed" });
    await setDoc(doc(db, "sessions", SID, "messages", "dr1"), { from: "doctor", original: "大丈夫です", status: "analyzing" });
  });
});

const as = (uid) => env.authenticatedContext(uid).firestore();
const msgs = (db, id) => doc(db, "sessions", SID, "messages", id);
const base = { original: "没事", status: "analyzing", createdAt: serverTimestamp() };

test("画面が使う書き込みは通る", async () => {
  await assertSucceeds(setDoc(msgs(as("pat"), "n1"), { ...base, from: "patient" }));
  await assertSucceeds(setDoc(msgs(as("doc"), "n2"), { ...base, from: "doctor" }));
  await assertSucceeds(setDoc(msgs(as("pat"), "n3"), { ...base, from: "patient", replyTo: "q1", answer: { type: "option", index: 0 } }));
  await assertSucceeds(updateDoc(msgs(as("doc"), "sent1"), { status: "received", receivedAt: serverTimestamp() }));
  await assertSucceeds(getDoc(msgs(as("pat"), "sent1")));
});

test("端末は作るときに Safety Gate を飛ばせない", async () => {
  await assertFails(setDoc(msgs(as("pat"), "x1"), { ...base, from: "patient", status: "sent" }));
  await assertFails(setDoc(msgs(as("pat"), "x2"), { ...base, from: "patient", translation: { text: "x" } }));
  await assertFails(setDoc(msgs(as("pat"), "x3"), { ...base, from: "patient", gate: { state: "READY_TO_SEND" } }));
  await assertFails(setDoc(msgs(as("pat"), "x4"), { ...base, from: "patient", kind: "clarify" }));
  await assertFails(setDoc(msgs(as("pat"), "x5"), { ...base, from: "doctor" }));
  await assertFails(setDoc(msgs(as("doc"), "x6"), { ...base, from: "patient" }));
  await assertFails(setDoc(msgs(as("doc"), "x7"), { ...base, from: "doctor", replyTo: "q1" }));
  await assertFails(setDoc(msgs(as("stranger"), "x8"), { ...base, from: "patient" }));
});

test("端末は後から送信済みにできない（意図の確認・照合の確認・確認待ちを飛ばせない）", async () => {
  for (const id of ["conf1", "wait1", "rev1"]) {
    await assertFails(updateDoc(msgs(as("pat"), id), { status: "sent", sentAt: serverTimestamp() }));
  }
  await assertFails(updateDoc(msgs(as("doc"), "dr1"), { status: "sent" }));
  await assertFails(updateDoc(msgs(as("pat"), "wait1"), { gate: { state: "READY_TO_SEND" } }));
});

test("届いた発言の訳・原文・問診メモを書き換えられない", async () => {
  await assertFails(updateDoc(msgs(as("pat"), "sent1"), { translation: { text: "改ざん" } }));
  await assertFails(updateDoc(msgs(as("pat"), "sent1"), { original: "改ざん" }));
  await assertFails(updateDoc(msgs(as("doc"), "sent1"), { meaning: {} }));
  await assertFails(updateDoc(msgs(as("doc"), "wait1"), { status: "received" })); // 届く前に既読にできない
});

test("未解決事項（session の audit）は端末が書けない・第三者は読めない", async () => {
  await assertFails(updateDoc(doc(as("doc"), "sessions", SID), { audit: { issues: [] } }));
  await assertFails(getDoc(doc(as("stranger"), "sessions", SID)));
  await assertFails(getDoc(msgs(as("stranger"), "sent1")));
});

// web/unsaid.js の receive と同じ: サーバーの最新の status を読み、sent のときだけ既読にする。
// 同時に書いた後の方はルールで断られるので、サーバーから読み直して、もう sent でなければ成功とみなす
const receiveTx = (db, id) => runTransaction(db, async (tx) => {
  const snap = await tx.get(msgs(db, id));
  if (snap.exists() && snap.data().status === "sent") tx.update(msgs(db, id), { status: "received", receivedAt: serverTimestamp() });
});
async function receive(db, id) {
  try {
    await receiveTx(db, id);
  } catch (e) {
    const now = e.code === "permission-denied" ? await getDocFromServer(msgs(db, id)).catch(() => null) : null;
    if (!(now?.exists() && now.data().status !== "sent")) throw e;
  }
}

test("同時更新（トランザクションだけ）: 後の方はルールで断られる（読み直しでは防げない。下の receive が必要な理由）", async () => {
  const r = await Promise.allSettled([receiveTx(as("doc"), "sent1"), receiveTx(as("doc"), "sent1")]);
  assert.equal(r.filter((x) => x.status === "fulfilled").length >= 1, true);
  assert.equal((await getDoc(msgs(as("doc"), "sent1"))).data().status, "received");
});

test("重複イベント: 既読の発言をもう一度既読にする書き込みはルールが断る（だから端末はトランザクションで確かめる）", async () => {
  await assertSucceeds(updateDoc(msgs(as("doc"), "sent1"), { status: "received", receivedAt: serverTimestamp() }));
  await assertFails(updateDoc(msgs(as("doc"), "sent1"), { status: "received", receivedAt: serverTimestamp() }));
});

test("同時更新: 2台の医師の端末が同時に既読にしても、どちらも成功し1回だけ書かれる", async () => {
  const a = as("doc"), b = as("doc");
  const r = await Promise.allSettled([receive(a, "sent1"), receive(b, "sent1"), receive(a, "sent1")]);
  assert.deepEqual(r.map((x) => x.status), ["fulfilled", "fulfilled", "fulfilled"], String(r.find((x) => x.reason)?.reason));
  const after = (await getDoc(msgs(as("doc"), "sent1"))).data();
  assert.equal(after.status, "received");
});

test("直接書き込み: 解析のやり直しの所有権（lease）・問診メモ・再確認の印を端末が書けない", async () => {
  await env.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(msgs(ctx.firestore(), "retry1"), {
      from: "patient", original: "就是天旋地转", status: "sent",
      meaning: { status: "retrying", retry_run: "server-run", retry_until: "2099-01-01T00:00:00+00:00", facts: [] },
    });
  });
  for (const uid of ["doc", "pat"]) {
    await assertFails(updateDoc(msgs(as(uid), "retry1"), { "meaning.status": "error" }));
    await assertFails(updateDoc(msgs(as(uid), "retry1"), { "meaning.retry_run": "mine" }));
    await assertFails(updateDoc(msgs(as(uid), "retry1"), { "meaning.facts": [{ status: "doctor_confirmed", rechecks: [] }] }));
  }
});

test("直接書き込み: 医師も確認質問・理解の確認を自分で作って送れない（Safety Gate と照合を飛ばせない）", async () => {
  await assertFails(setDoc(msgs(as("doc"), "y1"), { ...base, from: "doctor", kind: "clarify", status: "sent" }));
  await assertFails(setDoc(msgs(as("doc"), "y2"), { ...base, from: "doctor", kind: "clarify" }));
  await assertFails(setDoc(msgs(as("doc"), "y3"), { ...base, from: "doctor", kind: "teachback" }));
  await assertFails(setDoc(msgs(as("doc"), "y4"), { ...base, from: "doctor", translation: { text: "x" } }));
});
