# セキュリティ・データ管理・医療安全

## 1. Safety Gate（送信の安全装置）

| 守ること | どうしているか | 確かめた方法 |
|---|---|---|
| 発言者の明示的な送信操作なしに送らない | 送信操作＝端末が発言を作ること。音声認識の終了・Gemini の応答・解析の完了・時間経過では送らない | 画面のコード、`tests/test_gate_flow.py` |
| 意図が未確認の訳を確定として送らない | 意図が CLARIFICATION_REQUIRED / UNRESOLVED なら AWAITING_INTENT_CLARIFICATION で止め、下書きの訳は捨てる | `test_patient_intent_clarification_then_confirmed_translation` ほか |
| 照合の失敗・不一致で送らない | MISMATCH は 1 回だけ訳し直し、残れば NEEDS_REVIEW。VERIFICATION_FAILED（照合の失敗・スキーマ違反）も同じ。発言者が「未検証として送る」を選んだときだけ、未検証の印つきで送る | `test_unresolved_mismatch_goes_to_speaker_review`・`test_verifier_failure_never_sends`・`test_invalid_schema_from_verifier_never_sends` |
| 古い解析結果の混入 | run_id と原文のハッシュを送る直前にトランザクションで照合 | `test_stale_run_result_is_not_sent`・`test_send_checks` |
| 取り消し・診察終了の後に送らない | 状態 CANCELLED・session が無ければ送らない | `test_cancel_during_verification_is_not_sent`・`test_session_end_during_analysis_sends_nothing` |
| 二重送信 | 同じ発言は処理中の印で 409、同じ人の同じ文を 10 秒以内に再送したら2つ目を取り消す | `test_double_send_is_cancelled` |
| 端末から Safety Gate を迂回できない | **端末が `status: sent` を書くこと・訳や Gate の状態を書くこと・送った訳や原文を書き換えることをルールで禁止** | `tests/test_rules_emulation.py`（ルールの書き写しで 19 通り）。**本物のルールエンジンでは未確認**（`tests/rules/firestore.rules.test.mjs` を用意） |

送るかどうかは `ussa.send_checks` と状態遷移の表（`ussa.GATE_TRANSITIONS`）が決め、LLM の判断は記録として読むだけ（ARCHITECTURE.md の 4）。

## 2. 問診（session）の保護

| 守ること | どうしているか | 確かめた方法 |
|---|---|---|
| 推測されない id | Firestore の自動 id（20 文字・約 119 ビット）。QR は端末の中で作る | — |
| 参加できる人 | 作った人が医師（`members[0]`）、QR で参加した人が患者（`members[1]`）。30 分以内・2 人まで | `firestore.rules` |
| 読める人 | その問診の 2 人だけ（ルール）。API も毎回メンバーかを確認 | `pytest`（第三者は 403） |
| 医師と患者の権限分離 | 医師だけ: 確認質問を送る・候補の保留/無視・情報の確認/要修正・理解の確認・未解決事項の操作・未検証の原文を見る。発言者だけ: 自分の発言の解析・意図の選択・照合の確認・取り消し | `test_doctor_only_endpoints`・`test_only_the_author_triggers_the_gate`・`test_issue_actions_and_no_repeat_after_resolve` |
| 他の問診の情報 | API はその問診の中の発言しか読まない。`/` を含む id は 400。未解決事項は session ごと | `test_patient_only_send_and_session_isolation` |
| 期限 | 作ってから 12 時間で API は 410。TTL（`expireAt`）で自動削除 | `test_session_expiry` |
| 終了後 | 「診察を終える」で発言（確認質問・答え・理解の確認を含む）と session（未解決事項を含む）を消す | `test_end_deletes_all_new_records` |
| 鍵 | Gemini と Speech-to-Text は Cloud Run のサービスアカウント（Vertex AI）。API キーを画面に置かない | — |
| 費用の乱用 | 1 問診で Gemini・音声を使う API は 200 回まで。Cloud Run は最大 3 台 | `test_server.py` |

**ルールの公開について:** このブランチの `firestore.rules` は端末の送信を禁止したので、以前の画面（main）とは両立しない。
本番の `(default)` データベースには公開しない。ステージングの `staging` データベースにだけ公開する（[docs/staging.md](docs/staging.md)）。本番に入れるときは新しい画面と同時に切り替える。

