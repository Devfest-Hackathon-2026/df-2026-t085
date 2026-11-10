"""本物の Gemini で、相づち（嗯・うん・Mm）だけの返事を、安全に関わる事実（アレルギー・妊娠・服薬）への「はい」と
決めつけずに本人に確かめるか、はっきりした答え・指示への了解では聞かないかを測る（Source Agent の意図の判定）。

2026-11-10 の比較評価で、薬のアレルギーの質問への「嗯。」を「はい、あります」と訳して、確かめずに届けた（1/8 の見逃し）。

  UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> python eval/live_backchannel_check.py [回数]
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ussa  # noqa: E402
import unsaid_ai as u  # noqa: E402


def ctx(original, translation):
    return [{"speaker": "doctor", "original": original, "translation": translation}]


ALLERGY = ctx("薬のアレルギーはありますか？", "你对药物过敏吗？")
# (id, 発言, 言語, 聞き手, 文脈, 聞くべきか)
CASES = [
    ("zh-hmm-allergy", "嗯。", "zh-CN", "ja", ALLERGY, True),
    ("ja-un-pregnancy", "うん。", "ja", "en", [{"speaker": "doctor", "original": "Is there any chance you could be pregnant?",
                                              "translation": "妊娠している可能性はありますか？"}], True),
    ("en-mm-thinner", "Mm.", "en", "ja", [{"speaker": "doctor", "original": "血液をサラサラにする薬を飲んでいますか？",
                                           "translation": "Are you taking any blood thinners?"}], True),
    ("zh-yes-penicillin", "是的，我对青霉素过敏。", "zh-CN", "ja", ALLERGY, False),
    ("zh-no-allergy", "没有。", "zh-CN", "ja", ALLERGY, False),
    ("zh-ack-instruction", "嗯，明白了。", "zh-CN", "ja", ctx("この薬は食後に飲んでください。", "这个药请饭后吃。"), False),
    # ↓ プロンプトの例に無い言い方（例に合わせただけになっていないかを見る）
    ("vi-u-allergy", "Ừm.", "vi", "ja", ctx("薬のアレルギーはありますか？", "Bạn có bị dị ứng thuốc không?"), True),
    ("zh-enen-consent", "嗯嗯。", "zh-CN", "ja", ctx("これから胃カメラの検査をしてもよろしいですか？", "现在可以给你做胃镜检查吗？"), True),
    ("zh-yes-amoxicillin", "对，阿莫西林过敏，吃了会起疹子。", "zh-CN", "ja", ALLERGY, False),
    ("en-no-meds", "No, I'm not taking anything.", "en", "ja", [{"speaker": "doctor", "original": "今飲んでいる薬はありますか？",
                                                               "translation": "Are you taking any medicine now?"}], False),
    ("zh-ok-wait", "好的，我在这里等。", "zh-CN", "ja", ctx("検査の結果が出るまで待ってください。", "请等到检查结果出来。"), False),
]


def one(c):
    cid, text, lang, listener, turns, _ = c
    raw, _s = u.source_agent(text, "patient", lang, listener, turns)
    it = ussa.build_intent(raw, source_text=text, speaker_lang=lang, listener_lang=listener)
    return cid, it["state"] not in ussa.INTENT_OK


def main_():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(one, [c for c in CASES for _ in range(n)]))
    miss = fa = 0
    for cid, *_, want in CASES:
        asked = sum(a for k, a in res if k == cid)
        print(f"{cid}: 聞いた {asked}/{n}（期待: {'聞く' if want else '聞かない'}）")
        miss += (n - asked) if want else 0
        fa += asked if not want else 0
    print(f"聞くべきなのに聞かなかった {miss}、聞かなくてよいのに聞いた {fa}")


if __name__ == "__main__":
    main_()
