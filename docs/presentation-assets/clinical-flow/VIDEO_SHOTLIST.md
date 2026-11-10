# デモ動画のショットリスト（編集用）

- 画面: 1920×1080（16:9）。左に医師の MacBook（2560×1664 を縮小）、すぐ右に患者の iPhone（1206×2622 を縮小）。配置は最後まで固定
- マスク: 操作している側の相手の画面に黒 50%。相手に届いた瞬間（「両方」の行）はマスクなし。参加前の患者側は黒 100%
- 切り替え: カット、または短いフェード。無音（発表者が説明する）
- 長さ: 本編 2:46、補足デモ 0:27、合計 3:13
- 画像: すべて `docs/presentation-assets/clinical-flow/` からの相対パス。本物の Gemini で撮った 1 つの問診（補足デモは別の問診、誤訳は人工的に注入）
- 送信前の確認ダイアログは Kumu に無いので使っていない。「入力（未送信）→ 送信の操作 → 確認中 → 届いた」の順で見せる

## 左右の整合について（撮影していない状態）

撮影は「状態が変わった側」を撮ったので、次の時間は、片方の画面の正確な状態の画像が無い。別の時点の画像で補わず、
最後に撮った画像をマスクで暗くしたままにする（行の「注意」にも書いた）。

| 時間帯 | 画面 | 実際の状態 | 使う画像 |
|---|---|---|---|
| 0:00〜0:05 | 患者 | まだ QR を読んでいない | `02-patient-join-language.png` を黒 100% |
| 1:16〜1:31 | 患者 | 2 つ目の答えを送った後（你的回答・医生已读）〜 医師の説明が届く前 | `25-patient-answer2-checking.png` を暗く（この間に「両方を明るく」はしない） |
| 1:36〜1:44 | 医師 | 「理解を確認」を押した後（依頼の吹き出し） | `31-doctor-explain-sent.png` のまま（吹き出しは `36` から） |
| 1:46〜2:04 | 患者 | 誤った言い直しを送った後 〜 再説明が届く前 | `35-patient-teachback-wrong-checking.png` を暗く |
| 2:09〜2:17 | 医師 | 2 回目の「理解を確認」を押した後 | `41-doctor-reexplain-sent.png` のまま（吹き出しは `46` から） |

ほかの行は、左右が同じ時点の状態（同時に撮った画像、またはその間に画面が変わらない画像）。

## ショットリスト

