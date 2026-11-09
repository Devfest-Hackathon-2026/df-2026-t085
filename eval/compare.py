"""通常の翻訳（A）と UNSAID（B: 意味の構造化・確認候補・確認後の解決・理解の確認）を、同じ模擬症例で比べる。

  python eval/compare.py --live      本物の Gemini（Vertex AI）。gcloud auth application-default login と
                                     Vertex AI の権限が要る。結果を eval/compare_results.json / .md に書く
  python eval/compare.py --mock      偽の AI（devtools/fake_ai.py）で、この評価の仕組みが動くかだけを確かめる。
                                     数値はモデルの性能ではないので、ファイルには書かない

Firestore は使わない（unsaid_ai と clinical をサーバーと同じ順で直接呼ぶ）。測るもの（EVALUATION.md）:
  重要情報の保持率   A: 訳文に正解の語があるか / B: 問診メモ（原文の根拠がある情報）に正解の語があるか
  根拠のない追加     A・B とも、原文に無い語（forbidden）が出た数。B はさらに、原文に無い根拠を書いたため採用しなかった数
  曖昧さの検出       B の確認候補に正解の語が入っているか（A には仕組みが無い）。聞かなくてよい発言で候補を出した数
  確認質問の形       患者の言語で書かれているか・選択肢が 2〜4 個でそろっているか（自動で見られる形だけ。妥当性の人の評価はしていない）
  確認後の明確化     患者の自由回答で、曖昧だった所が正解の意味に解決したか
  理解の不一致の検出 teach-back で、食い違いを見つけたか・正しい理解に警告を出さなかったか
  処理時間           各呼び出しの秒数
"""
import argparse
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import clinical  # noqa: E402
import unsaid_ai  # noqa: E402
from eval.clinical_cases import TEACHBACKS, UTTERANCES  # noqa: E402

OUT = Path(__file__).parent
KANA = re.compile(r"[぀-ヿ]")
HAN = re.compile(r"[一-鿿]")
LATIN = re.compile(r"[A-Za-z]")


def has_any(text, words):
    return any(w in text for w in words)


def in_patient_lang(text, lang):
    if KANA.search(text):
        return False
    return bool(HAN.search(text)) if lang == "zh-CN" else bool(LATIN.search(text))


def timed(fn, *a, **k):
    t = time.time()
    out, _ = fn(*a, **k)
    out.pop("_meta", None)
    return out, round(time.time() - t, 2)


