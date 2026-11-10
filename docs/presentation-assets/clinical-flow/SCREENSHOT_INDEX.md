# 診察の流れ（全画面）— スクリーンショットの一覧

- 撮影: 2026-11-10 04:50 JST。本物の Gemini（評価用のプロジェクト kumu-devfest-2026）。Firestore はメモリ上の偽物。医師と患者は同じ問診（session id: 8e3cf18b4dc6455181bf（医師・患者とも同じ））
- 撮り方: `devtools/capture_clinical_flow.py`（Playwright・Chromium）。医師は CSS 1280×832・DPR 2、患者は CSS 402×874・DPR 3・モバイル（iPhone 16 Pro 相当）。
  ボタンを実際に押し、画面の状態を待ってから撮った。DOM は書き換えていない（問診メモのスクロールだけ）
- 症例は架空。Gemini の出力は撮るたびに少し変わる
- 失敗した場所: なし（最後まで完走）。画面のエラー: 6 件。大きさの不一致: なし

画面のエラー（ブラウザのコンソールの 403）は、どれも想定どおりの動き: 参加前の患者が問診を読もうとして断られる
（メンバーでない人は読めないルール。画面は「まだ参加していない」として扱う）、診察終了で問診を消した後の購読が断られる
（これで両方の画面が終了に切り替わる）。画面に出るエラーは無い。

## 送信の流れについて（実装どおり）

Kumu には「送信前の確認画面（ダイアログ）」は**無い**。入力しただけでは送られず、「发送／送る」を押すと Safety Gate の流れに入る:
入力済み・未送信 → 処理中（意図の解析・翻訳・照合。相手にはまだ何も届かない）→ 次のどれか
- 照合済みで届く（送信済み）
- 意図が決まらない → 発言者本人が意味を選ぶまで止まる（意図確認待ち）
- 照合で相違が残る → 発言者が「言い直す／未検証として送る」を選ぶまで止まる（送信保留）

医師の画面には、処理中の患者の発言は訳が出ない（「確認中」だけ。緊急時は「未検証の内容を見る」で原文を見られる）。
医師が明示的に「受け取った」を押す操作は無い。医師の画面に表示されると自動で既読になり、患者の画面に「医生已读」が出る。
送信後、患者は医師が読んだ内容（訳を中国語に戻したもの）を見て、違えば「不对？重新说」で言い直せる。

## 入力した文（毎回同じ）

| 場面 | 文 |
|---|---|
| first | 医生，我今天在车站突然心慌，胸口像堵住了一样，浑身发虚。我以为只是水土不服，可坐下来休息了一会儿，还是觉得不太对劲。 |
| answer1 | 我感觉心脏突然跳得很快，胸口也很闷。坐着休息了大约十分钟，还是没有好转。 |
| followup | 今も胸が詰まる感じや息苦しさは続いていますか？意識が遠くなったり、気を失ったりしたことはありますか？ |
| answer2 | 现在胸口还是闷，呼吸有点费力。没有晕倒过，也没有失去意识。 |
| explain | 今はそのまま座っていてください。すぐに医療スタッフを呼びます。ご自身で移動せず、スタッフの案内を待ってください。 |
| tb_wrong | 我先自己走去找护士，对吗？ |
| tb_right | 我留在这里坐着等，医生会马上叫医护人员过来，我不会自己走动。 |

## メインの流れ（58 枚）

