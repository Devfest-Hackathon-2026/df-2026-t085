"""本物の Gemini で、Verification Engine の誤警報（正しい訳を止める）と見逃し（誤りを入れた訳を通す）を、回数を重ねて測る。
1 回の比較評価では LLM のゆれで 0/20〜4/20 と揺れるので、同じ 20 件（eval/ussa_cases.py の PRECISION）を N 回照合する。

  UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> python eval/live_verify_repeat.py [回数]
"""
import collections
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
import unsaid_ai as u  # noqa: E402
import ussa  # noqa: E402
from eval.ussa_cases import PRECISION  # noqa: E402

PASS = (ussa.VERIFIED, ussa.AMBIGUOUS)


def one(c):
    raw, _ = u.source_agent(c["text"], c["speaker"], c["lang"], c["listener"], [])
    it = ussa.build_intent(raw, source_text=c["text"], speaker_lang=c["lang"], listener_lang=c["listener"])
    t0 = time.time()
    good = main._verify(c["text"], c["lang"], c["reference"], c["listener"], c["speaker"], it, None, {}, "g")
    ms = round((time.time() - t0) * 1000)
    bad = main._verify(c["text"], c["lang"], c["corrupted"], c["listener"], c["speaker"], it, None, {}, "b")
    strong = [f"{i['layer']}:{i['type']}" for i in good["issues"] if i["strength"] == "strong"]
    return c["id"], good["status"], bad["status"], strong, ms


def main_():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(one, [c for c in PRECISION for _ in range(n)]))
    fa = [r for r in res if r[1] not in PASS]
    miss = [r for r in res if r[2] in PASS]
    print(f"誤警報（正しい訳を止めた）: {len(fa)}/{len(res)}  見逃し（誤りを入れた訳を通した）: {len(miss)}/{len(res)}")
    print("誤警報の理由:", collections.Counter(s for r in fa for s in r[3]).most_common())
    print("誤警報の症例:", collections.Counter(r[0] for r in fa).most_common())
    print("見逃しの症例:", collections.Counter(r[0] for r in miss).most_common())
    out = ROOT / "eval" / "live_verify_repeat.json"
    out.write_text(json.dumps({"model": u.MODEL, "runs": n, "results": res}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main_()
