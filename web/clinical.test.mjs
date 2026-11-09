// web/clinical.js のテスト（node --test web/）。サーバーが書く形の発言を手で作って確かめる。
import { test } from "node:test";
import assert from "node:assert/strict";
import { clinicalMemo, candidates, karteText, factText, factsOf, teachbacks, activeIssues, verificationOf } from "./clinical.js";

const fact = (id, category, meaning, quote, extra = {}) => ({
  fact_id: id, category, normalized_meaning: meaning, source_quote: quote, polarity: "present",
  status: "ai_extracted", clarification_status: "none", resolved_meaning: "", history: [], ...extra,
});
const A = {
  id: "m1", from: "patient", status: "received", original: "今天在车站突然觉得头晕，我有点害怕。",
  translation: { text: "今日駅で突然めまいがしました。少し怖いです。" },
  meaning: {
    status: "ready",
    facts: [
      fact("f1", "symptom", "めまい", "头晕", { status: "unconfirmed", clarification_status: "open" }),
      fact("f2", "onset", "今日、駅で突然", "今天在车站突然", { about_fact_id: "f1" }),
      fact("f3", "psychological", "不安の訴え（本人の言葉）", "我有点害怕"),
      fact("f4", "background", "旅行で来日", "旅游"),
    ],
    candidates: [{ candidate_id: "c1", source_quote: "头晕", meaning_doctor: "めまい", unresolved: "回転性か浮動性か",
      priority: "high", status: "proposed", answers: [], fact_ids: ["f1"] }],
  },
};

test("問診メモ: 症状に属性がつき、言及なしを出す", () => {
  const memo = clinicalMemo([A]);
  assert.equal(memo.symptoms.length, 1);
  assert.equal(memo.symptoms[0].text, "めまい");
  assert.equal(memo.symptoms[0].attrs.get("onset")[0].text, "今日、駅で突然");
  assert.deepEqual(memo.otherOrder, ["background", "psychological"]);
  assert.deepEqual(memo.notMentioned, ["history", "medication", "allergy"]);
});

test("送信前の発言・医師の発言は問診メモに入れない", () => {
  const draft = { ...A, id: "m0", status: "confirmed" };
  assert.equal(clinicalMemo([draft]).count, 0);
  assert.equal(candidates([draft]).open.length, 0);
});

test("患者の回答で置き換わった意味を、元の言い方と一緒に出す", () => {
  assert.equal(factText(fact("f", "symptom", "めまい", "头晕", { resolved_meaning: "周囲が回る（回転性）" })), "めまい：周囲が回る（回転性）");
  assert.equal(factText(fact("f", "symptom", "めまい", "头晕", { resolved_meaning: "回転性のめまい" })), "回転性のめまい");
});

test("同じ症状を言い直したら最新を出し、前の値を残す", () => {
  const B = { ...A, id: "m2", meaning: { status: "ready", candidates: [], facts: [
    fact("f1", "symptom", "めまい", "头晕"), fact("f2", "onset", "昨日から", "昨天", { about_fact_id: "f1" })] } };
  const memo = clinicalMemo([A, B]);
  assert.equal(memo.symptoms.length, 1);
  const onset = memo.symptoms[0].attrs.get("onset")[0];
  assert.equal(onset.text, "昨日から");
  assert.deepEqual(onset.prev, ["今日、駅で突然"]);
});

test("旧い形の facts も読める", () => {
  const legacy = { id: "x", from: "patient", status: "sent", facts: [{ text: "fever", doctor_gloss: "発熱", kind: "symptom", negated: true, uncertain: false }] };
  const f = factsOf(legacy)[0];
  assert.equal(f.polarity, "denied");
  assert.equal(clinicalMemo([legacy]).symptoms[0].text, "発熱");
});

test("確認候補は状態ごとに分かれる", () => {
  const c = candidates([A]);
  assert.equal(c.open.length, 1);
  assert.equal(c.open[0].mid, "m1");
});

