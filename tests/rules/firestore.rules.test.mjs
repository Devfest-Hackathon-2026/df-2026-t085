// firestore.rules を本物のルールエンジン（Firebase エミュレーター）で確かめるテスト。
// 端末が Firestore に直接書いて Safety Gate を飛ばせないこと（送信済みにする・訳や Gate の状態を書く・なりすます）を確認する。
//
//   npm i --no-save firebase-tools@14 @firebase/rules-unit-testing@4 firebase@10
//   npx firebase emulators:exec --only firestore --project demo-unsaid "node --test tests/rules/"
//
// （Java 11 以上が要る。この作業環境では Java を入れられず、まだ実行していない。SECURITY.md・EVALUATION.md）
// 同じ条件を Python に書き写した devtools/rules.py は tests/test_rules_emulation.py で確かめている。
import { test, before, after, beforeEach } from "node:test";
import { readFileSync } from "node:fs";
import {
  initializeTestEnvironment, assertSucceeds, assertFails,
} from "@firebase/rules-unit-testing";
import { doc, setDoc, updateDoc, getDoc, serverTimestamp, Timestamp } from "firebase/firestore";

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
