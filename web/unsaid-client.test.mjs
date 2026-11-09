// web/unsaid.js の、端末から Firestore に直接書く部分のテスト（Firebase は devtools/fake-firebase の偽物。ネットに出ない）。
// ローカルキャッシュの反映（自分の書き込みがすぐ次のスナップショットに出る）を前提にしないことを確かめる:
// 偽物にはキャッシュが無く、同じ発言が何度 sent のまま届いても、2台の端末から同時に呼んでも、安全に1回だけ書く。
// 偽物のトランザクションは Firestore エミュレーターで確かめた順（ルール → 読んだ後の変更でやり直し）で判定する。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { resolve } from "../devtools/fake-firebase/hooks.mjs";

registerHooks({ resolve });
globalThis.location ??= { hostname: "localhost" };
globalThis.addEventListener ??= () => {}; // unsaid.js の online / offline
const S = (globalThis.__fakeFs ??= { docs: new Map(), writes: [], denied: [] });
const device1 = await import("./unsaid.js?device=1");
const device2 = await import("./unsaid.js?device=2"); // 医師がもう1台（タブ）で開いている

const M = "sessions/s1/messages/m1";
beforeEach(() => {
  S.docs.clear(); S.writes.length = 0; S.denied.length = 0;
  S.docs.set("sessions/s1", { members: ["doc"], patientLang: null, doctorLang: "ja" });
  S.docs.set(M, { from: "patient", status: "sent", original: "头晕" });
  globalThis.__fakeUid = "doc";
});

test("同じ発言が sent のまま何度届いても、受け取りは1回だけ書き、エラーにならない", async () => {
  const ids = ["m1", "m1", "m1"];
  const r = await Promise.allSettled(ids.map((id) => device1.receive("s1", id))); // スナップショットごとに呼ぶ画面と同じ
  assert.deepEqual(r.map((x) => x.status), ["fulfilled", "fulfilled", "fulfilled"]);
  assert.equal(S.writes.length, 1);
  assert.equal(S.denied.length, 0);
  assert.equal(S.docs.get(M).status, "received");
});

test("2台の端末が同時に受け取っても、1回だけ書き、どちらもエラーにならない", async () => {
  S.docs.set("sessions/s1/messages/m2", { from: "patient", status: "sent", original: "x" });
  const r = await Promise.allSettled([device1.receive("s1", "m2"), device2.receive("s1", "m2")]);
  assert.deepEqual(r.map((x) => x.status), ["fulfilled", "fulfilled"], String(r.find((x) => x.reason)?.reason));
  assert.equal(S.writes.length, 1);
  // 後の方はルールで断られうる（本物のエミュレーターと同じ順の判定）。断られても、サーバーから読み直して成功にする
  assert.ok(S.denied.length <= 1);
});

test("送信済みでない発言（取り消し・既読）は書かない", async () => {
  S.docs.set("sessions/s1/messages/m3", { from: "patient", status: "cancelled", original: "x" });
  await device1.receive("s1", "m3");
  assert.equal(S.writes.length, 0);
  assert.equal(S.denied.length, 0);
});

test("患者が参加ボタンを2回押しても（または2つのタブで）、参加は成功する", async () => {
  globalThis.__fakeUid = "pat";
  const r = await Promise.allSettled([device1.joinSession("s1", "zh-CN"), device2.joinSession("s1", "zh-CN")]);
  assert.deepEqual(r.map((x) => x.status), ["fulfilled", "fulfilled"], String(r.find((x) => x.reason)?.reason));
  assert.deepEqual(S.docs.get("sessions/s1").members, ["doc", "pat"]);
  assert.equal(S.docs.get("sessions/s1").patientLang, "zh-CN");
});

test("ほかの患者が先に参加していたら、参加できない（成功にしない）", async () => {
  S.docs.set("sessions/s1", { members: ["doc", "other"], patientLang: "en", doctorLang: "ja" });
  globalThis.__fakeUid = "pat";
  await assert.rejects(device1.joinSession("s1", "zh-CN"), { code: "permission-denied" });
});
