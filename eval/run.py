"""「なぜ Gemini か」の評価: 確認質問を出すべき発言で出せるか・出さなくてよい発言で黙れるかを、
Gemini（デプロイ済みの /analyze）と単語リストで比べる。デモの最初の1文の再現性も見る。

使い方（1行で）:
  python eval/run.py --api https://unsaid-api-xxxx.asia-northeast1.run.app --key <firebaseConfig の apiKey>
  python eval/run.py --api ... --key ... --demo 20     # デモの1文を20回
結果は eval/results.json と eval/results.md に書く。本物の Gemini を呼ぶので少しクレジットを使う。
"""
import argparse
import json
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import smoke  # noqa: E402  ログイン・Firestore の読み書きを使い回す
from cases import CASES, DEMO_TEXT, KEYWORDS  # noqa: E402

OUT = Path(__file__).parent


def keyword_hit(text, lang):
    low = text.lower()
    return [k for k in KEYWORDS[lang] if k in low]


def open_session(key, lang):
    dt, duid = smoke.sign_in(key, "医師")
    pt, puid = smoke.sign_in(key, "患者")
    sid = "eval-" + uuid.uuid4().hex[:8]
    smoke.doc_create("sessions", sid, {"patientLang": None, "doctorLang": "ja", "members": [duid]}, dt, f"session {lang}")
    smoke.doc_patch(f"sessions/{sid}", {"members": [duid, puid], "patientLang": lang},
                    ["members", "patientLang"], pt, f"参加 {lang}")
    return sid, pt


def analyze(api, sid, token, text):
    mid = "e-" + uuid.uuid4().hex[:8]
    smoke.doc_create(f"sessions/{sid}/messages", mid, {"from": "patient", "original": text, "status": "analyzing"},
                     token, "発言")
    t = time.time()
    code, body = smoke.http("POST", api.rstrip("/") + "/analyze", {"sessionId": sid, "messageId": mid}, token)
    sec = time.time() - t
    if code != 200:
        return {"error": f"{code} {json.dumps(body, ensure_ascii=False)[:200]}", "sec": sec}
    c = body.get("clarification")
    return {"sec": round(sec, 1), "status": body.get("status"),
            "target": c and c["target"], "question": c and c["question"], "options": c and c["options"],
            "facts": [f.get("doctor_gloss") for f in body.get("facts", [])]}


def wait_shown(api, sid, token, text):
    """患者が実際に待つ時間: 発言を作ってから、status が analyzing でなくなる（確認質問か逆翻訳が画面に出る）まで。
    /analyze の応答は医師用の facts まで待つので、それより短い。"""
    mid = "w-" + uuid.uuid4().hex[:8]
    smoke.doc_create(f"sessions/{sid}/messages", mid, {"from": "patient", "original": text, "status": "analyzing"},
                     token, "発言")
    t = time.time()
    with ThreadPoolExecutor(1) as ex:
        api_call = ex.submit(smoke.http, "POST", api.rstrip("/") + "/analyze", {"sessionId": sid, "messageId": mid}, token)
        shown = None
        while time.time() - t < 90:
            code, body = smoke.http("GET", f"{smoke.FS}/sessions/{sid}/messages/{mid}", token=token)
            st = body.get("fields", {}).get("status", {}).get("stringValue")
            if st and st != "analyzing":
                shown = time.time() - t
                break
            time.sleep(0.25)
        code, _ = api_call.result()
    return {"shown": shown and round(shown, 2), "api": round(time.time() - t, 2), "status": st, "code": code}