| 開始時刻 | 終了時刻 | 医師側PNGの正確なファイル名 | 患者側PNGの正確なファイル名 | 左右どちらを変更するか | どちらにマスクをかけるか | 画面上の操作・アニメーション | テロップ | 発表者の説明原稿 |
|---|---|---|---|---|---|---|---|---|
| 0:00 | 0:02 | `00-doctor-start.png` | `02-patient-join-language.png` | 両方 | 患者側（黒100%: まだ開いていない） | 「問診を始める」をクリック強調 （注意: 患者側の PNG は参加前のもの。この時点では患者はまだ開いていないので黒100%で隠す） | 医師: 問診を始める | 医師が問診を始めます。 |
| 0:02 | 0:05 | `01-doctor-qr.png` | `02-patient-join-language.png` | 左（医師） | 患者側（黒100%） | QR を表示（短いフェード） | QR を読むだけ。アプリのインストールは不要 | 患者さんにはスマートフォンで QR コードを読んでもらいます。 |
| 0:05 | 0:08 | `01-doctor-qr.png` | `02-patient-join-language.png` | 右（患者） | 医師側（50%） | 患者側の黒を外す。「中文（简体）」をタップ強調 | 患者: 自分の言語を選ぶ | 患者さんは中国語を選びます。 |
| 0:08 | 0:12 | `01-doctor-qr.png` | `03-patient-consent.png` | 右（患者） | 医師側（50%） | 「同意并开始」をタップ強調 | AI で訳すこと・間違えうること・診断しないこと・終われば記録が消えることに同意 | AI で訳すこと、診断はしないこと、終わったら記録が消えることを確認してから始めます。 |
| 0:12 | 0:14 | `05-doctor-connected.png` | `04-patient-connected.png` | 両方 | なし | 短いフェード。医師側の QR が閉じる | 同じ問診につながった | これで医師と患者が同じ問診につながりました。 |
| 0:14 | 0:19 | `05-doctor-connected.png` | `06-patient-first-typed.png` | 右（患者） | 医師側（50%） | 入力欄に文字が入る（タイプのアニメーション可）。まだ送らない | 入力しただけでは送られない | 患者さんが症状を伝えます。駅で急に「心慌」、胸が詰まるよう、全身に力が入らない。水土不服かと思ったが、休んでも良くならない。 |
| 0:19 | 0:20 | `05-doctor-connected.png` | `06-patient-first-typed.png` | 右（患者） | 医師側（50%） | 「发送」をタップ強調 （注意: Kumu に送信前の確認ダイアログは無い。送信操作の後に Safety Gate が確かめる） | 患者が送信 | 送信します。 |
| 0:20 | 0:24 | `08-doctor-first-pending.png` | `07-patient-first-checking.png` | 両方 | なし | 両画面の「確認中」の表示（スピナー） | 意図・翻訳・照合を確かめ終わるまで、医師にはまだ訳を出さない | ここで Kumu は、意図の解析、翻訳、意味の照合をしています。確かめ終わるまで、医師の画面には未確定の訳を出しません。 |
| 0:24 | 0:28 | `09-doctor-first-received.png` | `10-patient-first-delivered.png` | 両方 | なし | 届いた瞬間に両画面を明るく | 照合済みの日本語訳が届く／患者は医師が読んだ内容を中国語で確認 | 照合を通った日本語訳が届きました。患者さんの画面には、医師が読んだ内容が中国語で表示されます。違っていれば言い直せます。 |
| 0:28 | 0:32 | `09-doctor-first-received.png` | `10-patient-first-delivered.png` | なし | 患者側（50%） | 問診メモの「未確認」タグを枠で強調 | 「心慌」を動悸と決めつけない（未確認） | 問診メモは原文に根拠のある項目だけです。「心慌」は動悸とも不安感とも取れるので、AI の解釈は未確認のままです。 |
| 0:32 | 0:36 | `11-doctor-candidates.png` | `10-patient-first-delivered.png` | 左（医師） | 患者側（50%） | 確認候補「心慌」のカードを枠で強調 | AI の確認候補（提案だけ。決めるのは医師） | AI が、医師が確かめるべき点を候補として出します。聞くかどうかは医師が決めます。 |
| 0:36 | 0:40 | `12-doctor-evidence.png` | `10-patient-first-delivered.png` | 左（医師） | 患者側（50%） | 問診メモの項目をクリック → 原文の該当箇所のハイライトを強調 | 根拠は患者の原文 | どの項目も、患者さんの原文のどこから来たかをたどれます。 |
| 0:40 | 0:44 | `13-doctor-question-edit.png` | `10-patient-first-delivered.png` | 左（医師） | 患者側（50%） | 確認候補の「編集」をクリック → 編集欄を強調 | 医師が質問を選び、言葉を直す | 医師は質問を選び、自分の言葉に直せます。 |
| 0:44 | 0:45 | `13-doctor-question-edit.png` | `10-patient-first-delivered.png` | 左（医師） | 患者側（50%） | 「質問を送る」をクリック強調 （注意: 編集した質問の「照合中」の画面は撮れていない（すぐ終わった）） | 医師が送信 | 送ります。編集した質問も、訳し直して照合してから届きます。 |
| 0:45 | 0:49 | `14-doctor-question-sent.png` | `15-patient-question-received.png` | 両方 | なし | 届いた瞬間に両画面を明るく | 質問が中国語で届く（選択肢・分からない・どれでもない・自分の言葉） | 質問が中国語で届きました。選択肢から選ぶことも、自分の言葉で答えることもできます。 |
| 0:49 | 0:53 | `14-doctor-question-sent.png` | `16-patient-answer1-typed.png` | 右（患者） | 医師側（50%） | 入力欄に文字が入る。まだ送らない | 患者が自分の言葉で答える | 患者さんは、心臓が急に速く打つ感じで、10 分休んでも良くならなかったと答えます。 |
| 0:53 | 0:54 | `14-doctor-question-sent.png` | `16-patient-answer1-typed.png` | 右（患者） | 医師側（50%） | 「发送」をタップ強調 | 患者が送信 |  |
| 0:54 | 0:56 | `18-doctor-answer1-pending.png` | `17-patient-answer1-checking.png` | 両方 | なし | 両画面の「確認中」 | 照合中（医師にはまだ出ない） |  |
| 0:56 | 1:00 | `19-doctor-answer1-received.png` | `20-patient-answer1-delivered.png` | 両方 | なし | 届いた瞬間に両画面を明るく。「患者が回答」タグを強調 | 答えが届き、問診メモに反映（医師はまだ確認していない） | 答えが届き、「心慌」は心臓が速く打つ感覚だったと記録されます。これは患者回答で、医師の確認とは区別されます。 |
| 1:00 | 1:04 | `21-doctor-followup-typed.png` | `20-patient-answer1-delivered.png` | 左（医師） | 患者側（50%） | 問診メモ「胸部の閉塞感」から「患者に質問する」→ 質問欄の文字を強調 | 胸の症状は医師が自分で確かめる | 胸の症状は重要なので、医師が自分で質問を書きます。今も続いているか、意識を失ったことはないか。 |
| 1:04 | 1:05 | `21-doctor-followup-typed.png` | `20-patient-answer1-delivered.png` | 左（医師） | 患者側（50%） | 「質問を送る」をクリック強調 （注意: 照合中の画面は撮れていない（すぐ終わった）） | 医師が送信 |  |
| 1:05 | 1:09 | `22-doctor-followup-sent.png` | `23-patient-followup-received.png` | 両方 | なし | 届いた瞬間に両画面を明るく | 追加の質問が届く |  |
| 1:09 | 1:13 | `22-doctor-followup-sent.png` | `24-patient-answer2-typed.png` | 右（患者） | 医師側（50%） | 入力欄に文字が入る。まだ送らない | 患者: 今も胸が苦しい・息が少し苦しい・失神はない | 今も胸が苦しく、息が少し苦しい。倒れたことはない、と答えます。 |
| 1:13 | 1:14 | `22-doctor-followup-sent.png` | `24-patient-answer2-typed.png` | 右（患者） | 医師側（50%） | 「发送」をタップ強調 | 患者が送信 |  |
| 1:14 | 1:16 | `26-doctor-answer2-pending.png` | `25-patient-answer2-checking.png` | 両方 | なし | 両画面の「確認中」 | 照合中 |  |
| 1:16 | 1:20 | `27-doctor-answer2-received.png` | `25-patient-answer2-checking.png` | 左（医師） | 患者側（50%） | 医師側に答えが届く。訳と「患者が回答」を強調 （注意: ※ 届いた後の患者の画面（你的回答・医生已读）は撮影していない。患者側は最後に撮った 25 をマスクで暗くしたまま。届いた瞬間の「両方を明るく」はこの行では行わない） | 胸部の圧迫感が続く・失神はない（否定も保持） | 答えが届きました。続いている症状と、失神がないという否定の両方が残っています。 |
| 1:20 | 1:24 | `28-doctor-memo-after-questions.png` | `25-patient-answer2-checking.png` | なし | 患者側（50%） | 問診メモの下の方（否定された項目と、確認候補「浑身发虚」）を枠で強調 （注意: この時点で Conversation Auditor の未解決事項は出ていない（実際に出たのは 38）。未確認の項目は確認候補と「未確認」タグで見せる。患者側は上と同じ） | 未確認のまま残る項目（「浑身发虚」の確認候補） | 否定された項目も記録され、「浑身发虚」はまだ確かめていない項目として残っています。会話全体の食い違いは、後で Conversation Auditor が未解決事項として出します。 |
| 1:24 | 1:28 | `29-doctor-explain-typed.png` | `25-patient-answer2-checking.png` | 左（医師） | 患者側（50%） | 入力欄の文字を強調。まだ送らない （注意: 患者側は上と同じ（撮影していない状態）） | 医師の説明（AI は医療の指示を作らない） | 医師が説明します。今はそのまま座っていてください、すぐに医療スタッフを呼びます、自分で移動しないでください。 |
| 1:28 | 1:29 | `29-doctor-explain-typed.png` | `25-patient-answer2-checking.png` | 左（医師） | 患者側（50%） | 「送る」をクリック強調 （注意: 患者側は上と同じ） | 医師が送信 |  |
| 1:29 | 1:31 | `30-doctor-explain-checking.png` | `25-patient-answer2-checking.png` | 左（医師） | 患者側（50%） | 医師の発言に「訳を照合中…」 （注意: 患者側は上と同じ） | 説明も照合してから届く | 医師の説明も、照合してから届きます。 |
| 1:31 | 1:35 | `31-doctor-explain-sent.png` | `32-patient-explain-received.png` | 両方 | なし | 届いた瞬間に両画面を明るく | 説明が中国語で届く |  |
| 1:35 | 1:36 | `31-doctor-explain-sent.png` | `32-patient-explain-received.png` | 左（医師） | 患者側（50%） | 医師の説明の「理解を確認」をクリック強調 | 医師: 理解を確認 | 伝わったかを確かめます。 |
| 1:36 | 1:39 | `31-doctor-explain-sent.png` | `33-patient-teachback-prompt.png` | 右（患者） | 医師側（50%） | 「确认理解」のカードを強調 （注意: ※ 医師側の「理解の確認 送信済み」の吹き出しは 31 には無い（36 から見える）） | 患者に自分の言葉で言い直してもらう（teach-back） | 患者さんに、理解した内容を自分の言葉で言ってもらいます。 |
| 1:39 | 1:43 | `31-doctor-explain-sent.png` | `34-patient-teachback-wrong-typed.png` | 右（患者） | 医師側（50%） | 入力欄に文字が入る。まだ送らない （注意: 医師側は上と同じ） | 模擬患者の誤った理解: 自分で看護師を探しに行く | 模擬患者は、まず自分で歩いて看護師を探しに行くと理解しました。 |
| 1:43 | 1:44 | `31-doctor-explain-sent.png` | `34-patient-teachback-wrong-typed.png` | 右（患者） | 医師側（50%） | 「发送」をタップ強調 （注意: 医師側は上と同じ） | 患者が送信 |  |
| 1:44 | 1:46 | `36-doctor-teachback-wrong-pending.png` | `35-patient-teachback-wrong-checking.png` | 両方 | なし | 両画面の「確認中」 | 照合中 |  |
| 1:46 | 1:52 | `37-doctor-teachback-result.png` | `35-patient-teachback-wrong-checking.png` | 左（医師） | 患者側（50%） | 「認識の相違の可能性」カードを枠で強調（説明 / 患者の理解）。少し止める （注意: ※ 届いた後の患者の画面は撮影していない。患者側は 35 を暗くしたまま） | 認識の相違（AI の判定。決めるのは医師） | 医師の説明は「座ったまま、自分では動かない」。患者さんの理解は「自分で看護師を探しに行く」。この食い違いを項目ごとに示します。判定は AI、決めるのは医師です。 |
| 1:52 | 1:56 | `38-doctor-auditor-mismatch.png` | `35-patient-teachback-wrong-checking.png` | 左（医師） | 患者側（50%） | 問診メモ側の「未解決事項」カードを枠で強調 （注意: 患者側は上と同じ） | Conversation Auditor: 未解決事項として根拠つきで記録 | 会話全体を見ている Conversation Auditor も、この食い違いを未解決事項として記録します。解決にできるのは医師だけです。 |
| 1:56 | 1:57 | `38-doctor-auditor-mismatch.png` | `35-patient-teachback-wrong-checking.png` | 左（医師） | 患者側（50%） | 「再説明する」をクリック強調 （注意: 患者側は上と同じ） | 医師: 再説明する |  |
| 1:57 | 2:01 | `39-doctor-reexplain-typed.png` | `35-patient-teachback-wrong-checking.png` | 左（医師） | 患者側（50%） | 入力欄の再説明案を強調 （注意: 患者側は上と同じ） | AI の再説明案は入力欄に入るだけ。医師が読んで送る | 再説明の案が入力欄に入ります。医師が読んで、必要なら直してから送ります。 |
| 2:01 | 2:02 | `39-doctor-reexplain-typed.png` | `35-patient-teachback-wrong-checking.png` | 左（医師） | 患者側（50%） | 「送る」をクリック強調 （注意: 患者側は上と同じ） | 医師が送信 |  |
| 2:02 | 2:04 | `40-doctor-reexplain-checking.png` | `35-patient-teachback-wrong-checking.png` | 左（医師） | 患者側（50%） | 「訳を照合中…」 （注意: 患者側は上と同じ） | 照合中 |  |
| 2:04 | 2:08 | `41-doctor-reexplain-sent.png` | `42-patient-reexplain-received.png` | 両方 | なし | 届いた瞬間に両画面を明るく | 再説明が届く |  |
| 2:08 | 2:09 | `41-doctor-reexplain-sent.png` | `42-patient-reexplain-received.png` | 左（医師） | 患者側（50%） | 「理解を確認」をクリック強調 | 医師: もう一度理解を確認 |  |
| 2:09 | 2:12 | `41-doctor-reexplain-sent.png` | `43-patient-teachback-prompt2.png` | 右（患者） | 医師側（50%） | 「确认理解」のカードを強調 （注意: ※ 医師側の 2 回目の「理解の確認」の吹き出しは 41 には無い（46 から見える）） | もう一度言い直してもらう |  |
| 2:12 | 2:16 | `41-doctor-reexplain-sent.png` | `44-patient-teachback-right-typed.png` | 右（患者） | 医師側（50%） | 入力欄に文字が入る。まだ送らない （注意: 医師側は上と同じ） | 患者: ここに座って待つ。自分では動かない | 今度は、ここに座って待つ、自分では動かない、と言い直しました。 |
| 2:16 | 2:17 | `41-doctor-reexplain-sent.png` | `44-patient-teachback-right-typed.png` | 右（患者） | 医師側（50%） | 「发送」をタップ強調 （注意: 医師側は上と同じ） | 患者が送信 |  |
| 2:17 | 2:19 | `46-doctor-teachback-right-pending.png` | `45-patient-teachback-right-checking.png` | 両方 | なし | 両画面の「確認中」 | 照合中 |  |
| 2:19 | 2:23 | `47-doctor-teachback-result2.png` | `55-patient-before-end.png` | 両方 | なし | 届いた瞬間に両画面を明るく。「説明と一致」を強調 （注意: 患者側の 55 は終了前に撮った画像だが、この時点から終了まで患者の画面は変わらない（你的回答・医生已读）） | 理解が一致 | 説明と一致しました。 |
| 2:23 | 2:26 | `48-doctor-teachback-confirmed.png` | `55-patient-before-end.png` | 左（医師） | 患者側（50%） | 「理解を確認済みにする」をクリック強調 → 「医師が理解を確認」 | 医師が理解を確認（前の相違も解決） | 医師が理解を確認しました。さっきの相違も解決になります。 |
| 2:26 | 2:29 | `49-doctor-confirm-before.png` | `55-patient-before-end.png` | 左（医師） | 患者側（50%） | 問診メモの項目をクリック → 根拠と履歴を強調 | 医師が確認する前（患者回答） |  |
| 2:29 | 2:31 | `50-doctor-confirm-after.png` | `55-patient-before-end.png` | 左（医師） | 患者側（50%） | 「確認済みにする」をクリック強調 → チェックの印 | 医師が確認したものだけ「医師確認済み」 | 医師が確認したものだけが、医師確認済みになります。 |
| 2:31 | 2:35 | `51-doctor-summary-top.png` | `55-patient-before-end.png` | 左（医師） | 患者側（50%） | 問診メモを強調（医師確認済み・患者回答・未確認・否定） | 最終の問診メモ | 最終の問診メモです。医師が確認したもの、患者の答え、まだ未確認のものが区別されています。 |
| 2:35 | 2:38 | `52-doctor-summary-bottom.png` | `55-patient-before-end.png` | 左（医師） | 患者側（50%） | 問診メモを下へスクロール（未解決事項・確認候補） | 未確認の項目も残す |  |
| 2:38 | 2:42 | `53-doctor-karte.png` | `55-patient-before-end.png` | 左（医師） | 患者側（50%） | 「カルテにコピー」をクリック → 下書きのダイアログ | カルテ用の下書き（医師が編集してから貼る） | カルテ用の下書きを作ります。これは医師が直してから使う下書きで、病名は足しません。 |
| 2:42 | 2:43 | `52-doctor-summary-bottom.png` | `55-patient-before-end.png` | 左（医師） | 患者側（50%） | ダイアログを閉じ、「診察を終える」をクリック強調 （注意: 閉じた後の画面として 52 を使う（ダイアログを閉じた後の状態と同じ）） | 診察を終える |  |
| 2:43 | 2:46 | `56-doctor-ended.png` | `57-patient-ended.png` | 両方 | なし | 両画面が終了に切り替わる（短いフェード） | 記録は両方から消える | 診察を終えると、記録は医師と患者の両方から消えます。 |
| | | **── ここから補足デモ（別の問診・人工的に注入した誤訳）──** | | | | | | |
| 2:46 | 2:48 | `supplementary/06-doctor-lossy-typed.png` | `supplementary/09-patient-lossy-not-delivered.png` | 両方 | 患者側（50%） | 本編からの切り替え（フェード）。境界のテロップを大きく （注意: 補足デモは別の問診。患者側 09 は 08 と同時に撮った画像だが、それまでの患者の画面も同じ（何も届いていない）） | 補足デモ: 人工的に注入した誤訳（本物の Gemini の誤訳ではありません） | ここからは補足です。照合が誤訳を止める様子を見せるため、訳にわざと誤りを入れています。 |
| 2:48 | 2:49 | `supplementary/06-doctor-lossy-typed.png` | `supplementary/09-patient-lossy-not-delivered.png` | 左（医師） | 患者側（50%） | 「送る」をクリック強調 | 医師: 検査の前は8時間何も食べないでください |  |
| 2:49 | 2:51 | `supplementary/07-doctor-lossy-checking.png` | `supplementary/09-patient-lossy-not-delivered.png` | 左（医師） | 患者側（50%） | 「訳を照合中…」 | 照合中 |  |
| 2:51 | 2:55 | `supplementary/08-doctor-lossy-review.png` | `supplementary/09-patient-lossy-not-delivered.png` | 左（医師） | なし | 相違のカード（8時間が無い）を強調。患者側に何も届いていないことも見せるため、マスクなし （注意: 誤訳は人工的に注入） | 人工的な誤訳: 「8時間」が落ちた → まだ患者には届いていない | 訳から「8時間」が落ちています。照合がそれを見つけ、患者さんにはまだ届けません。言い直すか、未検証として送るかを医師が決めます。 |
| 2:55 | 2:57 | `supplementary/10-doctor-lossy-rephrase.png` | `supplementary/09-patient-lossy-not-delivered.png` | 左（医師） | 患者側（50%） | 「言い直す」をクリック強調 → 元の文が入力欄に戻る | 医師が言い直す |  |
| 2:57 | 2:59 | `supplementary/11-doctor-lossy-fixed-typed.png` | `supplementary/09-patient-lossy-not-delivered.png` | 左（医師） | 患者側（50%） | 言い直した文と「送る」をクリック強調 | 言い直して送信 |  |
| 2:59 | 3:00 | `supplementary/12-doctor-lossy-fixed-checking.png` | `supplementary/09-patient-lossy-not-delivered.png` | 左（医師） | 患者側（50%） | 「訳を照合中…」 | 照合中 |  |
| 3:00 | 3:02 | `supplementary/13-doctor-lossy-fixed-result.png` | `supplementary/14-patient-lossy-fixed-received.png` | 両方 | なし | 届いた瞬間に両画面を明るく | 照合を通った訳だけが届く |  |
| 3:02 | 3:04 | `supplementary/13-doctor-lossy-fixed-result.png` | `supplementary/15-patient-pt-lossy-typed.png` | 右（患者） | 医師側（50%） | 患者が入力（我对青霉素过敏）。まだ送らない | 患者: ペニシリンにアレルギーがある | 患者側でも同じです。 |
| 3:04 | 3:05 | `supplementary/13-doctor-lossy-fixed-result.png` | `supplementary/15-patient-pt-lossy-typed.png` | 右（患者） | 医師側（50%） | 「发送」をタップ強調 | 患者が送信 |  |
| 3:05 | 3:07 | `supplementary/17-doctor-pt-lossy-pending.png` | `supplementary/16-patient-pt-lossy-checking.png` | 両方 | なし | 両画面の「確認中」 | 照合中 |  |
| 3:07 | 3:11 | `supplementary/19-doctor-pt-lossy-waiting.png` | `supplementary/18-patient-pt-lossy-review.png` | 右（患者） | なし | 患者側の「翻译可能和你的意思不一样」と「医生会看到」を強調。医師側に訳が出ていないことも見せるため、マスクなし （注意: 誤訳は人工的に注入） | 人工的な誤訳: 「アレルギーはありません」に反転 → 医師には届かない | 訳で否定が反転しています。患者さんには、医師が読むことになる内容が表示され、言い直すことができます。医師には届いていません。 |
| 3:11 | 3:13 | `supplementary/13-doctor-lossy-fixed-result.png` | `supplementary/20-patient-pt-lossy-rephrase.png` | 右（患者） | 医師側（50%） | 「换个说法」をタップ強調 → 元の文が入力欄に戻る （注意: 言い直すと元の発言は取り消され、医師の画面から「確認中」が消える。その状態の医師の画面として 13（患者の発言が届く前）を使う） | 患者が言い直す | 誤った訳は、最後まで医師に届きませんでした。 |

## 使わなかった画像（20 枚）

- `54-doctor-karte-bottom.png`
- `supplementary/00-doctor-start.png`
- `supplementary/01-doctor-qr.png`
- `supplementary/02-patient-join-language.png`
- `supplementary/03-patient-consent.png`
- `supplementary/04-patient-connected.png`
- `supplementary/05-doctor-connected.png`
- `supplementary/21-patient-pt-fixed-typed.png`
- `supplementary/22-patient-pt-fixed-checking.png`
- `supplementary/23-doctor-pt-fixed-pending.png`
- `supplementary/24-doctor-pt-fixed-received.png`
- `supplementary/25-doctor-offer-typed.png`
- `supplementary/26-doctor-offer-checking.png`
- `supplementary/27-patient-fine-typed.png`
- `supplementary/28-patient-fine-checking.png`
- `supplementary/29-doctor-fine-pending.png`
- `supplementary/30-patient-fine-intent.png`
- `supplementary/31-doctor-fine-intent-waiting.png`
- `supplementary/32-patient-fine-chosen.png`
- `supplementary/33-doctor-fine-confirmed.png`
