# 確認質問の評価: Gemini vs 単語リスト

## まとめ（11/8、デプロイ済みの API で計測）

- **3回流して Gemini は 34 / 34 / 37 問正解（40問中）。単語リストは毎回 18。** 文と正解は `eval/cases.py`、正解ラベルは私たちが付けたもの（医師の確認はまだ）。
- 単語リストが負けるのは2種類。**言い回しの曖昧さ**（"on and off"、"for ages"、时不时、mấy bữa nay など、リストに無い言い方）は全部取りこぼす。**文脈で聞かなくてよい語**（"I waited at reception for a while"、"Sometimes my daughter comes with me"）にも全部反応して、患者に余計な質問をする。
- Gemini が3回とも取りこぼしたもの: "comes and goes"、"light-headed"、"khó chịu trong người"（体がだるい・気分が悪い）。断断续续 は3回中1回だけ聞いた。
- 「最近日本に来た」は、Gemini が来日の時期を聞くことがある（3言語で計3回）。ここは正解を「聞かない」にしたが、来日時期は感染症では大事なので、医師の先生に聞いて決めたい。
- デモの1文 "I've had a headache for a while." は **20回中20回**、"for a while" について確認質問が出た。

## 患者が実際に待つ時間（11/9、本番、hedged request あり）

発言を作ってから、確認質問か「医師にはこう伝わります」が画面に出るまで。確認質問が出る文と出ない文を交互に60回（`python eval/run.py ... --latency 60`、生データは `eval/latency.json`）。

- **中央値 3.7 秒、90% で 5.0 秒、95% で 6.2 秒、最大 7.4 秒。8 秒を超えた回は0。**
- 上の「/analyze の応答時間」（95% で約16秒）は医師用の facts まで待った時間で、患者の待ち時間ではない。

下の表は1回目の結果。

40 文（英・中・越）。正解 = 聞くべき発言で正しい語について聞く／聞かなくてよい発言で聞かない。

| 種類 | 文の数 | Gemini | 単語リスト |
|---|---|---|---|
| 単語リストにある曖昧な語 | 6 | 6 | 6 |
| 言い回しの曖昧さ（リストに無い） | 10 | 8 | 0 |
| 意味が複数ある語 | 6 | 4 | 3 |
| リストの語があるが聞かなくてよい | 9 | 7 | 0 |
| はっきりした発言 | 9 | 9 | 9 |
| **合計** | 40 | **34** | **18** |
| （en） | 14 | 11 | 6 |
| （zh-CN） | 13 | 11 | 6 |
| （vi） | 13 | 12 | 6 |

/analyze の応答時間（facts まで含む）: 中央値 4.0 秒、95% 15.5 秒、最大 24.6 秒

## デモの1文: "I've had a headache for a while."

20 回中 20 回、"for a while" について確認質問が出た。

出た質問: About how long have you had the headache? / How long have you had the headache?

## 1文ずつ

