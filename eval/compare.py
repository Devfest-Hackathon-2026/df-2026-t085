"""Kumu 2.0 の比較評価: 同じ 100 件の模擬症例（eval/ussa_cases.py）で、4 つの方式を比べる。

  A  通常の直接翻訳（文脈なし）
  B  直接翻訳＋発話意図の解析（Source Agent・直前の会話を文脈に）
  C  B＋Semantic Bridge＋Verification Engine（独立した照合・訳し直し・照合で相違が残れば発言者の確認）
  D  Kumu 完全版（C＋Meaning Engine の確認候補＋Conversation Auditor＋医師・発言者の判断）
  E  （比較用）英語を経由する翻訳。否定・数量・日時の保持だけを見る

  python eval/compare.py --live    本物の Gemini（Vertex AI）。gcloud auth application-default login と Vertex AI の権限が要る。
                                   結果を eval/compare_results.md / .json に書く（モデル・日時・件数を記録）
  python eval/compare.py --mock    偽の AI（devtools/fake_ai.py）で、評価の仕組みが最後まで動くかだけを確かめる。
                                   偽の AI はデモの数文しか知らないので、数値は意味を持たない。ファイルには書かない

サーバー（main.py）と同じ関数（_draft・_verify・ussa・clinical）を Firestore なしで呼ぶ。
ルール層だけの実測は eval/rules_eval.py、安全性（二重送信・問診どうしの混入など）は pytest（tests/）。
"""
import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import clinical  # noqa: E402
import main  # noqa: E402
import unsaid_ai  # noqa: E402
import ussa  # noqa: E402
from eval.clinical_cases import TEACHBACKS  # noqa: E402
from eval.ussa_cases import EXPLANATION, INTENT, MEDICAL, PRECISION, UNDERSTANDING  # noqa: E402

OUT = Path(__file__).parent
DIRECTION = {"doctor": "doctor_to_patient", "patient": "patient_to_doctor"}


def timed(fn, *a, **k):
    t = time.time()
    out, _ = fn(*a, **k)
    out.pop("_meta", None)
    return out, round((time.time() - t) * 1000)


def has(text, words):
    low = (text or "").lower()
    return any(w.lower() in low for w in words)


def strong_rule_issues(src, sl, tr, tl):
    return [i for i in ussa.rule_check(src, sl, tr, tl) if i["strength"] == "strong"]


def gate_pipeline(text, lang, listener, speaker, turns):
    """C/D の、送信操作から Safety Gate の判断まで（main._gate_run と同じ順。Firestore は使わない）。"""
    r = {"ms": {}}
    with ThreadPoolExecutor(2) as ex:
        f_src = ex.submit(timed, unsaid_ai.source_agent, text, speaker, lang, listener, turns)
        f_tr = ex.submit(main._draft, {"original": text}, lang, listener, DIRECTION[speaker], None, turns, None, None)
        raw, r["ms"]["source"] = f_src.result()
        intent = ussa.build_intent(raw, source_text=text, speaker_lang=lang, listener_lang=listener,
                                   schema=unsaid_ai.schema("source"))
        r["intent"] = intent["state"]
        if intent["state"] not in ussa.INTENT_OK:
            r["outcome"], r["actions"] = "asked_intent", 2  # 送信1回＋意味を選ぶ1回
            return r, intent
        draft, r["ms"]["translate"] = f_tr.result()
    timings, attempts = {}, []
    for attempt in range(main.TRANSLATION_RETRIES + 1):
        v = main._verify(text, lang, draft["translation"], listener, speaker, intent, None, timings, f"verify{attempt}")
        attempts.append(v["status"])
        if v["status"] != ussa.MISMATCH or attempt == main.TRANSLATION_RETRIES:
            break
        fix = [{"type": i["type"], "detail": i["detail"]} for i in v["issues"] if i["strength"] == "strong"][:6]
        draft, timings[f"retranslate{attempt}"] = main._draft({"original": text}, lang, listener, DIRECTION[speaker],
                                                               None, turns, None, fix)
    r["ms"].update(timings)
    r["attempts"], r["verification"], r["translation"] = attempts, v["status"], draft["translation"]
    r["outcome"] = "sent" if v["status"] in (ussa.VERIFIED, ussa.AMBIGUOUS) else "needs_review"
    r["actions"] = 1 + (r["outcome"] == "needs_review")
    return r, intent