test("カルテ: 未確認・言及なし・本人の気持ちを含み、印を付ける", () => {
  const text = karteText([A], { patientLang: "zh-CN" }, new Date("2026-11-10T10:00:00+09:00"));
  assert.match(text, /中国語話者/);
  assert.match(text, /■主訴\nめまい［未］（主な訴え）/);
  assert.match(text, /いつから：今日、駅で突然/);
  assert.match(text, /「头晕」：回転性か浮動性か（未確認）/);
  assert.match(text, /本人の気持ち：不安の訴え（本人の言葉）/);
  assert.match(text, /言及なし：既往歴・服薬・アレルギー/);
  assert.doesNotMatch(text, /診断名|疑い/);
});

test("カルテ: 確認した内容と理解の確認", () => {
  const answered = structuredClone(A);
  answered.meaning.facts[0] = { ...answered.meaning.facts[0], status: "doctor_confirmed", resolved_meaning: "回転性" };
  answered.meaning.candidates[0] = { ...answered.meaning.candidates[0], status: "answered",
    answers: [{ message_id: "a1", meaning_doctor: "回転性" }] };
  const exp = { id: "d1", from: "doctor", status: "sent", original: "今日は帰らずに待ってください。" };
  const tb = { id: "t1", from: "doctor", kind: "teachback", status: "sent", teachback: { explanation_id: "d1",
    status: "assessed", result: { overall: "inconsistent", points: [{ verdict: "mismatch", explanation: "今日と明日が違う" }] } } };
  const text = karteText([answered, exp, tb], { patientLang: "zh-CN" });
  assert.match(text, /めまい：回転性［医］/);
  assert.match(text, /「头晕」（めまい）→ 回転性/);
  assert.match(text, /説明「今日は帰らずに待ってください。」→ 認識の相違の可能性（AI判定）／医師未確認/);
  assert.match(text, /今日と明日が違う/);
  assert.equal(teachbacks([exp, tb]).get("d1").id, "t1");
});

test("何も無ければカルテは空", () => {
  assert.equal(karteText([], { patientLang: "en" }), "");
});

test("未解決事項: 医師が閉じたものは出さず、保留は分ける", () => {
  const audit = { issues: [
    { issue_id: "u1", type: "instruction_misunderstanding", description: "朝に2錠と理解", status: "OPEN", held: false, severity: "high" },
    { issue_id: "u2", type: "contradiction", description: "アレルギー", status: "ANSWERED", held: true, severity: "medium" },
    { issue_id: "u3", type: "contradiction", description: "解決した事項ZZ", status: "RESOLVED", held: false },
    { issue_id: "u4", type: "unanswered_question", description: "無視した事項YY", status: "DISMISSED", held: false },
  ] };
  const a = activeIssues(audit);
  assert.deepEqual(a.open.map((i) => i.issue_id), ["u1"]);
  assert.deepEqual(a.held.map((i) => i.issue_id), ["u2"]);
  const text = karteText([A], { patientLang: "zh-CN" }, new Date(), audit);
  assert.match(text, /指示の理解の相違：朝に2錠と理解/);
  assert.match(text, /前の発言との矛盾：アレルギー（保留）（回答あり）/);
  assert.doesNotMatch(text, /解決した事項ZZ|無視した事項YY/);
});

test("訳の検証の表示: 未検証で送った訳ははっきり分ける", () => {
  assert.equal(verificationOf({ gate: { verification: { status: "VERIFIED" } } }).level, "ok");
  assert.equal(verificationOf({ gate: { verification: { status: "MISMATCH" }, override: "speaker_ack" } }).label, "訳は未検証");
  assert.equal(verificationOf({ gate: { verification: { status: "PRETRANSLATED" } } }), null);
  const B = structuredClone(A);
  B.gate = { override: "speaker_ack", verification: { status: "MISMATCH" } };
  assert.match(karteText([B], { patientLang: "zh-CN" }), /めまい［未］［訳未検証］/);
});
