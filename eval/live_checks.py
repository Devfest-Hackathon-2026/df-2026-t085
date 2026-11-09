"""本物の Gemini で、USSA の各コンポーネントがサーバーの流れの中で動くかを確かめる（基本動作の検証）。

サーバー（main.py）の API を、Firestore だけ偽物（devtools/fakestore.py）にして呼ぶ。Gemini は本物（偽の AI は入れない）。
発言はすべて架空。結果（原文・訳・状態）を eval/live_checks_results.md / .json に書く。認証情報は書かない。

  UNSAID_VERTEX_PROJECT=<プロジェクト ID> UNSAID_VERTEX_LOCATION=global \\
  GOOGLE_APPLICATION_CREDENTIALS=<ADC のファイル> python eval/live_checks.py

判定（expect）は「この動きをするはず」という開発チームの期待。LLM の出力は回ごとに変わりうるので、1 回の結果は目安。
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import unsaid_ai  # noqa: E402
import ussa  # noqa: E402
from devtools import fakestore  # noqa: E402

OUT = Path(__file__).parent
assert unsaid_ai.translate.__module__ == "unsaid_ai", "偽の AI が入っている"


class Session:
    """1つの問診（医師 doc・患者 pat、中国語）。"""

    def __init__(self, db, client, sid, lang="zh-CN"):
        self.db, self.client, self.sid, self.lang = db, client, sid, lang
        db.collection("sessions").document(sid).set({
            "patientLang": lang, "doctorLang": "ja", "members": ["doc", "pat"], "createdAt": datetime.now(timezone.utc)})
        self.n = 0

    def post(self, uid, path, **body):
        main.app.dependency_overrides[main.current_uid] = lambda: uid
        r = self.client.post(path, json={"sessionId": self.sid, **body})
        return r

    def get(self, mid):
        return self.db.collection("sessions").document(self.sid).collection("messages").document(mid).get().to_dict()

    def say(self, frm, text, **extra):
        self.n += 1  # 同じ文の二重送信の取り消し（10 秒）に当たらないように、時刻をずらす
        ref = self.db.collection("sessions").document(self.sid).collection("messages").document()
        ref.set({"from": frm, "original": text, "status": "analyzing",
                 "createdAt": datetime.now(timezone.utc) + timedelta(seconds=20 * self.n), **extra})
        uid = "doc" if frm == "doctor" else "pat"
        t = time.time()
        r = self.post(uid, "/translate" if frm == "doctor" else "/analyze", messageId=ref.id)
        ms = round((time.time() - t) * 1000)
        m = self.get(ref.id)
        if frm == "patient" and m["status"] == "confirmed":
            self.post(uid, "/send", messageId=ref.id)
            m = self.get(ref.id)
        return ref.id, m, r.status_code, ms

    def audit(self):
        return (self.db.collection("sessions").document(self.sid).get().to_dict().get("audit") or {}).get("issues", [])


def brief(m):
    g = m.get("gate") or {}
    it = g.get("intent") or {}
    return {"status": m.get("status"), "translation": (m.get("translation") or {}).get("text", ""),
            "intent": it.get("state"), "decided_by": it.get("decided_by"),
            "interpretations": [i.get("meaning_speaker") for i in it.get("interpretations", [])],
            "verification": (g.get("verification") or {}).get("status"),
            "issues": [(i.get("type"), i.get("strength"), i.get("detail")) for i in (g.get("verification") or {}).get("issues", [])][:6],
            "frame": (it.get("frame") or {})}


def run():
    db = fakestore.FakeDB()
    fakestore.install(main, db)
    main.CLARIFY_MODE = "doctor"
    client = TestClient(main.app)
    R = []

    def rec(name, expect, ok, **detail):
        R.append({"check": name, "expect": expect, "ok": bool(ok), **detail})
        print(f"{'OK ' if ok else 'NG '} {name}: {detail.get('summary', '')}")

    # 1. 医師 → 患者の翻訳
    s = Session(db, client, "live-1")
    mid, m, code, ms = s.say("doctor", "この薬は1日2回、朝と夜に1錠ずつ飲んでください。")
    b = brief(m)
    rec("医師→患者の翻訳", "検証済みで届く・数（2回・1錠）と時間帯（朝・夜）が残る",
        b["status"] == "sent" and any(w in b["translation"] for w in ("两次", "2次", "二次")) and "早" in b["translation"]
        and "晚" in b["translation"] and any(w in b["translation"] for w in ("一片", "1片")),
        summary=f"{b['status']} {b['verification']} 「{b['translation']}」", ms=ms, **b)

    # 2. 患者 → 医師の翻訳
    s = Session(db, client, "live-2")
    mid, m, code, ms = s.say("patient", "我从昨天晚上开始发烧，三十八度五，没有咳嗽。")
    b = brief(m)
    rec("患者→医師の翻訳", "検証済みで届く・38.5度・咳なし（否定）・昨夜から",
        b["status"] == "sent" and any(w in b["translation"] for w in ("38.5", "38度5分", "三十八度五分"))
        and "咳" in b["translation"] and "昨" in b["translation"],
        summary=f"{b['status']} {b['verification']} 「{b['translation']}」", ms=ms, **b)

    # 3. 「大丈夫です」（医師）の意図解析: 文脈で決まる / 決まらない
    s = Session(db, client, "live-3")
    s.say("patient", "这个药可以和我平时吃的降压药一起吃吗？")
    mid, m, code, ms = s.say("doctor", "大丈夫です。")
    b = brief(m)
    rec("「大丈夫です」: 文脈で決まる（併用してよいか → 大丈夫）", "聞かずに「一緒に飲んでよい」の意味で訳す（RESOLVED）",
        b["intent"] == ussa.RESOLVED and b["status"] == "sent",
        summary=f"{b['intent']} {b['status']} 「{b['translation']}」", ms=ms, **b)
    s = Session(db, client, "live-4")
    mid, m, code, ms = s.say("doctor", "大丈夫です。")
    b = brief(m)
    rec("「大丈夫です」: 文脈なし（意味が決まらない）", "訳を送らず、医師本人に意味を確認する（CLARIFICATION_REQUIRED）",
        b["status"] == "awaiting_intent" and not b["translation"],
        summary=f"{b['intent']} {b['status']} 候補 {b['interpretations']}", ms=ms, **b)

    # 4. 意図確認と翻訳への反映（患者の「没事」→ 本人が選ぶ）
    s = Session(db, client, "live-5")
    s.say("doctor", "痛み止めの注射をしましょうか？")
    mid, m, code, ms = s.say("patient", "没事。")
    b = brief(m)
    asked = b["status"] == "awaiting_intent"
    detail = {"before": b}
    if asked:
        it = m["gate"]["intent"]
        # 「要らない（断る）」に当たる解釈を選ぶ。選んだ意味で訳されたかを見る
        pick = next((i for i in it["interpretations"] if any(w in (i.get("meaning_listener") or "") for w in ("不要", "要りません", "結構", "いらない", "必要ありません"))),
                    it["interpretations"][0])
        t = time.time()
        r = s.post("pat", "/intent", messageId=mid, choice=pick["id"])
        after = brief(s.get(mid))
        detail.update(chosen=pick, after=after, ms_after_choice=round((time.time() - t) * 1000))
        ok = r.status_code == 200 and after["status"] == "sent" and after["intent"] == ussa.USER_CONFIRMED
        summary = f"選んだ「{pick.get('meaning_speaker')}」→ 訳「{after['translation']}」{after['verification']}"
    else:
        ok, summary = False, f"聞かなかった（{b['intent']} 「{b['translation']}」）"
    rec("意図確認と翻訳への反映（没事）", "本人に聞き、選んだ意味（断る）で訳して届く（USER_CONFIRMED）", ok, summary=summary,
        ms=ms, **detail)

    # 5. Semantic Bridge（意味の枠）
    s = Session(db, client, "live-6")
    mid, m, code, ms = s.say("doctor", "明日の朝8時までは何も食べないでください。水は飲んでもかまいません。")
    b = brief(m)
    f = b["frame"]
    rec("Semantic Bridge の意味の枠", "禁止（must_not）・時刻 08:00・明日（day+1）を取り出す",
        f.get("modality") in ("must_not", "should_not") and any("08:00" in json.dumps(t) for t in f.get("times", []))
        and any("day+1" in json.dumps(t) for t in f.get("times", [])),
        summary=f"modality={f.get('modality')} polarity={f.get('polarity')} times={f.get('times')}", ms=ms, frame=f,
        translation=b["translation"], verification=b["verification"])

    # 6. Verification Engine（誤りを入れた訳 / 正しい訳）
    src = "この薬は1日2回、朝と夜に1錠ずつ飲んでください。"
    raw, _ = unsaid_ai.source_agent(src, "doctor", "ja", "zh-CN", [])  # この原文の意図と意味の枠（本物の Source Agent）
    intent = ussa.build_intent(raw, source_text=src, speaker_lang="ja", listener_lang="zh-CN")
    cases = [("1日3回に変えた訳", "这个药一天吃三次，早上和晚上各吃一片。", False),
             ("否定を足した訳", "这个药不要一天吃两次，早上和晚上各吃一片。", False),
             ("夜を落とした訳", "这个药一天吃两次，早上吃一片。", False),
             ("正しい訳", "这个药一天吃两次，早上和晚上各吃一片。", True)]
    for name, tr, good in cases:
        t0 = time.time()
        v = main._verify(src, "ja", tr, "zh-CN", "doctor", intent, None, {}, "x")
        ok = (v["status"] in (ussa.VERIFIED, ussa.AMBIGUOUS)) if good else (v["status"] == ussa.MISMATCH)
        rec(f"Verification Engine: {name}", "正しい訳は通す" if good else "MISMATCH にする", ok,
            summary=f"{v['status']} {[(i['type'], i['strength']) for i in v['issues']][:4]}",
            ms=round((time.time() - t0) * 1000), verification=v)

    # 7. Safety Gate の送信判定（訳が数を落としたまま直らないとき送らない）
    s = Session(db, client, "live-7")
    real = unsaid_ai.translate

    def lossy(text, *a, **k):  # 「8時間」を必ず落とす翻訳（訳し直しでも直らない）
        out, sec = real(text, *a, **k)
        out["translation"] = "检查前请不要吃东西。"
        return out, sec
    unsaid_ai.translate = lossy
    try:
        mid, m, code, ms = s.say("doctor", "検査の前は8時間何も食べないでください。")
    finally:
        unsaid_ai.translate = real
    b = brief(m)
    rec("Safety Gate: 数が落ちた訳は自動で送らない", "needs_review（発言者の確認）・訳は届かない",
        b["status"] == "needs_review" and not b["translation"],
        summary=f"{b['status']} {b['verification']} {b['issues'][:3]}", ms=ms, **b)

    # 8. Conversation Auditor（服薬の理解の相違）
    s = Session(db, client, "live-8")
    s.say("doctor", "この薬は1日2回、朝と夜に1錠ずつ飲んでください。")
    mid, m, code, ms = s.say("patient", "早上吃两片就可以了，对吗？")
    issues = [i for i in s.audit() if i["status"] in ussa.ACTIVE_ISSUE]
    hit = [i for i in issues if ussa.ISSUE_FAMILY.get(i["type"]) == "understanding"]
    rec("Conversation Auditor: 理解の相違（朝に2錠）", "理解の相違を未解決事項に 1 件出す（根拠の発言つき）",
        len(hit) >= 1, summary=f"{[(i['type'], i['description'][:60]) for i in issues]}", ms=ms,
        issues=issues, patient=brief(m))
    s2 = Session(db, client, "live-9")
    s2.say("doctor", "この薬は1日2回、朝と夜に1錠ずつ飲んでください。")
    mid, m, code, ms = s2.say("patient", "一天两次，早上和晚上各吃一片，对吗？")
    issues = [i for i in s2.audit() if i["status"] in ussa.ACTIVE_ISSUE]
    rec("Conversation Auditor: 正しい理解に警告を出さない", "理解の相違を出さない",
        not any(ussa.ISSUE_FAMILY.get(i["type"]) == "understanding" for i in issues),
        summary=f"{[(i['type'], i['description'][:60]) for i in issues]}", ms=ms, issues=issues)

    # 9. Teach-back
    for name, ans, expect_bad in [("食い違い", "明天再来就可以了，是吗？", True), ("正しい理解", "今天不回家，在医院等检查结果出来。", False)]:
        s = Session(db, client, f"live-tb-{expect_bad}")
        exp, em, _, _ = s.say("doctor", "今日は検査の結果が出るまで、帰らずに待ってください。")
        r0 = s.post("doc", "/teachback/start", messageId=exp)
        if r0.status_code != 200:  # 説明が届かなかった（意図の確認・照合の確認で止まった）
            rec(f"Teach-back: {name}", "説明が届き、理解の確認ができる", False,
                summary=f"説明が届かなかった: {brief(em)['status']} {brief(em)['verification']} {brief(em)['issues'][:3]}",
                explanation=brief(em))
            continue
        qid = r0.json()["messageId"]
        mid, m, code, ms = s.say("patient", ans, replyTo=qid, answer={"type": "free_text"})
        r = m.get("teachback_result") or {}
        ok = (r.get("overall") == "inconsistent") if expect_bad else (r.get("overall") == "consistent")
        rec(f"Teach-back: {name}", "inconsistent（認識の相違）" if expect_bad else "consistent", ok,
            summary=f"{r.get('overall')} {[p.get('patient_understanding', '')[:40] for p in r.get('points', [])][:3]}",
            ms=ms, result=r, translation=brief(m)["translation"])
    return R


def main_():
    started = time.strftime("%Y-%m-%d %H:%M %Z")
    R = run()
    ok = sum(r["ok"] for r in R)
    meta = {"started": started, "model": unsaid_ai.MODEL, "project": unsaid_ai.PROJECT, "location": unsaid_ai.LOCATION,
            "checks": len(R), "ok": ok}
    (OUT / "live_checks_results.json").write_text(json.dumps({"meta": meta, "results": R}, ensure_ascii=False, indent=1,
                                                             default=str), encoding="utf-8")
    L = ["# 本物の Gemini での基本動作の確認", "",
         f"- 実行: {started}、モデル {unsaid_ai.MODEL}、Vertex AI の場所 {unsaid_ai.LOCATION}（評価用のプロジェクト）",
         "- サーバーの API を、Firestore だけ偽物にして通した。発言はすべて架空。1 回の実行（LLM の出力は回ごとに変わりうる）", "",
         "| 確認 | 期待 | 結果 | 実際 | 時間 |", "|---|---|---|---|---|"]
    for r in R:
        L.append(f"| {r['check']} | {r['expect']} | {'OK' if r['ok'] else '**NG**'} | {str(r.get('summary', '')).replace('|', '／')} | "
                 f"{r.get('ms', 0) / 1000:.1f} 秒 |")
    L += ["", f"{ok} / {len(R)} が期待どおり。"]
    (OUT / "live_checks_results.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n{ok} / {len(R)} OK")


if __name__ == "__main__":
    main_()