## 3. データの管理

- **同意**: 患者は言語を選んだ後、自分の言語で「AI（Google Gemini）で訳す・間違えることがある・診断しない・終わったら消える・声は保存しない」を読んで同意してから参加する。
- **保存しないもの**: 音声（文字にした後に捨てる）、文字起こしの途中経過（本人が直して送ったものだけが発言になる）。
- **残す期間**: 診察中だけ。「診察を終える」と TTL で消える。
- **ログ**: 患者の発言・訳・AI の出力をログに出さない。出すのは処理時間・件数・状態・例外の種類だけ（`test_meaning_failure_keeps_original_and_can_retry` で確認）。
- **未検証の原文の閲覧**: 医師が見たことを `gate.revealed` に記録する。
- **デモ**: 架空の症例だけ。

## 4. 医療安全

- **診断・治療を決めない。** すべてのプロンプトで禁止。カルテ用の文章は AI に書かせない。AI による緊急度の判定もしない。
- **不確かな情報を確定として出さない。** AI の抽出は「AI 抽出」、意図は RESOLVED（AI）と USER_CONFIRMED（本人）を区別、照合は「相違なし」（正しさの証明ではない）、理解の判定は「AI の判定」。
  「医師確認済み」「解決済み」「理解を確認済み」は医師が押したときだけ（状態遷移の表で保証、テストで検査）。
- **原文を失わない。** どの失敗でも原文は残る。訳は送ったときだけ `translation` に、送る前は `gate.draft`。
- **未回答を否定にしない。** 答えない・分からない・どれでもないは未確認のまま。
- **医師が確定した情報を古い AI 処理で上書きしない。** 解析のやり直しは解析が失敗した発言だけ。Auditor は情報を変えない。
- **重要な訴えを隠さない。** 確認が終わるまで医師が原文を見られる（未検証・意味未確定と明示）。
- **医療通訳の代わりとは言わない。** 臨床的な有効性は検証していない。

## 5. 残っている課題

- Firestore のルールは、Firestore エミュレーター（firebase-tools 14.27.0・Java 21）の本物のルールエンジンで確かめた（2026-11-10、`npm run test:rules`、10 件）:
  迂回の書き込み・重複した既読・2台の端末の同時の既読（本物の SDK の `runTransaction`）・解析のやり直しの所有権と問診メモの直接書き込み。
  サーバーの処理も、pytest 194 件を本物のトランザクションで流した（`npm run test:emulator`）。本番のプロジェクトのルールは確かめていない。
  エミュレーターで分かったこと: 2台が同時に既読にすると、後の方は（トランザクションの読み直しより先に）**ルールで断られる**。トランザクションだけでは防げない。
- 端末が直接書くのは「既読」（医師）と「参加」（患者）だけ。どちらもローカルキャッシュの反映を前提にしない: 既読はトランザクションでサーバーの最新の
  `status` を読み、`sent` のときだけ書く。2台が同時でルールに断られたら、サーバーから読み直し（`getDocFromServer`）、もう `sent` でなければ成功とみなす。参加は、二重押しでルールが断っても、参加できていれば成功とみなす
  （`web/unsaid.js`、`web/unsaid-client.test.mjs`）。
- 問診（session）を作れるのは、メール＋パスワードでログインし、かつ Firestore の `doctors/{uid}` がある医師だけ（`firestore.rules` の `isDoctorAccount`）。患者は匿名ログインのまま QR から参加する。
  医師のアカウントはこちらで作って登録する（画面からの新規登録はない）。医師の資格そのもの（HPKI・医師等資格確認検索など）は確かめていない。本番運用ではそれが要る。
- 照合（VERIFIED）は相違を見つけられなかったという意味。Gemini どうしが同じ誤りを共有する可能性は残る（ルール層で数・日時は独立に確認）。
- 本物の Gemini での誤検出・見逃しの率は未測定。医学的な妥当性は医師が評価していない。
- `/reveal` で返す未検証の機械翻訳は参考で、誤っている可能性がある（画面で明示）。