def run_utterance(c):
    r = {"id": c["id"], "lang": c["lang"]}
    try:
        tr, r["a_sec"] = timed(unsaid_ai.translate, c["text"], c["lang"], "ja", "patient_to_doctor")
        r["a_translation"] = tr["translation"]
        raw, r["b_sec"] = timed(unsaid_ai.meaning, c["text"], c["lang"], "ja", [], [], None)
        m = clinical.build_meaning(raw, utterance_id="eval", source_text=c["text"], source_lang=c["lang"],
                                   schema=unsaid_ai.schema("meaning"))
    except Exception as e:  # 1件の失敗で全体を止めない（失敗として数える）
        r["error"] = f"{type(e).__name__}"
        return r
    memo = " / ".join(f["normalized_meaning"] for f in m["facts"])
    r["b_memo"] = memo
    r["key_total"] = len(c["key_facts"])
    r["a_key"] = sum(has_any(r["a_translation"], k) for k in c["key_facts"])
    r["b_key"] = sum(has_any(memo, k) for k in c["key_facts"])
    r["a_forbidden"] = [w for w in c["forbidden"] if w in r["a_translation"]]
    r["b_forbidden"] = [w for w in c["forbidden"] if w in memo]
    r["b_rejected_ungrounded"] = sum(x["reason"] == "quote_not_in_source" for x in m["rejected"])
    r["candidates"] = [{"quote": x["source_quote"], "question_patient": x["question_patient"],
                        "question_doctor": x["question_doctor"], "options_patient": x["options_patient"],
                        "options_doctor": x["options_doctor"], "priority": x["priority"]} for x in m["candidates"]]
    gold = [g.lower() for g in c["ambiguities"]]
    hit = next((x for x in m["candidates"] if any(g in x["source_quote"].lower() or x["source_quote"].lower() in g
                                                   for g in gold)), None)
    r["expected_ambiguity"] = bool(gold)
    r["ambiguity_found"] = bool(hit)
    r["unneeded_candidates"] = 0 if gold else len(m["candidates"])
    r["question_shape_ok"] = [
        in_patient_lang(x["question_patient"] + "".join(x["options_patient"]), c["lang"])
        and (not x["options_patient"] or 2 <= len(x["options_patient"]) == len(x["options_doctor"]) <= 4)
        and bool(x["question_doctor"])
        for x in m["candidates"]]
    if hit and c.get("answer"):
        pending = {"source_quote": hit["source_quote"], "question_doctor_lang": hit["question_doctor"],
                   "question_patient_lang": hit["question_patient"], "unresolved": hit["unresolved"]}
        try:
            raw2, r["resolve_sec"] = timed(unsaid_ai.meaning, c["answer"]["text"], c["lang"], "ja", [], [], pending)
            m2 = clinical.build_meaning(raw2, utterance_id="eval2", source_text=c["answer"]["text"],
                                        source_lang=c["lang"], schema=unsaid_ai.schema("meaning"))
            res = m2["resolution"]
            r["resolution"] = res["meaning_doctor"]
            r["resolved_ok"] = res["resolves"] in ("yes", "partly") and has_any(res["meaning_doctor"], c["answer"]["expect"])
        except Exception as e:
            r["resolution_error"] = type(e).__name__
            r["resolved_ok"] = False
    return r


def run_teachback(c):
    r = {"id": c["id"], "lang": c["lang"], "expected": c["expected"]}
    try:
        tr, r["translate_sec"] = timed(unsaid_ai.translate, c["explanation"], "ja", c["lang"], "doctor_to_patient")
        raw, r["sec"] = timed(unsaid_ai.compare_understanding, c["explanation"], tr["translation"], c["restatement"], c["lang"])
        out = clinical.build_teachback(raw, restatement=c["restatement"], schema=unsaid_ai.schema("teachback"))
    except Exception as e:
        r["error"] = type(e).__name__
        return r
    r["overall"] = out["overall"]
    r["points"] = out["points"]
    r["flagged"] = out["overall"] == "inconsistent"
    return r


def pct(n, d):
    return f"{n}/{d}（{100 * n / d:.0f}%）" if d else "—"


def med(xs):
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs):.1f} 秒（{len(xs)} 回）" if xs else "—"