| 順番 | ファイル | 端末 | 実際の操作 | 画面状態 | 表示される重要情報 | 実Gemini/モック | 備考 |
|---|---|---|---|---|---|---|---|
| 0 | `00-doctor-start.png` | 医師（MacBook 2560×1664） | 医師がアプリを開いた直後 | 入力前（問診の開始前） | 「問診を始める」だけがある | 実Gemini | サービス名を Kumu に変えた後に撮り直した（2026-11-10。AI を使わない画面。大きさ・構図は同じ） |
| 1 | `01-doctor-qr.png` | 医師（MacBook 2560×1664） | 「問診を始める」を押した | QR を表示（患者の参加待ち） | 患者がスマホで読む QR と説明 | 実Gemini |  |
| 2 | `02-patient-join-language.png` | 患者（iPhone 16 Pro 1206×2622） | QR のリンクを開いた | 参加前（言語の選択） | 患者の言語を選ぶ | 実Gemini |  |
| 3 | `03-patient-consent.png` | 患者（iPhone 16 Pro 1206×2622） | 中文を選んだ | 同意前 | AI で訳すこと・間違えうること・診断しないこと・終わったら消えること | 実Gemini |  |
| 4 | `04-patient-connected.png` | 患者（iPhone 16 Pro 1206×2622） | 「同意并开始」を押した | 接続済み・入力前 | 話す画面（マイクと入力欄） | 実Gemini |  |
| 5 | `05-doctor-connected.png` | 医師（MacBook 2560×1664） | 患者が参加した | 接続済み・入力前 | QR が閉じ、「患者（中国語）と接続中」 | 実Gemini |  |
| 6 | `06-patient-first-typed.png` | 患者（iPhone 16 Pro 1206×2622） | 最初の訴えを入力（まだ送らない） | 入力済み・未送信 | 医生，我今天在车站突然心慌，胸口像堵住了一样，浑身发虚。我以为只是水土不服，可坐下来休息了一会儿，还是觉得不太对劲。 | 実Gemini |  |
| 7 | `07-patient-first-checking.png` | 患者（iPhone 16 Pro 1206×2622） | 「发送」を押した直後 | 処理中（意図の解析・翻訳・照合） | 患者には確認中の表示。まだ医師に届いていない | 実Gemini |  |
| 8 | `08-doctor-first-pending.png` | 医師（MacBook 2560×1664） | 患者が送信した直後の医師の画面 | 処理中（医師にはまだ届かない） | 医師には「確認中」の表示だけ。訳は未確定なので出ない | 実Gemini |  |
| 9 | `09-doctor-first-received.png` | 医師（MacBook 2560×1664） | 患者の訴えが届いた | 受信済み（照合済みの訳） | 日本語訳・照合の状態・原文に根拠のある問診メモ（未確認）・確認候補 | 実Gemini |  |
| 10 | `10-patient-first-delivered.png` | 患者（iPhone 16 Pro 1206×2622） | 送信後の患者の画面 | 送信済み・医師が表示（医生已读） | 医師が読んだ内容（訳を中国語に戻したもの）と「不对？重新说」 | 実Gemini |  |
| 11 | `11-doctor-candidates.png` | 医師（MacBook 2560×1664） | 問診メモの確認候補を見る | 確認候補（AI の提案。まだ聞いていない） | 曖昧な語・なぜ確認が必要か・質問と選択肢。送る／編集／保留／無視は医師が選ぶ | 実Gemini |  |
| 12 | `12-doctor-evidence.png` | 医師（MacBook 2560×1664） | 問診メモの項目を開いた | 未確認（根拠の表示） | 原文の該当箇所・訳・状態・履歴 | 実Gemini |  |
| 13 | `13-doctor-question-edit.png` | 医師（MacBook 2560×1664） | 確認候補の「編集」を押した | 医師が質問を選び・編集中（未送信） | AI の質問案を医師が直せる。送ると Safety Gate を通る | 実Gemini |  |
| 14 | `14-doctor-question-sent.png` | 医師（MacBook 2560×1664） | 質問が届いた | 送信済み | 「確認質問」の吹き出しと送信済み | 実Gemini |  |
| 15 | `15-patient-question-received.png` | 患者（iPhone 16 Pro 1206×2622） | 医師の質問が届いた | 受信済み・回答前 | 中国語の質問・選択肢・分からない・どれでもない・自分の言葉で | 実Gemini |  |
| 16 | `16-patient-answer1-typed.png` | 患者（iPhone 16 Pro 1206×2622） | 質問への答えを入力（まだ送らない） | 入力済み・未送信 | 我感觉心脏突然跳得很快，胸口也很闷。坐着休息了大约十分钟，还是没有好转。 | 実Gemini |  |
| 17 | `17-patient-answer1-checking.png` | 患者（iPhone 16 Pro 1206×2622） | 「发送」を押した直後 | 処理中（意図の解析・翻訳・照合） | 患者には確認中の表示。まだ医師に届いていない | 実Gemini |  |
| 18 | `18-doctor-answer1-pending.png` | 医師（MacBook 2560×1664） | 患者が送信した直後の医師の画面 | 処理中（医師にはまだ届かない） | 医師には「確認中」の表示だけ。訳は未確定なので出ない | 実Gemini |  |
| 19 | `19-doctor-answer1-received.png` | 医師（MacBook 2560×1664） | 患者の答えが届いた | 受信済み（患者回答） | 答えの訳・「患者が回答」・問診メモの更新（医師はまだ確認していない） | 実Gemini |  |
| 20 | `20-patient-answer1-delivered.png` | 患者（iPhone 16 Pro 1206×2622） | 答えを送った後 | 送信済み | 医師が読んだ内容と「修改回答」 | 実Gemini |  |
| 21 | `21-doctor-followup-typed.png` | 医師（MacBook 2560×1664） | 「胸部の閉塞感・圧迫感」について医師が自分で質問を書いた | 入力済み・未送信 | 今も胸が詰まる感じや息苦しさは続いていますか？意識が遠くなったり、気を失ったりしたことはありますか？ | 実Gemini |  |
| 22 | `22-doctor-followup-sent.png` | 医師（MacBook 2560×1664） | 追加の質問が届いた | 送信済み |  | 実Gemini |  |
| 23 | `23-patient-followup-received.png` | 患者（iPhone 16 Pro 1206×2622） | 追加の質問が届いた | 受信済み・回答前 |  | 実Gemini |  |
| 24 | `24-patient-answer2-typed.png` | 患者（iPhone 16 Pro 1206×2622） | 追加の質問への答えを入力（まだ送らない） | 入力済み・未送信 | 现在胸口还是闷，呼吸有点费力。没有晕倒过，也没有失去意识。 | 実Gemini |  |
| 25 | `25-patient-answer2-checking.png` | 患者（iPhone 16 Pro 1206×2622） | 「发送」を押した直後 | 処理中（意図の解析・翻訳・照合） | 患者には確認中の表示。まだ医師に届いていない | 実Gemini |  |
| 26 | `26-doctor-answer2-pending.png` | 医師（MacBook 2560×1664） | 患者が送信した直後の医師の画面 | 処理中（医師にはまだ届かない） | 医師には「確認中」の表示だけ。訳は未確定なので出ない | 実Gemini |  |
| 27 | `27-doctor-answer2-received.png` | 医師（MacBook 2560×1664） | 患者の答えが届いた | 受信済み（患者回答） | 胸の症状が続いていること・失神はないこと | 実Gemini |  |
| 28 | `28-doctor-memo-after-questions.png` | 医師（MacBook 2560×1664） | 問診メモの下の方（確認候補・未解決事項） | 問診メモの更新後 | 症状・経過・本人の解釈・未確認の項目・未解決事項 | 実Gemini |  |
| 29 | `29-doctor-explain-typed.png` | 医師（MacBook 2560×1664） | 医師が説明を入力（まだ送らない） | 入力済み・未送信 | 今はそのまま座っていてください。すぐに医療スタッフを呼びます。ご自身で移動せず、スタッフの案内を待ってください。 | 実Gemini |  |
| 30 | `30-doctor-explain-checking.png` | 医師（MacBook 2560×1664） | 「送る」を押した直後 | 処理中（意図の解析・翻訳・照合） | 医師の発言は「確認中…／訳を照合中…」。患者にはまだ届いていない | 実Gemini |  |
| 31 | `31-doctor-explain-sent.png` | 医師（MacBook 2560×1664） | 説明が届いた | 送信済み | 照合済みの中国語訳で届く | 実Gemini |  |
| 32 | `32-patient-explain-received.png` | 患者（iPhone 16 Pro 1206×2622） | 医師の説明が届いた | 受信済み | 中国語の説明 | 実Gemini |  |
| 33 | `33-patient-teachback-prompt.png` | 患者（iPhone 16 Pro 1206×2622） | 医師が「理解を確認」を押した | 理解の確認（回答前） | 自分の言葉で言い直すよう頼まれる | 実Gemini |  |
| 34 | `34-patient-teachback-wrong-typed.png` | 患者（iPhone 16 Pro 1206×2622） | 理解の言い直し（誤り）を入力（まだ送らない） | 入力済み・未送信 | 我先自己走去找护士，对吗？ | 実Gemini |  |
| 35 | `35-patient-teachback-wrong-checking.png` | 患者（iPhone 16 Pro 1206×2622） | 「发送」を押した直後 | 処理中（意図の解析・翻訳・照合） | 患者には確認中の表示。まだ医師に届いていない | 実Gemini |  |
| 36 | `36-doctor-teachback-wrong-pending.png` | 医師（MacBook 2560×1664） | 患者が送信した直後の医師の画面 | 処理中（医師にはまだ届かない） | 医師には「確認中」の表示だけ。訳は未確定なので出ない | 実Gemini |  |
| 37 | `37-doctor-teachback-result.png` | 医師（MacBook 2560×1664） | 患者の言い直しが届いた | 意味不一致 | 説明・患者の理解・食い違う点。再説明／理解を確認済みにする（医師が決める） | 実Gemini |  |
| 38 | `38-doctor-auditor-mismatch.png` | 医師（MacBook 2560×1664） | 未解決事項を見る | 未解決事項（理解の相違） | 根拠の発言つき | 実Gemini |  |
| 39 | `39-doctor-reexplain-typed.png` | 医師（MacBook 2560×1664） | 医師が再説明（AI の提案を入力欄で確認）を入力（まだ送らない） | 入力済み・未送信 | ご自身では動かないで、そのまま座っていてください。すぐにスタッフを呼びますので、案内があるまでお待ちください。 | 実Gemini |  |
| 40 | `40-doctor-reexplain-checking.png` | 医師（MacBook 2560×1664） | 「送る」を押した直後 | 処理中（意図の解析・翻訳・照合） | 医師の発言は「確認中…／訳を照合中…」。患者にはまだ届いていない | 実Gemini |  |
| 41 | `41-doctor-reexplain-sent.png` | 医師（MacBook 2560×1664） | 再説明が届いた | 送信済み |  | 実Gemini |  |
| 42 | `42-patient-reexplain-received.png` | 患者（iPhone 16 Pro 1206×2622） | 再説明が届いた | 受信済み |  | 実Gemini |  |
| 43 | `43-patient-teachback-prompt2.png` | 患者（iPhone 16 Pro 1206×2622） | 医師がもう一度「理解を確認」 | 理解の確認（回答前） |  | 実Gemini |  |
| 44 | `44-patient-teachback-right-typed.png` | 患者（iPhone 16 Pro 1206×2622） | 理解の言い直し（正しい）を入力（まだ送らない） | 入力済み・未送信 | 我留在这里坐着等，医生会马上叫医护人员过来，我不会自己走动。 | 実Gemini |  |
| 45 | `45-patient-teachback-right-checking.png` | 患者（iPhone 16 Pro 1206×2622） | 「发送」を押した直後 | 処理中（意図の解析・翻訳・照合） | 患者には確認中の表示。まだ医師に届いていない | 実Gemini |  |
| 46 | `46-doctor-teachback-right-pending.png` | 医師（MacBook 2560×1664） | 患者が送信した直後の医師の画面 | 処理中（医師にはまだ届かない） | 医師には「確認中」の表示だけ。訳は未確定なので出ない | 実Gemini |  |
| 47 | `47-doctor-teachback-result2.png` | 医師（MacBook 2560×1664） | 正しい言い直しが届いた | 理解一致 |  | 実Gemini |  |
| 48 | `48-doctor-teachback-confirmed.png` | 医師（MacBook 2560×1664） | 医師が「理解を確認済みにする」 | 確認済み（医師） | 前の相違も解決になる | 実Gemini |  |
| 49 | `49-doctor-confirm-before.png` | 医師（MacBook 2560×1664） | 問診メモの項目を開いた | 医師確認待ち | 原文・訳・状態・履歴（患者の答え） | 実Gemini |  |
| 50 | `50-doctor-confirm-after.png` | 医師（MacBook 2560×1664） | 「確認済みにする」を押した | 確認済み（医師） | 履歴に「医師が確認」 | 実Gemini |  |
| 51 | `51-doctor-summary-top.png` | 医師（MacBook 2560×1664） | 最終の問診メモ（上） | 最終 | 症状・経過・本人の解釈・医師確認済み/患者回答/未確認 | 実Gemini |  |
| 52 | `52-doctor-summary-bottom.png` | 医師（MacBook 2560×1664） | 最終の問診メモ（下） | 最終 | 未解決事項・確認候補・カルテにコピー・診察を終える | 実Gemini |  |
| 53 | `53-doctor-karte.png` | 医師（MacBook 2560×1664） | 「カルテにコピー」を押した | カルテ用の下書き（医師が編集してから貼る） |  | 実Gemini |  |
| 54 | `54-doctor-karte-bottom.png` | 医師（MacBook 2560×1664） | カルテ用の文章の下の方 | カルテ用の下書き | 未確認事項・理解の確認 | 実Gemini |  |
| 55 | `55-patient-before-end.png` | 患者（iPhone 16 Pro 1206×2622） | 診察の終わりの前の患者の画面 | 受信済み |  | 実Gemini |  |
| 56 | `56-doctor-ended.png` | 医師（MacBook 2560×1664） | 「診察を終える」を押した | 終了（記録を削除） |  | 実Gemini |  |
| 57 | `57-patient-ended.png` | 患者（iPhone 16 Pro 1206×2622） | 医師が診察を終えた | 終了（記録を削除） | 患者の画面も終了 | 実Gemini |  |

