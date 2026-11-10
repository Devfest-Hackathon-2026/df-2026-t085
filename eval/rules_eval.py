"""Verification Engine のルール層（D: 数・時刻・日付・曜日・否定）と Safety Gate の処理時間を、AI を使わずに測る。

  python eval/rules_eval.py      → eval/rules_results.md

ルール層は決まった動きをするコードなので、ここの数字は実測（モデルの性能ではない）。
PRECISION の 20 件で、人が書いた正しい訳（reference）と、誤りを1つ入れた訳（corrupted）を照合する。
LLM の層（B: 訳文だけから取った枠、C: 直接の照合）は含まない（eval/compare.py --live で測る）。
"""
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import ussa  # noqa: E402
from eval.ussa_cases import PRECISION  # noqa: E402

OUT = Path(__file__).parent / "rules_results.md"


def strong(issues):
    return [i for i in issues if i["strength"] == "strong"]


def bench(fn, n=2000):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1e6)
    ts.sort()
    q = statistics.quantiles(ts, n=100)
    return {"p50": statistics.median(ts), "p95": q[94], "p99": q[98], "n": n}


def main():
    rows, caught, false_alarm = [], 0, 0
    for c in PRECISION:
        bad = strong(ussa.rule_check(c["text"], c["lang"], c["corrupted"], c["listener"]))
        good = strong(ussa.rule_check(c["text"], c["lang"], c["reference"], c["listener"]))
        weak_bad = [i for i in ussa.rule_check(c["text"], c["lang"], c["corrupted"], c["listener"]) if i["strength"] == "weak"]
        caught += bool(bad)
        false_alarm += bool(good)
        rows.append((c, bad, good, weak_bad))

    by_type = {}
    for c, bad, _, _ in rows:
        t = by_type.setdefault(c["error"], [0, 0])
        t[0] += 1
        t[1] += bool(bad)

    # Safety Gate とルール層の処理時間（AI を含まない、このコード自体の時間）
    c = PRECISION[0]
    msg = {"from": "patient", "original": "hi", "gate": {
        "state": ussa.READY, "source_confirmed_at": "t", "source_hash": ussa.source_hash("hi"), "run_id": "r",
        "intent": {"state": ussa.RESOLVED}, "verification": {"status": ussa.VERIFIED}, "draft": {"translation": "x"}}}
    t_rules = bench(lambda: ussa.rule_check(c["text"], c["lang"], c["reference"], c["listener"]))
    t_checks = bench(lambda: ussa.send_checks(msg, run_id="r", author_role="patient"))

    neg = [r for r in rows if r[0]["error"] == "negation"]
    neg_weak = sum(bool(r[3]) for r in neg)
    ref_weak = sum(any(i["strength"] == "weak" for i in ussa.rule_check(c["text"], c["lang"], c["reference"], c["listener"]))
                   for c in PRECISION)
    L = ["# ルール層（Verification Engine の D）の実測", "",
         f"実行: {time.strftime('%Y-%m-%d %H:%M')}。AI を使わない決まった動きのコードなので、何度流しても同じ結果になる。",
         "対象: `eval/ussa_cases.py` の PRECISION 20 件（日本語・中国語・英語・ベトナム語）。正しい訳と、誤りを1つ入れた訳を人が書いた。", "",
         "| 指標 | 結果 |", "|---|---|",
         f"| 誤りを入れた訳を「強い不一致」として検出 | {caught} / {len(rows)} |",
         f"| 正しい訳を誤って「強い不一致」とした（誤警報） | {false_alarm} / {len(rows)} |",
         f"| 否定の反転に「弱い証拠」（否定の有無の違い）を出した | {neg_weak} / {len(neg)} |",
         f"| 正しい訳にも「弱い証拠」が出た（弱い証拠だけでは止めない理由） | {ref_weak} / {len(rows)} |",
         "", "誤りの種類ごと（検出数 / 件数）:", "",
         "| 種類 | 検出 |", "|---|---|"]
    L += [f"| {k} | {v[1]} / {v[0]} |" for k, v in sorted(by_type.items())]
    L += ["", "ルール層だけでは見つけられないもの: 否定の反転（字面の否定は「弱い」証拠として扱い、枠の比較と LLM の照合で判断する）、"
          "条件の欠落（数を含まないもの）、数の入れ替え（同じ数の組のまま位置が入れ替わる）。これらは B・C の層の役割で、"
          "本物の Gemini での実測は `eval/compare.py --live`（未実施）。", "",
          "## このコード自体の処理時間（AI を含まない）", "",
          "| 処理 | p50 | p95 | p99 |", "|---|---|---|---|",
          f"| ルール照合（rule_check 1回） | {t_rules['p50']:.0f} µs | {t_rules['p95']:.0f} µs | {t_rules['p99']:.0f} µs |",
          f"| Safety Gate の最終チェック（send_checks 1回） | {t_checks['p50']:.1f} µs | {t_checks['p95']:.1f} µs | {t_checks['p99']:.1f} µs |",
          "", "（Firestore のトランザクションの往復時間は含まない。各 2000 回）", "",
          "## 1件ずつ", "", "| 症例 | 誤りの種類 | 誤りの訳: 強い不一致 | 正しい訳: 強い不一致 | 誤りの訳: 弱い証拠 |", "|---|---|---|---|---|"]
    for c, bad, good, weak in rows:
        L.append("| {} | {} | {} | {} | {} |".format(
            c["id"], c["error"], "；".join(i["detail"] for i in bad) or "—（見逃し）",
            "；".join(i["detail"] for i in good) or "なし", "；".join(i["detail"] for i in weak) or "—"))
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:16]))


if __name__ == "__main__":
    main()