def critical_ms(ms):
    """患者・医師が待つ時間の目安: 意図の解析と下書きの訳は並列、照合はその後（訳し直しがあれば足す）。"""
    first = max(ms.get("source", 0), ms.get("translate", 0))
    return first + sum(v for k, v in ms.items() if k.startswith(("verify", "retranslate")))


# ───────────── 方式ごとの実行 ─────────────
def run_intent(c):
    out = {"id": c["id"], "expect": c["expect"]}
    turns = c["context"]
    try:
        a, ms = timed(unsaid_ai.translate, c["text"], c["lang"], c["listener"], DIRECTION[c["speaker"]])
        out["A"] = {"translation": a["translation"], "asked": False, "ms": ms}
        b_tr, ms_tr = main._draft({"original": c["text"]}, c["lang"], c["listener"], DIRECTION[c["speaker"]], None, turns,
                                  None, None)
        raw, ms_src = timed(unsaid_ai.source_agent, c["text"], c["speaker"], c["lang"], c["listener"], turns)
        intent = ussa.build_intent(raw, source_text=c["text"], speaker_lang=c["lang"], listener_lang=c["listener"])
        asked = intent["state"] not in ussa.INTENT_OK
        out["B"] = {"translation": "" if asked else b_tr["translation"], "asked": asked,
                    "ms": max(ms_tr, ms_src), "intent": intent["state"]}
        g, _ = gate_pipeline(c["text"], c["lang"], c["listener"], c["speaker"], turns)
        out["C"] = out["D"] = {"translation": g.get("translation", ""), "asked": g["outcome"] == "asked_intent",
                               "outcome": g["outcome"], "ms": critical_ms(g["ms"]), "actions": g["actions"]}
    except Exception as e:
        out["error"] = type(e).__name__
    for k in "ABCD":
        r = out.get(k)
        if r:
            r["intent_ok"] = (not r["asked"]) and has(r["translation"], c.get("ok", [])) and not has(r["translation"], c.get("bad", []))
    return out


