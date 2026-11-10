"""通常の翻訳（A）と Kumu（B）を比べるための模擬症例（すべて架空）。EVALUATION.md の「比較評価」。

正解の付け方（このファイルを書いた人が、評価の前に決めたもの）:
  key_facts   医師に届くべき重要な情報。各項目は「医師の言語（日本語）でこのどれかの語が含まれていれば保持」とみなす語のリスト
  ambiguities 医師が確認すべき曖昧な語（原文の語。どれか1つに当たれば検出とみなす）。空なら「聞かなくてよい」発言
  forbidden   原文に無いのに出てはいけない語（病名・原因・検査結果・言っていない程度など）
  answer      確認質問への患者の自由回答（患者の言語）と、解決後の意味に含まれるべき語
teach-back の症例: explanation（医師・日本語）と restatement（患者の言い直し）、expected は consistent / inconsistent。

語の一致で数える近似なので、言い換えを取りこぼすことがある（EVALUATION.md「限界」）。
"""

UTTERANCES = [
    # ── 中国語 ──
    dict(id="zh-dizzy", lang="zh-CN",
         text="医生，我是来日本旅游的，昨天刚到。今天在车站突然觉得头晕，眼前发黑，差点晕倒。坐下来休息了一会儿好一些，但现在还是有点不舒服，我有点害怕。",
         key_facts=[["旅行"], ["昨日"], ["今日"], ["駅"], ["突然", "急に"], ["めまい", "眩暈", "ふらつ", "くらくら"],
                    ["暗", "目の前", "眼前"], ["倒れ", "失神", "気を失"], ["休", "座"], ["不快", "気分", "調子", "まだ", "続"],
                    ["怖", "不安"]],
         ambiguities=["头晕"],
         forbidden=["貧血", "脳", "不整脈", "低血糖", "失神した", "意識消失", "起立性"],
         answer=dict(text="就是天旋地转的那种，周围的东西都在转。", expect=["回転", "回る", "ぐるぐる"])),
    dict(id="zh-headache-days", lang="zh-CN", text="我头疼好几天了，吃了止疼药也没用。",
         key_facts=[["頭痛", "頭が痛"], ["数日", "何日", "日"], ["鎮痛", "痛み止め"], ["効", "変わらない", "良くならない"]],
         ambiguities=["好几天"], forbidden=["片頭痛", "偏頭痛", "緊張型", "ロキソニン", "カロナール"],
         answer=dict(text="大概五天。", expect=["5日", "五日"])),
    dict(id="zh-chest-sometimes", lang="zh-CN", text="我有时候胸口疼，特别是爬楼梯的时候。",
         key_facts=[["胸"], ["痛"], ["階段", "上る", "登る"], ["時々", "ときどき", "時折"]],
         ambiguities=["有时候"], forbidden=["狭心症", "心筋梗塞", "心臓病"],
         answer=dict(text="一个星期两三次。", expect=["週", "2", "3", "二", "三"])),
    dict(id="zh-no-fever", lang="zh-CN", text="我没有发烧，也不咳嗽。",
         key_facts=[["熱", "発熱"], ["咳"]], ambiguities=[], forbidden=["風邪", "感染"]),
    dict(id="zh-medicine-unsure", lang="zh-CN", text="我在吃降压药，但是今天早上好像忘了吃。",
         key_facts=[["降圧", "血圧"], ["今朝", "朝"], ["忘", "飲み忘"]], ambiguities=[],
         forbidden=["アムロジピン", "高血圧症の診断", "副作用"]),
    # ── 英語 ──
    dict(id="en-dizzy-standing", lang="en", text="I feel dizzy when I stand up, and I almost fell this morning.",
         key_facts=[["めまい", "ふらつ", "くらくら", "目が回"], ["立ち上が", "立つ"], ["今朝", "朝"], ["転び", "倒れ"]],
         ambiguities=["dizzy"], forbidden=["起立性低血圧", "貧血", "転倒した", "骨折"],
         answer=dict(text="Like I was going to faint, everything went dark.", expect=["失神", "気が遠", "暗", "気を失"])),
    dict(id="en-headache-while", lang="en", text="I've had a headache for a while.",
         key_facts=[["頭痛", "頭が痛"], ["しばらく", "前から", "以前から"]],
         ambiguities=["for a while"], forbidden=["片頭痛", "慢性", "数週間", "1週間"],
         answer=dict(text="About two weeks.", expect=["2週間", "二週間", "2 週間"])),
    dict(id="en-allergy", lang="en", text="I'm allergic to penicillin. I got a rash last time.",
         key_facts=[["ペニシリン"], ["アレルギー"], ["発疹", "湿疹", "ぶつぶつ", "皮疹"]], ambiguities=[],
         forbidden=["アナフィラキシー", "蕁麻疹"]),
    dict(id="en-reception", lang="en", text="I waited at reception for a while. My throat has hurt since yesterday.",
         key_facts=[["喉", "のど"], ["昨日"]], ambiguities=[], forbidden=["扁桃", "咽頭炎", "風邪"]),
    # ── ベトナム語 ──
    dict(id="vi-dizzy", lang="vi", text="Tôi bị chóng mặt khi đứng dậy.",
         key_facts=[["めまい", "ふらつ", "くらくら", "目が回"], ["立ち上が", "立つ"]],
         ambiguities=["chóng mặt"], forbidden=["起立性低血圧", "貧血"],
         answer=dict(text="Mọi thứ xung quanh quay vòng.", expect=["回", "ぐるぐる"])),
    dict(id="vi-stomach-days", lang="vi", text="Mấy bữa nay tôi bị đau bụng sau khi ăn.",
         key_facts=[["腹", "おなか", "お腹"], ["痛"], ["食後", "食べた後", "食事の後"], ["数日", "ここ", "最近", "この"]],
         ambiguities=["Mấy bữa nay", "mấy bữa"], forbidden=["胃炎", "胃潰瘍", "食中毒"],
         answer=dict(text="Khoảng ba ngày.", expect=["3日", "三日"])),
]