def latency(api, key, n, workers):
    """--latency N: 確認質問が出る文と出ない文を交互に N 回流し、患者の待ち時間を測る。"""
    texts = [DEMO_TEXT, "I'm allergic to penicillin.", "I feel dizzy when I stand up.", "我没有发烧。"]
    sessions, out = {}, []
    for i in range(0, n, 40):  # 1 session の呼び出し上限（MAX_CALLS_PER_SESSION）に当たらないように分ける
        sessions[i] = open_session(key, "en")
    jobs = [(texts[i % len(texts)], *sessions[i - i % 40]) for i in range(n)]
    with ThreadPoolExecutor(workers) as ex:
        out = list(ex.map(lambda j: {"text": j[0], **wait_shown(api, j[1], j[2], j[0])}, jobs))
    shown = sorted(r["shown"] for r in out if r["shown"])
    q = statistics.quantiles(shown, n=20)
    summary = {"n": n, "ok": len(shown), "median": round(statistics.median(shown), 1), "p90": round(q[17], 1),
               "p95": round(q[18], 1), "max": round(max(shown), 1), "over8s": sum(s > 8 for s in shown)}
    (OUT / "latency.json").write_text(json.dumps({"summary": summary, "runs": out}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


def correct_gemini(case, r):
    if "error" in r:
        return False
    if not case["ask"]:
        return r["target"] is None
    return bool(r["target"]) and any(t.lower() in r["target"].lower() or r["target"].lower() in t.lower()
                                     for t in case["targets"])


def correct_keyword(case, hits):
    if not case["ask"]:
        return not hits
    return any(any(h in t.lower() or t.lower() in h for t in case["targets"]) for h in hits)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--demo", type=int, default=20, help="デモの1文を流す回数（0 で省く）")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--latency", type=int, default=0, help="評価の代わりに、患者の待ち時間を N 回測る")
    a = p.parse_args()
    if a.latency:
        return latency(a.api, a.key, a.latency, min(a.workers, 2))

    sessions = {lang: open_session(a.key, lang) for lang in sorted({c["lang"] for c in CASES})}
    jobs = [(c, *sessions[c["lang"]]) for c in CASES]
    jobs += [(dict(lang="en", group="demo", ask=True, targets=["for a while"], text=DEMO_TEXT), *sessions["en"])] * a.demo
    with ThreadPoolExecutor(a.workers) as ex:
        results = list(ex.map(lambda j: analyze(a.api, j[1], j[2], j[0]["text"]), jobs))

    rows = []
    for (case, _, _), r in zip(jobs, results):
        hits = keyword_hit(case["text"], case["lang"])
        rows.append({**case, "gemini": r, "keyword_hits": hits,
                     "gemini_ok": correct_gemini(case, r), "keyword_ok": correct_keyword(case, hits)})
    (OUT / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    report(rows)


def report(rows):
    main_rows = [r for r in rows if r["group"] != "demo"]
    demo = [r for r in rows if r["group"] == "demo"]
    groups = ["keyword", "paraphrase", "meaning", "context", "clear"]
    label = {"keyword": "単語リストにある曖昧な語", "paraphrase": "言い回しの曖昧さ（リストに無い）",
             "meaning": "意味が複数ある語", "context": "リストの語があるが聞かなくてよい", "clear": "はっきりした発言"}
    L = ["# 確認質問の評価: Gemini vs 単語リスト", "",
         f"{len(main_rows)} 文（英・中・越）。正解 = 聞くべき発言で正しい語について聞く／聞かなくてよい発言で聞かない。", "",
         "| 種類 | 文の数 | Gemini | 単語リスト |", "|---|---|---|---|"]
    for g in groups:
        rs = [r for r in main_rows if r["group"] == g]
        L.append(f"| {label[g]} | {len(rs)} | {sum(r['gemini_ok'] for r in rs)} | {sum(r['keyword_ok'] for r in rs)} |")
    L.append(f"| **合計** | {len(main_rows)} | **{sum(r['gemini_ok'] for r in main_rows)}** | "
             f"**{sum(r['keyword_ok'] for r in main_rows)}** |")
    for lang in ["en", "zh-CN", "vi"]:
        rs = [r for r in main_rows if r["lang"] == lang]
        L.append(f"| （{lang}） | {len(rs)} | {sum(r['gemini_ok'] for r in rs)} | {sum(r['keyword_ok'] for r in rs)} |")
    secs = [r["gemini"]["sec"] for r in rows if "sec" in r["gemini"] and "error" not in r["gemini"]]
    if secs:
        q = statistics.quantiles(secs, n=20)
        L += ["", f"/analyze の応答時間（facts まで含む）: 中央値 {statistics.median(secs):.1f} 秒、95% {q[-1]:.1f} 秒、最大 {max(secs):.1f} 秒"]
    if demo:
        ok = sum(r["gemini_ok"] for r in demo)
        qs = sorted({r["gemini"].get("question") or "（質問なし）" for r in demo})
        L += ["", f"## デモの1文: \"{demo[0]['text']}\"", "", f"{len(demo)} 回中 {ok} 回、\"for a while\" について確認質問が出た。",
              "", "出た質問: " + " / ".join(qs)]
    L += ["", "## 1文ずつ", "", "| 言語 | 種類 | 発言 | 正解 | Gemini の対象 | Gemini の質問 | 単語リスト | G | K |", "|---|---|---|---|---|---|---|---|---|"]
    for r in main_rows:
        g = r["gemini"]
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["lang"], r["group"], r["text"], " / ".join(r["targets"]) or "聞かない",
            g.get("error") or g.get("target") or "—", (g.get("question") or "").replace("|", "/"),
            ", ".join(r["keyword_hits"]) or "—", "○" if r["gemini_ok"] else "×", "○" if r["keyword_ok"] else "×"))
    (OUT / "results.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:20]))


if __name__ == "__main__":
    main()