def run_medical(c):
    out = {"id": c["id"], "expected": bool(c["ambiguities"])}
    try:
        tr, ms = timed(unsaid_ai.translate, c["text"], c["lang"], "ja", "patient_to_doctor")
        out["A_forbidden"] = [w for w in c["forbidden"] if w in tr["translation"]]
        raw, ms_m = timed(unsaid_ai.meaning, c["text"], c["lang"], "ja", [], [], None)
        m = clinical.build_meaning(raw, utterance_id="eval", source_text=c["text"], source_lang=c["lang"],
                                   schema=unsaid_ai.schema("meaning"))
        memo = " / ".join(f["normalized_meaning"] for f in m["facts"])
        gold = [g.lower() for g in c["ambiguities"]]
        hit = any(any(g in x["source_quote"].lower() or x["source_quote"].lower() in g for g in gold) for x in m["candidates"])
        out.update(D_found=hit, D_candidates=[x["source_quote"] for x in m["candidates"]],
                   D_forbidden=[w for w in c["forbidden"] if w in memo],
                   D_rejected_ungrounded=sum(r["reason"] == "quote_not_in_source" for r in m["rejected"]), ms=ms_m)
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def run_precision(c, pivot):
    out = {"id": c["id"], "error_type": c["error"]}
    sl, tl, sp = c["lang"], c["listener"], c["speaker"]
    try:
        a, ms = timed(unsaid_ai.translate, c["text"], sl, tl, DIRECTION[sp])
        out["A"] = {"changed": bool(strong_rule_issues(c["text"], sl, a["translation"], tl)), "ms": ms}
        out["B"] = dict(out["A"])  # B は訳の照合をしない（意図の解析だけ）ので、訳の正確さは A と同じ
        g, intent = gate_pipeline(c["text"], sl, tl, sp, [])
        delivered = g["outcome"] == "sent"
        out["C"] = out["D"] = {"changed": delivered and bool(strong_rule_issues(c["text"], sl, g.get("translation", ""), tl)),
                               "outcome": g["outcome"], "attempts": g.get("attempts", []), "ms": critical_ms(g["ms"]),
                               "actions": g["actions"]}
        if g["outcome"] != "asked_intent":
            t = {}
            vb = main._verify(c["text"], sl, c["corrupted"], tl, sp, intent, None, t, "x")
            vg = main._verify(c["text"], sl, c["reference"], tl, sp, intent, None, t, "y")
            out["injected"] = {"corrupted": vb["status"], "reference": vg["status"]}
        if pivot and "en" not in (sl, tl):
            mid, _ = timed(unsaid_ai.translate, c["text"], sl, "en", DIRECTION[sp])
            e, _ = timed(unsaid_ai.translate, mid["translation"], "en", tl, DIRECTION[sp])
            out["E"] = {"changed": bool(strong_rule_issues(c["text"], sl, e["translation"], tl))}
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def run_explanation(c):
    out = {"id": c["id"], "expected": c["insufficient"]}
    try:
        raw, ms = timed(unsaid_ai.audit, {"utterance_id": "d1", "speaker": "doctor", "text": c["text"],
                                          "speech_act": "instruction"}, [], [], [])
        clinical.validate(raw, unsaid_ai.schema("audit"))
        a = ussa.empty_audit()
        ussa.merge_audit(a, raw, latest_id="d1", utterances={"d1": {"original": c["text"], "translation": ""}})
        out["D_flagged"] = any(i["type"] == "insufficient_explanation" for i in a["issues"])
        out["ms"] = ms
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def run_understanding(c):
    out = {"id": c["id"], "expected": c["mismatch"]}
    try:
        exp_raw, _ = timed(unsaid_ai.source_agent, c["explanation"], "doctor", "ja", c["lang"], [])
        exp = ussa.build_intent(exp_raw, source_text=c["explanation"], speaker_lang="ja", listener_lang=c["lang"])
        exp_tr, _ = timed(unsaid_ai.translate, c["explanation"], "ja", c["lang"], "doctor_to_patient")
        turns = [{"speaker": "doctor", "original": c["explanation"], "translation": exp_tr["translation"]}]
        pat_raw, _ = timed(unsaid_ai.source_agent, c["restatement"], "patient", c["lang"], "ja", turns)
        pat = ussa.build_intent(pat_raw, source_text=c["restatement"], speaker_lang=c["lang"], listener_lang="ja")
        pat_tr, _ = timed(unsaid_ai.translate, c["restatement"], c["lang"], "ja", "patient_to_doctor", turns=turns)
        diffs = ussa.cross_check(exp["frame"], pat["frame"])
        mem = [ussa.memory_item("d1", {"from": "doctor", "original": c["explanation"]}, exp["frame"])]
        raw, ms = timed(unsaid_ai.audit, {"utterance_id": "p1", "speaker": "patient", "text": pat_tr["translation"],
                                          "speech_act": pat["frame"]["speech_act"]}, mem, [], [])
        a = ussa.empty_audit()
        utter = {"d1": {"original": c["explanation"], "translation": exp_tr["translation"]},
                 "p1": {"original": c["restatement"], "translation": pat_tr["translation"]}}
        ussa.merge_audit(a, raw, latest_id="p1", utterances=utter, schema=unsaid_ai.schema("audit"))
        llm = any(ussa.ISSUE_FAMILY.get(i["type"]) == "understanding" for i in a["issues"])
        out.update(D_rule=bool(diffs), D_llm=llm, D_flagged=bool(diffs) or llm, rule_detail=diffs, ms=ms)
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def run_teachback(c):
    out = {"id": c["id"], "expected": c["expected"]}
    try:
        tr, _ = timed(unsaid_ai.translate, c["explanation"], "ja", c["lang"], "doctor_to_patient")
        raw, ms = timed(unsaid_ai.compare_understanding, c["explanation"], tr["translation"], c["restatement"], c["lang"])
        r = clinical.build_teachback(raw, restatement=c["restatement"], schema=unsaid_ai.schema("teachback"))
        out.update(overall=r["overall"], flagged=r["overall"] == "inconsistent", ms=ms)
    except Exception as e:
        out["error"] = type(e).__name__
    return out


