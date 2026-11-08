"""「なぜ Gemini か」の評価セット。ask=True は確認質問が要る発言、targets はその対象として正しい語（どれか1つ）。

ラベルは「医師の判断が変わりうる曖昧さか」で付けた（prompts/clarify_system.txt と同じ基準）。
程度の語（"a little" など）は今の方針で対象外（docs/requirements.md の TBD 1）。

group の意味:
  keyword   単語リストに載っている曖昧な語（どちらの方式でも拾えるはず）
  paraphrase 単語リストに載っていない言い回しの曖昧さ（言い換え・口語）
  meaning   意味が医学的に複数ある語（dizzy など）
  context   単語リストの語が入っているが、文脈上聞かなくてよい（聞くと患者の負担になる）
  clear     はっきりしている発言・否定・本人が言った不確かさ
"""

CASES = [
    # ---------- English ----------
    dict(lang="en", group="keyword", ask=True, targets=["for a while"], text="I've had a headache for a while."),
    dict(lang="en", group="keyword", ask=True, targets=["sometimes"], text="My stomach hurts sometimes after I eat."),
    dict(lang="en", group="paraphrase", ask=True, targets=["on and off"], text="My chest has been hurting on and off."),
    dict(lang="en", group="paraphrase", ask=True, targets=["comes and goes"], text="The pain in my back comes and goes."),
    dict(lang="en", group="paraphrase", ask=True, targets=["ages", "for ages"], text="I've been coughing for ages."),
    dict(lang="en", group="paraphrase", ask=True, targets=["every now and then"], text="Every now and then my hands go numb."),
    dict(lang="en", group="meaning", ask=True, targets=["dizzy"], text="I feel dizzy when I stand up."),
    dict(lang="en", group="meaning", ask=True, targets=["light-headed", "lightheaded"], text="I get light-headed in the morning."),
    dict(lang="en", group="context", ask=False, targets=[], text="I waited at reception for a while. My throat has hurt since yesterday."),
    dict(lang="en", group="context", ask=False, targets=[], text="I recently moved to Japan. The rash started yesterday morning."),
    dict(lang="en", group="context", ask=False, targets=[], text="Sometimes my daughter comes with me to translate."),
    dict(lang="en", group="clear", ask=False, targets=[], text="I had a fever of 38.5 two days ago, but it's gone now."),
    dict(lang="en", group="clear", ask=False, targets=[], text="I'm allergic to penicillin."),
    dict(lang="en", group="clear", ask=False, targets=[], text="I'm not sure when it started."),
    # ---------- 简体中文 ----------
    dict(lang="zh-CN", group="keyword", ask=True, targets=["好几天", "几天"], text="我头疼好几天了。"),
    dict(lang="zh-CN", group="keyword", ask=True, targets=["有时候"], text="我有时候吃完饭胃疼。"),
    dict(lang="zh-CN", group="paraphrase", ask=True, targets=["断断续续"], text="我胸口断断续续地疼。"),
    dict(lang="zh-CN", group="paraphrase", ask=True, targets=["时不时"], text="我时不时手发麻。"),
    dict(lang="zh-CN", group="paraphrase", ask=True, targets=["前阵子", "前阵子开始"], text="前阵子开始一直咳嗽。"),
    dict(lang="zh-CN", group="meaning", ask=True, targets=["头晕"], text="我一站起来就头晕。"),
    dict(lang="zh-CN", group="meaning", ask=True, targets=["心里难受", "难受"], text="我心里难受。"),
    dict(lang="zh-CN", group="context", ask=False, targets=[], text="我在前台等了一段时间。从昨天开始喉咙疼。"),
    dict(lang="zh-CN", group="context", ask=False, targets=[], text="我最近刚来日本。昨天早上开始起疹子。"),
    dict(lang="zh-CN", group="context", ask=False, targets=[], text="有时候我女儿陪我来翻译。"),
    dict(lang="zh-CN", group="clear", ask=False, targets=[], text="两天前发烧38.5度，现在退了。"),
    dict(lang="zh-CN", group="clear", ask=False, targets=[], text="我对青霉素过敏。"),
    dict(lang="zh-CN", group="clear", ask=False, targets=[], text="我没有发烧。"),
    # ---------- Tiếng Việt ----------
    dict(lang="vi", group="keyword", ask=True, targets=["một thời gian"], text="Tôi bị đau đầu một thời gian rồi."),
    dict(lang="vi", group="keyword", ask=True, targets=["thỉnh thoảng"], text="Thỉnh thoảng tôi bị đau bụng sau khi ăn."),
    dict(lang="vi", group="paraphrase", ask=True, targets=["lúc có lúc không"], text="Ngực tôi đau lúc có lúc không."),
    dict(lang="vi", group="paraphrase", ask=True, targets=["mấy bữa nay", "mấy bữa"], text="Mấy bữa nay tôi ho nhiều."),
    dict(lang="vi", group="paraphrase", ask=True, targets=["hay", "hay bị"], text="Tay tôi hay bị tê."),
    dict(lang="vi", group="meaning", ask=True, targets=["chóng mặt"], text="Tôi bị chóng mặt khi đứng dậy."),
    dict(lang="vi", group="meaning", ask=True, targets=["khó chịu trong người", "khó chịu"], text="Tôi thấy khó chịu trong người."),
    dict(lang="vi", group="context", ask=False, targets=[], text="Tôi đã đợi ở quầy lễ tân một thời gian. Tôi bị đau họng từ hôm qua."),
    dict(lang="vi", group="context", ask=False, targets=[], text="Gần đây tôi mới sang Nhật. Tôi bị nổi mẩn từ sáng hôm qua."),
    dict(lang="vi", group="context", ask=False, targets=[], text="Thỉnh thoảng con gái tôi đi cùng để phiên dịch."),
    dict(lang="vi", group="clear", ask=False, targets=[], text="Hai ngày trước tôi sốt 38,5 độ, bây giờ hết rồi."),
    dict(lang="vi", group="clear", ask=False, targets=[], text="Tôi bị dị ứng với penicillin."),
    dict(lang="vi", group="clear", ask=False, targets=[], text="Tôi không bị sốt."),
]

# 比べる相手: 曖昧な語の単語リスト（prompts/clarify_system.txt の例と、その素直な訳・よくある言い方）
KEYWORDS = {
    "en": ["for a while", "a while", "recently", "sometimes", "a few days", "few days", "lately",
           "a little while ago", "dizzy", "occasionally", "often"],
    "zh-CN": ["一段时间", "一会儿", "最近", "有时候", "有时", "几天", "好几天", "不久前", "头晕", "经常"],
    "vi": ["một thời gian", "gần đây", "dạo này", "thỉnh thoảng", "vài ngày", "mấy ngày", "chóng mặt", "thường"],
}

DEMO_TEXT = "I've had a headache for a while."
