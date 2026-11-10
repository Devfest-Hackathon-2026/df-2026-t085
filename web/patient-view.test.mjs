// web/patient-view.js のテスト（患者の「話す」画面の状態の計算。patient.html の view() と同じ関数）
import { test } from "node:test";
import assert from "node:assert/strict";
import { speakState } from "./patient-view.js";

const q = (id, kind = "clarify", status = "sent") => ({ id, from: "doctor", kind, status, original: id });
const say = (id, status = "sent") => ({ id, from: "doctor", status, original: id });
const ans = (id, replyTo, status = "sent") => ({ id, from: "patient", status, original: id, replyTo });
const pt = (id, status = "sent") => ({ id, from: "patient", status, original: id });

test("続けて2つ質問され、2つ目に答えても、1つ目は出たまま答えられる（レビュー指摘2）", () => {
  const msgs = [pt("p1"), q("q1"), q("q2"), ans("a2", "q2")];
  const v = speakState(msgs);
  assert.deepEqual(v.doctors.map((m) => m.id), ["q1"]);
  assert.ok(v.openIds.has("q1") && !v.openIds.has("q2"));
  assert.equal(v.openQ.id, "q1"); // 文字で答えると q1 への答えになる
});

test("1つ目に答えると2つ目が残る・両方答えると消える", () => {
  assert.equal(speakState([q("q1"), q("q2"), ans("a1", "q1")]).openQ.id, "q2");
  const v = speakState([q("q1"), q("q2"), ans("a1", "q1"), ans("a2", "q2")]);
  assert.equal(v.openQ, null);
  assert.deepEqual(v.doctors, []);
});

test("解析中・確認待ち・取り消し・失敗した答えは、届いた答えとして数えない", () => {
  for (const st of ["analyzing", "awaiting_intent", "needs_review", "cancelled", "error"]) {
    const v = speakState([q("q1"), ans("a1", "q1", st)]);
    assert.ok(v.openIds.has("q1"), st);
  }
});

test("まだ届いていない質問（照合中・確認待ち）は出さない", () => {
  for (const st of ["analyzing", "awaiting_intent", "needs_review", "cancelled"]) {
    const v = speakState([q("q1", "clarify", st)]);
    assert.equal(v.openQ, null, st);
    assert.deepEqual(v.doctors, []);
  }
});

test("通常の医師の発言は、最後に送った後のものだけ（前のものは答えても残らない）", () => {
  const v = speakState([say("d1"), pt("p1"), say("d2")]);
  assert.deepEqual(v.doctors.map((m) => m.id), ["d2"]);
  assert.equal(v.openQ, null);
});

test("理解の確認も質問として扱う。答えると閉じる", () => {
  assert.equal(speakState([say("exp"), q("t1", "teachback")]).openQ.id, "t1");
  assert.equal(speakState([say("exp"), q("t1", "teachback"), ans("a", "t1")]).openQ, null);
});

test("回答を直す: 答えた質問を開き直せる。ほかに開いた質問があれば出さない", () => {
  const msgs = [q("q1"), ans("a1", "q1")];
  assert.equal(speakState(msgs).answeredQ.id, "q1");
  const v = speakState(msgs, "q1");
  assert.equal(v.openQ.id, "q1");
  assert.deepEqual(v.doctors.map((m) => m.id), ["q1"]);
  assert.equal(speakState([q("q1"), q("q2"), ans("a1", "q1")]).answeredQ, null);
});

test("再読み込み（同じ一覧から計算し直す）で同じ結果になる", () => {
  const msgs = [pt("p1"), q("q1"), q("q2"), ans("a2", "q2"), say("d3")];
  const a = speakState(msgs);
  const b = speakState(structuredClone(msgs));
  assert.deepEqual(a.doctors.map((m) => m.id), b.doctors.map((m) => m.id));
  assert.deepEqual(a.doctors.map((m) => m.id), ["q1", "d3"]);
  assert.equal(a.openQ.id, "q1");
});