# ───────────── 小さな比較（--subset）: 種類・正解ラベルごとに偏らず 30 件を選ぶ（毎回同じ件）─────────────
SUBSET = {"intent": ("expect", {"clarify": 4, "resolved": 4}),
          "medical": (lambda c: bool(c["ambiguities"]), {True: 3, False: 1}),
          "precision": ("error", None),  # 誤りの種類ごとに 1 件ずつ、8 件まで
          "explanation": ("insufficient", {True: 2, False: 2}),
          "understanding": ("mismatch", {True: 3, False: 1}),
          "teachback": ("expected", {"inconsistent": 1, "consistent": 1})}


def subset(name, cases):
    key, quota = SUBSET[name]
    get = key if callable(key) else (lambda c: c[key])
    if quota is None:
        seen, out = set(), []
        for c in cases:
            if get(c) not in seen and len(out) < 8:
                seen.add(get(c))
                out.append(c)
        return out
    left = dict(quota)
    out = []
    for c in cases:
        if left.get(get(c), 0) > 0:
            left[get(c)] -= 1
            out.append(c)
    return out


# ───────────── 集計 ─────────────
def frac(n, d):
    return f"{n}/{d}（{100 * n / d:.0f}%）" if d else "—"


def pct3(xs):
    xs = sorted(x for x in xs if x is not None)
    if len(xs) < 2:
        return "—"
    q = statistics.quantiles(xs, n=100)
    return f"{statistics.median(xs) / 1000:.1f} / {q[94] / 1000:.1f} / {q[98] / 1000:.1f} 秒"