## 撮影時のメモ

- session id: 8e3cf18b4dc6455181bf（医師・患者とも同じ）
- 最初の訴え: Source Agent は意図の確認を求めなかった（心慌などの医学的な曖昧さは、翻訳前の意図ではなく、届いた後に Meaning Engine が医師への確認候補にする設計。患者に先に聞かない）
- 質問の照合中の表示は撮れなかった
- この時点で Conversation Auditor の未解決事項は出なかった
- 追加の質問の照合中の表示は撮れなかった
- 再説明の文（AI の提案が入力欄に入り、医師が送った）: ご自身では動かないで、そのまま座っていてください。すぐにスタッフを呼びますので、案内があるまでお待ちください。


## 補助デモ（`supplementary/`、34 枚）

メインの流れでは、照合が誤訳を止める場面と、意図の確認が出る場面が起きなかったため、別の問診で撮った。
**誤訳は人工的に注入したもの**（開発サーバーの `UNSAID_INJECT_ERRORS=1`。決まった 2 文の翻訳だけを置き換える。本番には無い）。
本物の Gemini の誤訳ではない。照合・Safety Gate・言い直し・意図の確認は本物の Gemini とサーバーのコードで動いている。
撮影: 2026-11-10 04:52 JST。失敗した場所: なし

| 順番 | ファイル | 端末 | 実際の操作 | 画面状態 | 表示される重要情報 | 実Gemini/モック | 備考 |
|---|---|---|---|---|---|---|---|
| 0 | `supplementary/00-doctor-start.png` | 医師（MacBook 2560×1664） | 医師がアプリを開いた直後 | 入力前（問診の開始前） | 「問診を始める」だけがある | 実Gemini | サービス名を Kumu に変えた後に撮り直した（2026-11-10。AI を使わない画面。大きさ・構図は同じ） |
| 1 | `supplementary/01-doctor-qr.png` | 医師（MacBook 2560×1664） | 「問診を始める」を押した | QR を表示（患者の参加待ち） | 患者がスマホで読む QR と説明 | 実Gemini |  |
| 2 | `supplementary/02-patient-join-language.png` | 患者（iPhone 16 Pro 1206×2622） | QR のリンクを開いた | 参加前（言語の選択） | 患者の言語を選ぶ | 実Gemini |  |
| 3 | `supplementary/03-patient-consent.png` | 患者（iPhone 16 Pro 1206×2622） | 中文を選んだ | 同意前 | AI で訳すこと・間違えうること・診断しないこと・終わったら消えること | 実Gemini |  |
| 4 | `supplementary/04-patient-connected.png` | 患者（iPhone 16 Pro 1206×2622） | 「同意并开始」を押した | 接続済み・入力前 | 話す画面（マイクと入力欄） | 実Gemini |  |
| 5 | `supplementary/05-doctor-connected.png` | 医師（MacBook 2560×1664） | 患者が参加した | 接続済み・入力前 | QR が閉じ、「患者（中国語）と接続中」 | 実Gemini |  |
| 6 | `supplementary/06-doctor-lossy-typed.png` | 医師（MacBook 2560×1664） | 医師が説明（訳に人工的な誤りを注入）を入力（まだ送らない） | 入力済み・未送信 | 検査の前は8時間何も食べないでください。 | 実Gemini |  |
| 7 | `supplementary/07-doctor-lossy-checking.png` | 医師（MacBook 2560×1664） | 「送る」を押した直後 | 処理中（意図の解析・翻訳・照合） | 医師の発言は「確認中…／訳を照合中…」。患者にはまだ届いていない | 実Gemini |  |
| 8 | `supplementary/08-doctor-lossy-review.png` | 医師（MacBook 2560×1664） | 照合で相違（人工的に注入した誤訳） | 送信保留（医師の確認待ち） | どこが違うか・患者にはまだ届いていない・言い直す／未検証として送る | 実Gemini | 誤訳は人工的に注入（UNSAID_INJECT_ERRORS） |
| 9 | `supplementary/09-patient-lossy-not-delivered.png` | 患者（iPhone 16 Pro 1206×2622） | その間の患者の画面 | 未受信（届いていない） | 何も届いていない | 実Gemini |  |
| 10 | `supplementary/10-doctor-lossy-rephrase.png` | 医師（MacBook 2560×1664） | 「言い直す」を押した | 再入力（未送信） | 元の文が入力欄に戻る | 実Gemini |  |
| 11 | `supplementary/11-doctor-lossy-fixed-typed.png` | 医師（MacBook 2560×1664） | 医師が言い直した説明を入力（まだ送らない） | 入力済み・未送信 | 検査を受ける前の8時間は、何も食べないでください。 | 実Gemini |  |
| 12 | `supplementary/12-doctor-lossy-fixed-checking.png` | 医師（MacBook 2560×1664） | 「送る」を押した直後 | 処理中（意図の解析・翻訳・照合） | 医師の発言は「確認中…／訳を照合中…」。患者にはまだ届いていない | 実Gemini |  |
| 13 | `supplementary/13-doctor-lossy-fixed-result.png` | 医師（MacBook 2560×1664） | 言い直した説明 | 送信済み |  | 実Gemini |  |
| 14 | `supplementary/14-patient-lossy-fixed-received.png` | 患者（iPhone 16 Pro 1206×2622） | 言い直した説明が届いた | 受信済み |  | 実Gemini |  |
| 15 | `supplementary/15-patient-pt-lossy-typed.png` | 患者（iPhone 16 Pro 1206×2622） | 訴え（訳に人工的な誤りを注入）を入力（まだ送らない） | 入力済み・未送信 | 我对青霉素过敏。 | 実Gemini |  |
| 16 | `supplementary/16-patient-pt-lossy-checking.png` | 患者（iPhone 16 Pro 1206×2622） | 「发送」を押した直後 | 処理中（意図の解析・翻訳・照合） | 患者には確認中の表示。まだ医師に届いていない | 実Gemini |  |
| 17 | `supplementary/17-doctor-pt-lossy-pending.png` | 医師（MacBook 2560×1664） | 患者が送信した直後の医師の画面 | 処理中（医師にはまだ届かない） | 医師には「確認中」の表示だけ。訳は未確定なので出ない | 実Gemini |  |
| 18 | `supplementary/18-patient-pt-lossy-review.png` | 患者（iPhone 16 Pro 1206×2622） | 照合で相違（人工的に注入した誤訳） | 送信保留（本人の確認待ち） | 医生会看到（医師が読む内容）と、换个说法／仍然发送 | 実Gemini | 誤訳は人工的に注入 |
| 19 | `supplementary/19-doctor-pt-lossy-waiting.png` | 医師（MacBook 2560×1664） | その間の医師の画面 | 未受信（患者が確認中） | 訳は出ない。「未検証の内容を見る」だけ | 実Gemini |  |
| 20 | `supplementary/20-patient-pt-lossy-rephrase.png` | 患者（iPhone 16 Pro 1206×2622） | 「换个说法」を押した | 再入力（未送信） | 元の文が入力欄に戻る | 実Gemini |  |
| 21 | `supplementary/21-patient-pt-fixed-typed.png` | 患者（iPhone 16 Pro 1206×2622） | 言い直した訴えを入力（まだ送らない） | 入力済み・未送信 | 我对青霉素有过敏反应。 | 実Gemini |  |
| 22 | `supplementary/22-patient-pt-fixed-checking.png` | 患者（iPhone 16 Pro 1206×2622） | 「发送」を押した直後 | 処理中（意図の解析・翻訳・照合） | 患者には確認中の表示。まだ医師に届いていない | 実Gemini |  |
| 23 | `supplementary/23-doctor-pt-fixed-pending.png` | 医師（MacBook 2560×1664） | 患者が送信した直後の医師の画面 | 処理中（医師にはまだ届かない） | 医師には「確認中」の表示だけ。訳は未確定なので出ない | 実Gemini |  |
| 24 | `supplementary/24-doctor-pt-fixed-received.png` | 医師（MacBook 2560×1664） | 言い直した訴えが届いた | 受信済み |  | 実Gemini |  |
| 25 | `supplementary/25-doctor-offer-typed.png` | 医師（MacBook 2560×1664） | 医師が提案を入力（まだ送らない） | 入力済み・未送信 | 痛み止めの注射をしましょうか？ | 実Gemini |  |
| 26 | `supplementary/26-doctor-offer-checking.png` | 医師（MacBook 2560×1664） | 「送る」を押した直後 | 処理中（意図の解析・翻訳・照合） | 医師の発言は「確認中…／訳を照合中…」。患者にはまだ届いていない | 実Gemini |  |
| 27 | `supplementary/27-patient-fine-typed.png` | 患者（iPhone 16 Pro 1206×2622） | 「没事」を入力（まだ送らない） | 入力済み・未送信 | 没事。 | 実Gemini |  |
| 28 | `supplementary/28-patient-fine-checking.png` | 患者（iPhone 16 Pro 1206×2622） | 「发送」を押した直後 | 処理中（意図の解析・翻訳・照合） | 患者には確認中の表示。まだ医師に届いていない | 実Gemini |  |
| 29 | `supplementary/29-doctor-fine-pending.png` | 医師（MacBook 2560×1664） | 患者が送信した直後の医師の画面 | 処理中（医師にはまだ届かない） | 医師には「確認中」の表示だけ。訳は未確定なので出ない | 実Gemini |  |
| 30 | `supplementary/30-patient-fine-intent.png` | 患者（iPhone 16 Pro 1206×2622） | Source Agent が意味の確認を求めた | 意図確認待ち（選ぶ前） | 断る・同意・痛くない など | 実Gemini |  |
| 31 | `supplementary/31-doctor-fine-intent-waiting.png` | 医師（MacBook 2560×1664） | その間の医師の画面 | 意図確認待ち（医師には訳が出ない） |  | 実Gemini |  |
| 32 | `supplementary/32-patient-fine-chosen.png` | 患者（iPhone 16 Pro 1206×2622） | 意味を選んだ | 送信済み |  | 実Gemini |  |
| 33 | `supplementary/33-doctor-fine-confirmed.png` | 医師（MacBook 2560×1664） | 本人が選んだ意味で届いた | 受信済み（本人が意味を確認） |  | 実Gemini |  |