| 言語 | 種類 | 発言 | 正解 | Gemini の対象 | Gemini の質問 | 単語リスト | G | K |
|---|---|---|---|---|---|---|---|---|
| en | keyword | I've had a headache for a while. | for a while | for a while | About how long have you had the headache? | for a while, a while | ○ | ○ |
| en | keyword | My stomach hurts sometimes after I eat. | sometimes | sometimes | About how often does the pain happen after you eat? | sometimes | ○ | ○ |
| en | paraphrase | My chest has been hurting on and off. | on and off | on and off | How often does the pain come and go? | — | ○ | × |
| en | paraphrase | The pain in my back comes and goes. | comes and goes | — |  | — | × | × |
| en | paraphrase | I've been coughing for ages. | ages / for ages | for ages | How long have you been coughing? | — | ○ | × |
| en | paraphrase | Every now and then my hands go numb. | every now and then | Every now and then | About how often does this happen? | — | ○ | × |
| en | meaning | I feel dizzy when I stand up. | dizzy | dizzy | What kind of dizzy do you feel? | dizzy | ○ | ○ |
| en | meaning | I get light-headed in the morning. | light-headed / lightheaded | — |  | — | × | × |
| en | context | I waited at reception for a while. My throat has hurt since yesterday. | 聞かない | — |  | for a while, a while | ○ | × |
| en | context | I recently moved to Japan. The rash started yesterday morning. | 聞かない | recently | About when did you move to Japan? | recently | × | × |
| en | context | Sometimes my daughter comes with me to translate. | 聞かない | — |  | sometimes | ○ | × |
| en | clear | I had a fever of 38.5 two days ago, but it's gone now. | 聞かない | — |  | — | ○ | ○ |
| en | clear | I'm allergic to penicillin. | 聞かない | — |  | — | ○ | ○ |
| en | clear | I'm not sure when it started. | 聞かない | — |  | — | ○ | ○ |
| zh-CN | keyword | 我头疼好几天了。 | 好几天 / 几天 | 好几天 | 请问大概持续了几天？ | 几天, 好几天 | ○ | ○ |
| zh-CN | keyword | 我有时候吃完饭胃疼。 | 有时候 | 有时候 | 您说的“有时候”，大概是怎样的频率？ | 有时候, 有时 | ○ | ○ |
| zh-CN | paraphrase | 我胸口断断续续地疼。 | 断断续续 | — |  | — | × | × |
| zh-CN | paraphrase | 我时不时手发麻。 | 时不时 | 时不时 | 大概多久发作一次？ | — | ○ | × |
| zh-CN | paraphrase | 前阵子开始一直咳嗽。 | 前阵子 / 前阵子开始 | 前阵子 | “前阵子”大概是多久以前？ | — | ○ | × |
| zh-CN | meaning | 我一站起来就头晕。 | 头晕 | 头晕 | 请问您具体是哪种感觉？ | 头晕 | ○ | ○ |
| zh-CN | meaning | 我心里难受。 | 心里难受 / 难受 | 心里难受 | 请问这里的“心里难受”具体是指哪种感觉？ | — | ○ | × |
| zh-CN | context | 我在前台等了一段时间。从昨天开始喉咙疼。 | 聞かない | — |  | 一段时间 | ○ | × |
| zh-CN | context | 我最近刚来日本。昨天早上开始起疹子。 | 聞かない | 最近 | 您大概是什么时候来日本的？ | 最近 | × | × |
| zh-CN | context | 有时候我女儿陪我来翻译。 | 聞かない | — |  | 有时候, 有时 | ○ | × |
| zh-CN | clear | 两天前发烧38.5度，现在退了。 | 聞かない | — |  | — | ○ | ○ |
| zh-CN | clear | 我对青霉素过敏。 | 聞かない | — |  | — | ○ | ○ |
| zh-CN | clear | 我没有发烧。 | 聞かない | — |  | — | ○ | ○ |
| vi | keyword | Tôi bị đau đầu một thời gian rồi. | một thời gian | một thời gian | Bạn bị đau đầu khoảng bao lâu rồi? | một thời gian | ○ | ○ |
| vi | keyword | Thỉnh thoảng tôi bị đau bụng sau khi ăn. | thỉnh thoảng | Thỉnh thoảng | "Thỉnh thoảng" có nghĩa là khoảng bao lâu một lần? | thỉnh thoảng | ○ | ○ |
| vi | paraphrase | Ngực tôi đau lúc có lúc không. | lúc có lúc không | lúc có lúc không | Cơn đau thường xuất hiện với mức độ thường xuyên như thế nào? | — | ○ | × |
| vi | paraphrase | Mấy bữa nay tôi ho nhiều. | mấy bữa nay / mấy bữa | Mấy bữa nay | "Mấy bữa nay" cụ thể là khoảng bao lâu rồi? | — | ○ | × |
| vi | paraphrase | Tay tôi hay bị tê. | hay / hay bị | hay | Bạn bị tê tay thường xuyên như thế nào? | — | ○ | × |
| vi | meaning | Tôi bị chóng mặt khi đứng dậy. | chóng mặt | chóng mặt | Bạn cảm thấy chóng mặt như thế nào? | chóng mặt | ○ | ○ |
| vi | meaning | Tôi thấy khó chịu trong người. | khó chịu trong người / khó chịu | — |  | — | × | × |
| vi | context | Tôi đã đợi ở quầy lễ tân một thời gian. Tôi bị đau họng từ hôm qua. | 聞かない | — |  | một thời gian | ○ | × |
| vi | context | Gần đây tôi mới sang Nhật. Tôi bị nổi mẩn từ sáng hôm qua. | 聞かない | — |  | gần đây | ○ | × |
| vi | context | Thỉnh thoảng con gái tôi đi cùng để phiên dịch. | 聞かない | — |  | thỉnh thoảng | ○ | × |
| vi | clear | Hai ngày trước tôi sốt 38,5 độ, bây giờ hết rồi. | 聞かない | — |  | — | ○ | ○ |
| vi | clear | Tôi bị dị ứng với penicillin. | 聞かない | — |  | — | ○ | ○ |
| vi | clear | Tôi không bị sốt. | 聞かない | — |  | — | ○ | ○ |