def report(R, mode, started, pivot):
    it = [r for r in R["intent"] if "error" not in r]
    need = [r for r in it if r["expect"] == "clarify"]
    noneed = [r for r in it if r["expect"] == "resolved"]
    pr = [r for r in R["precision"] if "error" not in r]
    md = [r for r in R["medical"] if "error" not in r]
    ex = [r for r in R["explanation"] if "error" not in r]
    un = [r for r in R["understanding"] if "error" not in r]
    errs = sum("error" in r for k in R for r in R[k])
    neg = [r for r in pr if r["error_type"] == "negation" and "injected" in r]
    inj = [r for r in pr if "injected" in r]
    L = ["# 比較評価: A 通常の翻訳 / B ＋意図の解析 / C ＋Semantic Bridge・検証 / D Kumu 完全版", "",
         f"- 実行: {started}（{'本物の Gemini: ' + unsaid_ai.MODEL + '（役割ごと: source=' + unsaid_ai.SOURCE_MODEL + ', verify=' + unsaid_ai.VERIFY_MODEL + ', audit=' + unsaid_ai.AUDIT_MODEL + '）' if mode == 'live' else '偽の AI（仕組みの確認だけ。数値は性能ではない）'}）",
         f"- 症例: 意図 {len(R['intent'])}・医学的な曖昧さ {len(R['medical'])}・否定/数量/日時/単位 {len(R['precision'])}・"
         f"説明不足 {len(R['explanation'])}・理解の相違 {len(R['understanding'])}・teach-back {len(R['teachback'])}"
         f"（計 {sum(len(v) for v in R.values())}、すべて架空）。失敗した症例 {errs} 件",
         f"- 訳し直しの上限: {main.TRANSLATION_RETRIES} 回。臨床的な有効性は検証していない", "",
         "| 指標 | A | B | C | D |", "|---|---|---|---|---|"]

    def row(name, f):
        L.append(f"| {name} | " + " | ".join(f(k) for k in "ABCD") + " |")

    row("重要情報の保持（否定・数・日時が変わらずに届いた）",
        lambda k: frac(sum(not r[k]["changed"] for r in pr), len(pr)))
    row("誤りを入れた訳の検出（意味の反転・数・日時など）",
        lambda k: "仕組みなし" if k in "AB" else frac(sum(r["injected"]["corrupted"] != ussa.VERIFIED for r in inj), len(inj)))
    row("意味の反転の見逃し（否定の反転を入れた訳）",
        lambda k: frac(len(neg), len(neg)) if k in "AB" else frac(sum(r["injected"]["corrupted"] == ussa.VERIFIED for r in neg), len(neg)))
    row("誤訳の見逃し（誤りを入れた訳を、そのまま届く判定にした）",
        lambda k: frac(len(inj), len(inj)) if k in "AB" else frac(sum(r["injected"]["corrupted"] in (ussa.VERIFIED, ussa.AMBIGUOUS) for r in inj), len(inj)))
    for et, label in (("negation", "否定"), ("quantity", "数量"), ("condition_missing", "条件の欠落"), ("frequency", "回数"), ("time", "日時")):
        grp = [r for r in inj if r["error_type"] == et]
        if grp:
            row(f"　うち{label}の取り違えの見逃し",
                lambda k, grp=grp: frac(len(grp), len(grp)) if k in "AB" else frac(sum(r["injected"]["corrupted"] in (ussa.VERIFIED, ussa.AMBIGUOUS) for r in grp), len(grp)))
    row("正しい訳を誤って止めた（照合の誤警報）",
        lambda k: "—" if k in "AB" else frac(sum(r["injected"]["reference"] in (ussa.MISMATCH, ussa.VERIFICATION_FAILED) for r in inj), len(inj)))
    row("根拠のない情報の追加（病名などが出た数）",
        lambda k: str(sum(len(r["A_forbidden"]) for r in md)) if k == "A" else str(sum(len(r["D_forbidden"]) for r in md)) if k == "D" else "—")
    row("原文に無い根拠のため採用しなかった AI の出力",
        lambda k: str(sum(r["D_rejected_ungrounded"] for r in md)) if k == "D" else "—")
    row("意図の曖昧さで本人に聞いた（聞くべき発言）",
        lambda k: frac(sum(r[k]["asked"] for r in need), len(need)) if k != "A" else frac(0, len(need)))
    row("必要な確認の見逃し（意図）", lambda k: frac(sum(not r[k]["asked"] for r in need), len(need)))
    row("意味が未確認のまま送信（聞くべき発言を、聞かずに訳して届けた）",
        lambda k: frac(sum((not r[k]["asked"]) and bool(r[k]["translation"]) for r in need), len(need)))
    row("不要な確認（文脈で決まる発言で聞いた）", lambda k: frac(sum(r[k]["asked"] for r in noneed), len(noneed)))
    row("文脈で決まる発言を正しい意味で訳した", lambda k: frac(sum(r[k]["intent_ok"] for r in noneed), len(noneed)))
    medneed = [r for r in md if r["expected"]]
    medclear = [r for r in md if not r["expected"]]
    row("医学的な曖昧さを確認候補にした", lambda k: frac(sum(r["D_found"] for r in medneed), len(medneed)) if k == "D" else "仕組みなし")
    row("聞かなくてよい発言に出した確認候補", lambda k: str(sum(len(r["D_candidates"]) for r in medclear)) if k == "D" else "—")
    exneed = [r for r in ex if r["expected"]]
    exok = [r for r in ex if not r["expected"]]
    row("説明不足を指摘（足りない説明）", lambda k: frac(sum(r["D_flagged"] for r in exneed), len(exneed)) if k == "D" else "仕組みなし")
    row("説明不足の誤指摘（足りている説明）", lambda k: frac(sum(r["D_flagged"] for r in exok), len(exok)) if k == "D" else "—")
    unneed = [r for r in un if r["expected"]]
    unok = [r for r in un if not r["expected"]]
    row("理解の相違を検出", lambda k: frac(sum(r["D_flagged"] for r in unneed), len(unneed)) if k == "D" else "仕組みなし")
    row("正しい理解に警告を出した", lambda k: frac(sum(r["D_flagged"] for r in unok), len(unok)) if k == "D" else "—")
    row("発言者の確認に回した（照合で相違が残った）",
        lambda k: frac(sum(r[k].get("outcome") == "needs_review" for r in pr + it), len(pr) + len(it)) if k in "CD" else "—")
    row("1発言あたりの操作回数（平均）",
        lambda k: "1.00" if k == "A" else f"{statistics.mean([1 + r[k]['asked'] for r in it] + [1] * len(pr)):.2f}" if k == "B"
        else f"{statistics.mean([r[k]['actions'] for r in it + pr]):.2f}")
    row("待ち時間 p50 / p95 / p99（意図＋訳＋照合の合計の目安）",
        lambda k: pct3([r[k]["ms"] for r in it + pr if k in r]))
    if pivot:
        pe = [r for r in pr if "E" in r]
        L += ["", f"英語経由（E）の重要情報の保持: {frac(sum(not r['E']['changed'] for r in pe), len(pe))}"
              f"（直接訳 A は同じ {len(pe)} 件で {frac(sum(not r['A']['changed'] for r in pe), len(pe))}）"]
    tb = [r for r in R["teachback"] if "error" not in r]
    if tb:
        bad = [r for r in tb if r["expected"] == "inconsistent"]
        good = [r for r in tb if r["expected"] == "consistent"]
        L += ["", f"teach-back（D）: 食い違いの検出 {frac(sum(r['flagged'] for r in bad), len(bad))}、"
              f"正しい理解への警告 {frac(sum(r['flagged'] for r in good), len(good))}"]
    L += ["", "注: 「保持」はルール層で数・日時・曜日が変わっていないことの確認（否定は含まない）。語の一致による近似。"
          "確認質問の医学的な妥当性は人が評価していない。安全性（二重送信・問診どうしの混入・未確認の送信の防止）は pytest の結果を見る。"]
    return "\n".join(L) + "\n"