TEACHBACKS = [
    dict(id="zh-wait-today-wrong", lang="zh-CN", explanation="今日は検査の結果が出るまで、帰らずに待ってください。",
         restatement="明天再来就可以了，是吗？", expected="inconsistent"),
    dict(id="zh-wait-today-right", lang="zh-CN", explanation="今日は検査の結果が出るまで、帰らずに待ってください。",
         restatement="今天不回家，在医院等检查结果出来。", expected="consistent"),
    dict(id="zh-medicine-right", lang="zh-CN", explanation="この薬は1日3回、食後に飲んでください。",
         restatement="一天三次，吃完饭以后吃。", expected="consistent"),
    dict(id="en-dose-wrong", lang="en", explanation="この薬は1回1錠、1日2回、朝と夕の食後に5日間飲んでください。",
         restatement="So I take two tablets once a day?", expected="inconsistent"),
    dict(id="en-dose-right", lang="en", explanation="この薬は1回1錠、1日2回、朝と夕の食後に5日間飲んでください。",
         restatement="One pill after breakfast and one after dinner, for five days.", expected="consistent"),
    dict(id="en-test-wait-right", lang="en", explanation="血液検査の結果は30分ほどで出ます。それまで待合室でお待ちください。",
         restatement="I should wait in the waiting room for about thirty minutes until the blood test results come out.",
         expected="consistent"),
    dict(id="vi-revisit-wrong", lang="vi", explanation="来週の月曜日にもう一度来てください。",
         restatement="Tôi quay lại vào thứ Hai tuần này.", expected="inconsistent"),
    dict(id="vi-revisit-right", lang="vi", explanation="来週の月曜日にもう一度来てください。",
         restatement="Thứ Hai tuần sau tôi quay lại khám.", expected="consistent"),
]
