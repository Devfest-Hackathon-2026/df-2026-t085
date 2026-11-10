"""本物の Gemini で、Meaning Engine が医学的に曖昧な語（头晕）を確認候補にするか、決まっている語・否定では出さないかを測る。

2026-11-10: 「眼前发黑，差点晕倒」を伴う头晕は、伴う症状を「決まった」とみなして候補にしないことが多かった（1/5）。
「感覚そのものの説明だけが種類を決める。伴う症状は手がかり」をプロンプトに足した後 5/5。対照（天旋地转・没有发烧）は 0/5 のまま。

  UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> python eval/live_meaning_check.py [回数]
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clinical  # noqa: E402
import unsaid_ai as u  # noqa: E402

CASES = {
    "dizzy+dark": ("医生，我是来日本旅游的，昨天刚到。今天在车站突然觉得头晕，眼前发黑，差点晕倒。坐下来休息了一会儿好一些，"
                   "但现在还是有点不舒服，我有点害怕。", "头晕", True),
    "dizzy-spinning": ("我头晕，天旋地转的，周围的东西都在转。", "头晕", False),
    "nofever": ("我没有发烧，也不咳嗽。", "", False),
}


def one(k):
    t, _, _ = CASES[k]
    raw, _s = u.meaning(t, "zh-CN", "ja", [], [], None)
    m = clinical.build_meaning(raw, utterance_id="e", source_text=t, source_lang="zh-CN", schema=u.schema("meaning"))
    return k, [c["source_quote"] for c in m["candidates"]]


def main_():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(one, [k for k in CASES for _ in range(n)]))
    for k, (_, q, want) in CASES.items():
        got = [c for kk, c in res if kk == k]
        hit = sum(any(q and q in x for x in c) for c in got)
        print(f"{k}: '{q}' の候補 {hit}/{len(got)}（期待: {'すべて' if want else 'なし'}）、候補あり {sum(bool(c) for c in got)}/{len(got)} {got}")


if __name__ == "__main__":
    main_()