def main_():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--live", action="store_true")
    g.add_argument("--mock", action="store_true")
    ap.add_argument("--pivot", action="store_true", help="英語経由の翻訳（E）も比べる")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--subset", action="store_true", help="30 件だけ（課金を抑えて先に確かめる）")
    a = ap.parse_args()
    if a.mock:
        from devtools import fake_ai
        fake_ai.install(unsaid_ai)
    started = time.strftime("%Y-%m-%d %H:%M %Z")
    C = {"intent": INTENT, "medical": MEDICAL, "precision": PRECISION, "explanation": EXPLANATION,
         "understanding": UNDERSTANDING, "teachback": TEACHBACKS}
    if a.subset:
        C = {k: subset(k, v) for k, v in C.items()}
    with ThreadPoolExecutor(a.workers) as ex:
        R = {"intent": list(ex.map(run_intent, C["intent"])),
             "medical": list(ex.map(run_medical, C["medical"])),
             "precision": list(ex.map(lambda c: run_precision(c, a.pivot), C["precision"])),
             "explanation": list(ex.map(run_explanation, C["explanation"])),
             "understanding": list(ex.map(run_understanding, C["understanding"])),
             "teachback": list(ex.map(run_teachback, C["teachback"]))}
    md = report(R, "live" if a.live else "mock", started, a.pivot)
    if a.live:
        meta = {"started": started, "model": unsaid_ai.MODEL, "source_model": unsaid_ai.SOURCE_MODEL,
                "verify_model": unsaid_ai.VERIFY_MODEL, "audit_model": unsaid_ai.AUDIT_MODEL,
                "translation_retries": main.TRANSLATION_RETRIES, "cases": {k: len(v) for k, v in R.items()}}
        name = "compare_results_subset" if a.subset else "compare_results"
        meta.update(project=unsaid_ai.PROJECT, location=unsaid_ai.LOCATION, subset=a.subset)
        (OUT / f"{name}.json").write_text(json.dumps({"meta": meta, **R}, ensure_ascii=False, indent=1), encoding="utf-8")
        (OUT / f"{name}.md").write_text(md, encoding="utf-8")
    else:
        print("※ 偽の AI での実行。数値はモデルの性能ではない（評価の仕組みが動くかの確認）。ファイルには書かない。\n")
    print(md)


if __name__ == "__main__":
    main_()
