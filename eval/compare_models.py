"""どのモデルで確認質問を作るか: 同じ40文（eval/cases.py）を複数のモデルに流し、正解数・速さ・トークン数を比べる。

Cloud Run を通さず、この PC から Vertex AI を直接呼ぶ（test.py と同じ。gcloud auth application-default login 済みであること）。
速さはモデルそのものを比べたいので、hedged request（HEDGE_SEC）は切って測る。

使い方（リポジトリの一番上で、1行で）:
  python eval/compare_models.py --models gemini-3.8-flash,gemini-3.8-flash-lite
  python eval/compare_models.py --models gemini-3.8-flash,gemini-3.8-flash-lite --runs 2
モデル名が違うと 404 になる。Vertex AI の Model Garden に出ている名前を入れる。
結果は eval/models.md と eval/models.json に書く。
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")  # Windows でベトナム語・中国語を出しても落ちないように

import unsaid_ai  # noqa: E402
from cases import CASES  # noqa: E402
from run import correct_gemini, keyword_hit, correct_keyword  # noqa: E402

OUT = Path(__file__).parent


def one(case):
    t = time.time()
    try:
        out, _ = unsaid_ai.clarify(case["text"], case["lang"])
    except Exception as e:  # noqa: BLE001  モデル名違い・混雑などはその文だけ失敗として数える
        return {"error": f"{type(e).__name__}: {str(e)[:120]}", "sec": time.time() - t}
    c = out["clarifications"][0] if out["clarifications"] else None
    m = out.get("_meta", {})
    return {"sec": round(time.time() - t, 2), "target": c and c["target"], "question": c and c["question"],
            "in_tokens": m.get("in_tokens"), "out_tokens": m.get("out_tokens"), "thoughts": m.get("thoughts")}


def run_model(model, runs, workers):
    unsaid_ai.MODEL = model
    rows = []
    for r in range(runs):
        with ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(one, CASES))
        rows += [{"run": r + 1, **c, "result": x, "ok": correct_gemini(c, x)} for c, x in zip(CASES, res)]
        print(f"  {model} run {r + 1}: {sum(x['ok'] for x in rows if x['run'] == r + 1)} / {len(CASES)}")
    return rows


def avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs)) if xs else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", required=True, help="カンマ区切り")
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    unsaid_ai.HEDGE_SEC = 0
    unsaid_ai.FALLBACK_MODEL = ""

    allrows, L = {}, []
    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        print(model)
        allrows[model] = run_model(model, a.runs, a.workers)
    (OUT / "models.json").write_text(json.dumps(allrows, ensure_ascii=False, indent=1), encoding="utf-8")

    kw = sum(correct_keyword(c, keyword_hit(c["text"], c["lang"])) for c in CASES)
    L += ["# 確認質問のモデル比較", "",
          f"同じ {len(CASES)} 文（eval/cases.py）を各モデルで {a.runs} 回。hedged request なし。単語リストは {kw} / {len(CASES)}。", "",
          "| モデル | 正解（回ごと） | 失敗した呼び出し | 速さ 中央値 | 95% | 最大 | 入力トークン | 出力トークン | 思考トークン |",
          "|---|---|---|---|---|---|---|---|---|"]
    for model, rows in allrows.items():
        per_run = [sum(x["ok"] for x in rows if x["run"] == r) for r in range(1, a.runs + 1)]
        secs = sorted(x["result"]["sec"] for x in rows if "error" not in x["result"])
        errs = sum("error" in x["result"] for x in rows)
        q = statistics.quantiles(secs, n=20) if len(secs) >= 2 else [0] * 19
        speed = (f"{statistics.median(secs):.1f} 秒 | {q[18]:.1f} 秒 | {max(secs):.1f} 秒" if secs else "- | - | -")
        L.append(f"| {model} | {' / '.join(map(str, per_run))} | {errs} | {speed} | "
                 f"{avg(x['result'].get('in_tokens') for x in rows)} | {avg(x['result'].get('out_tokens') for x in rows)} | "
                 f"{avg(x['result'].get('thoughts') for x in rows)} |")
    L += ["", "トークン数は1回あたりの平均。料金は Vertex AI の料金ページの単価を掛けて出す。", "",
          "## モデルごとに結果が違った文", "", "| 文 | 正解 | " + " | ".join(allrows) + " |",
          "|---|---|" + "---|" * len(allrows)]
    for c in CASES:
        cells = []
        for rows in allrows.values():
            mine = [x for x in rows if x["text"] == c["text"]]
            cells.append(f"{sum(x['ok'] for x in mine)}/{len(mine)} " +
                         ", ".join(sorted({str(x['result'].get('target') or x['result'].get('error', '—'))[:30] for x in mine})))
        if len({cell.split(' ')[0] for cell in cells}) > 1:
            L.append(f"| {c['text']} | {' / '.join(c['targets']) or '聞かない'} | " + " | ".join(cells) + " |")
    (OUT / "models.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:12]))


if __name__ == "__main__":
    main()
