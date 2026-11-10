# 本物の Gemini での基本動作の確認

- 実行: 2026-11-10 05:33 JST、モデル gemini-3.8-flash、Vertex AI の場所 global（評価用のプロジェクト）
- サーバーの API を、Firestore だけ偽物にして通した。発言はすべて架空。1 回の実行（LLM の出力は回ごとに変わりうる）

| 確認 | 期待 | 結果 | 実際 | 時間 |
|---|---|---|---|---|
| 医師→患者の翻訳 | 検証済みで届く・数（2回・1錠）と時間帯（朝・夜）が残る | OK | sent VERIFIED 「这个药请每天吃两次，早晚各吃一片。」 | 14.5 秒 |
| 患者→医師の翻訳 | 検証済みで届く・38.5度・咳なし（否定）・昨夜から | OK | sent VERIFIED 「昨日の夜から熱が出始めて、38度5分あり、咳はありません。」 | 9.9 秒 |
| 「大丈夫です」: 文脈で決まる（併用してよいか → 大丈夫） | 聞かずに「一緒に飲んでよい」の意味で訳す（RESOLVED） | OK | RESOLVED sent 「没问题。」 | 13.9 秒 |
| 「大丈夫です」: 文脈なし（意味が決まらない） | 訳を送らず、医師本人に意味を確認する（CLARIFICATION_REQUIRED） | OK | CLARIFICATION_REQUIRED awaiting_intent 候補 ['問題ありません・心配いりません', '結構です（必要ありません）', '構いません（了解・同意します）'] | 14.3 秒 |
| 意図確認と翻訳への反映（没事） | 本人に聞き、選んだ意味（断る）で訳して届く（USER_CONFIRMED） | OK | 選んだ「不需要打止痛针」→ 訳「大丈夫です（注射は結構です）。」VERIFIED | 4.6 秒 |
| Semantic Bridge の意味の枠 | 禁止（must_not）・時刻 08:00・明日（day+1）を取り出す | OK | modality=must_not polarity=mixed times=[{'kind': 'relative_day', 'value': 'day+1'}, {'kind': 'part_of_day', 'value': 'morning'}, {'kind': 'clock', 'value': '08:00'}] | 17.6 秒 |
| Verification Engine: 1日3回に変えた訳 | MISMATCH にする | OK | MISMATCH [('quantity_change', 'strong'), ('addition', 'strong'), ('frequency_change', 'strong'), ('frequency_change', 'strong')] | 4.1 秒 |
| Verification Engine: 否定を足した訳 | MISMATCH にする | OK | MISMATCH [('polarity_flip', 'medium'), ('polarity_flip', 'strong'), ('frequency_change', 'strong'), ('polarity_flip', 'strong')] | 3.2 秒 |
| Verification Engine: 夜を落とした訳 | MISMATCH にする | OK | MISMATCH [('time_change', 'strong'), ('omission', 'strong')] | 13.4 秒 |
| Verification Engine: 正しい訳 | 正しい訳は通す | OK | VERIFIED [] | 4.1 秒 |
| Safety Gate: 数が落ちた訳は自動で送らない | needs_review（発言者の確認）・訳は届かない | OK | needs_review MISMATCH [('quantity_change', 'strong', '原文の数「8」が訳文に無い'), ('time_change', 'strong', '期間が違う（原文 8時間 / 訳文 なし）'), ('time_change', 'strong', '「8時間」という絶食時間の指定が訳から抜け落ちています。')] | 12.3 秒 |
| Conversation Auditor: 理解の相違（朝に2錠） | 理解の相違を未解決事項に 1 件出す（根拠の発言つき） | OK | [('instruction_misunderstanding', '説明と患者の理解が違う可能性: 量: 説明 1錠 / 理解 2錠／時間帯: 説明は 朝・夕・夜、理解は 朝 だけ')] | 15.4 秒 |
| Conversation Auditor: 正しい理解に警告を出さない | 理解の相違を出さない | OK | [] | 13.8 秒 |
| Teach-back: 食い違い | inconsistent（認識の相違） | OK | inconsistent ['明日また来ればよいのかと理解している'] | 14.3 秒 |
| Teach-back: 正しい理解 | consistent | OK | consistent ['今日は家に帰らずに病院で待つ', '検査の結果が出るまで待つ'] | 14.9 秒 |

15 / 15 が期待どおり。
