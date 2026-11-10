// 接続先はホスト名だけで決まり、プロジェクトをまたいで混ざらない。
import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.location = { hostname: "unsaid-devfest.web.app" };
const { pick, TARGET, API_URL } = await import("./firebase-config.js");

test("チームの本番の URL では、これまでどおりチームの本番の API・Firestore", () => {
  assert.equal(TARGET, "team");
  assert.equal(API_URL, "https://unsaid-api-799163399675.asia-northeast1.run.app");
  const t = pick("unsaid-devfest.firebaseapp.com");
  assert.equal(t.firebaseConfig.projectId, "unsaid-devfest");
  assert.equal(t.db, "(default)");
});

test("チームのプレビューチャンネルは、チームのステージング", () => {
  const t = pick("unsaid-devfest--clinical-meaning-abc123.web.app");
  assert.equal(t.name, "team-staging");
  assert.equal(t.firebaseConfig.projectId, "unsaid-devfest");
  assert.equal(t.db, "staging");
  assert.match(t.api, /unsaid-api-staging/);
});

test("Kumu の独立ステージングは、画面・API・Firestore がすべて kumu-devfest-2026", () => {
  for (const host of ["kumu-devfest-2026.web.app", "kumu-devfest-2026.firebaseapp.com", "kumu-devfest-2026--e2e-x1.web.app"]) {
    const t = pick(host);
    assert.equal(t.name, "kumu-staging", host);
    assert.equal(t.firebaseConfig.projectId, "kumu-devfest-2026");
    assert.equal(t.firebaseConfig.authDomain, "kumu-devfest-2026.firebaseapp.com");
    assert.ok(!JSON.stringify(t).includes("unsaid-devfest"), "チームのプロジェクトの値が混ざっていない");
    assert.ok(!JSON.stringify(t).includes("799163399675"), "チームのプロジェクト番号が混ざっていない");
    assert.equal(t.gate, true, "参加コードの解錠が要る");
  }
  assert.notEqual(pick("unsaid-devfest.web.app").gate, true, "チームの本番は参加コードを聞かない");
});

test("設定が空の接続先のホストで開くと、黙ってチームの本番につながらずに止まる", async () => {
  globalThis.location = { hostname: "kumu-devfest-2026.web.app" };
  const kumu = pick("kumu-devfest-2026.web.app");
  if (kumu.firebaseConfig.apiKey && kumu.api) return;  // 値を入れた後はこの確認は要らない
  await assert.rejects(import("./firebase-config.js?empty"), /kumu-staging/);
});