def report(utt, tbs, mode, started):
    ok = [r for r in utt if "error" not in r]
    L = ["# 比較評価: 通常の翻訳（A）と UNSAID（B）", "",
         f"- 実行: {started}（{'本物の Gemini: ' + unsaid_ai.MODEL if mode == 'live' else '偽の AI（仕組みの確認だけ。性能ではない）'}）",
         f"- 症例: 発言 {len(utt)} 件（中国語・英語・ベトナム語）、理解の確認 {len(tbs)} 件（医師の説明は日本語）。すべて架空",
         f"- 失敗した呼び出し: 発言 {len(utt) - len(ok)} 件、理解の確認 {sum('error' in r for r in tbs)} 件", "",
         "| 指標 | A: 通常の翻訳 | B: UNSAID |", "|---|---|---|"]
    kt = sum(r["key_total"] for r in ok)
    L.append(f"| 重要情報の保持（正解の語が含まれる） | {pct(sum(r['a_key'] for r in ok), kt)}（訳文） | "
             f"{pct(sum(r['b_key'] for r in ok), kt)}（問診メモ） |")
    L.append(f"| 原文に無い語（病名など）が出た数 | {sum(len(r['a_forbidden']) for r in ok)} | "
             f"{sum(len(r['b_forbidden']) for r in ok)} |")
    L.append(f"| 原文に無い根拠のため採用しなかった AI の情報 | — | {sum(r['b_rejected_ungrounded'] for r in ok)} |")
    amb = [r for r in ok if r["expected_ambiguity"]]
    L.append(f"| 曖昧さの検出（聞くべき発言） | 仕組みなし | {pct(sum(r['ambiguity_found'] for r in amb), len(amb))} |")
    clear = [r for r in ok if not r["expected_ambiguity"]]
    L.append(f"| 聞かなくてよい発言に出した確認候補 | — | {sum(r['unneeded_candidates'] for r in clear)} 件（{len(clear)} 発言中） |")
    shapes = [x for r in ok for x in r["question_shape_ok"]]
    L.append(f"| 確認質問の形（患者の言語・選択肢がそろう） | — | {pct(sum(shapes), len(shapes))} |")
    res = [r for r in ok if "resolved_ok" in r]
    L.append(f"| 確認後に意味が解決（患者の自由回答） | 仕組みなし | {pct(sum(r['resolved_ok'] for r in res), len(res))} |")
    tok = [r for r in tbs if "error" not in r]
    bad = [r for r in tok if r["expected"] == "inconsistent"]
    good = [r for r in tok if r["expected"] == "consistent"]
    L.append(f"| 理解の不一致を検出 | 仕組みなし | {pct(sum(r['flagged'] for r in bad), len(bad))} |")
    L.append(f"| 正しい理解に警告を出した | — | {sum(r['flagged'] for r in good)} 件（{len(good)} 件中） |")
    L.append(f"| 処理時間（中央値） | 翻訳 {med([r.get('a_sec') for r in ok])} | 意味の構造化 {med([r.get('b_sec') for r in ok])}、"
             f"理解の比較 {med([r.get('sec') for r in tok])} |")
    L += ["", "B の処理は翻訳と並列に走るので、患者の待ち時間は A と同じ翻訳1本分（サーバーの /analyze）。",
          "語の一致で数えるため、言い換えは取りこぼす。確認質問の「医学的な妥当性」は人が評価していない。臨床的な有効性は検証していない。",
          "", "## 発言ごと", "", "| 症例 | 保持 A | 保持 B | 曖昧さ | 確認候補 | 解決 | A の禁止語 | B の禁止語 |", "|---|---|---|---|---|---|---|---|"]
    for r in utt:
        if "error" in r:
            L.append(f"| {r['id']} | 失敗: {r['error']} | | | | | | |")
            continue
        L.append("| {} | {}/{} | {}/{} | {} | {} | {} | {} | {} |".format(
            r["id"], r["a_key"], r["key_total"], r["b_key"], r["key_total"],
            ("○" if r["ambiguity_found"] else "×") if r["expected_ambiguity"] else "（不要）",
            " / ".join(x["quote"] for x in r["candidates"]) or "—",
            {True: "○", False: "×"}.get(r.get("resolved_ok"), "—"),
            "、".join(r["a_forbidden"]) or "—", "、".join(r["b_forbidden"]) or "—"))
    L += ["", "## 理解の確認ごと", "", "| 症例 | 正解 | 判定 |", "|---|---|---|"]
    for r in tbs:
        L.append(f"| {r['id']} | {r['expected']} | {r.get('overall') or '失敗: ' + r.get('error', '')} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--live", action="store_true")
    g.add_argument("--mock", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.mock:
        from devtools import fake_ai
        fake_ai.install(unsaid_ai)
    started = time.strftime("%Y-%m-%d %H:%M")
    with ThreadPoolExecutor(a.workers) as ex:
        utt = list(ex.map(run_utterance, UTTERANCES))
        tbs = list(ex.map(run_teachback, TEACHBACKS))
    md = report(utt, tbs, "live" if a.live else "mock", started)
    if a.live:
        (OUT / "compare_results.json").write_text(json.dumps({"utterances": utt, "teachbacks": tbs}, ensure_ascii=False,
                                                             indent=1), encoding="utf-8")
        (OUT / "compare_results.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
