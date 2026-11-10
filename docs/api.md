# Kumu API（Cloud Run）と画面側の約束

画面（Yusei）が知っておくことは「Firestore に発言を作る → その id で API を呼ぶ → あとは onSnapshot で待つ」だけ。
API のレスポンスを使わなくても、結果は同じメッセージに書き戻されるので画面は勝手に更新される。

> **2026-11-09 追記（`feature/unsaid-clinical-meaning`）**：既定の流れが「医師が確認質問を選ぶ」（`CLARIFY_MODE=doctor`）になった。
> 下の「医師が選ぶ流れ」が今の約束。その後ろの節は従来の流れ（`CLARIFY_MODE=patient`）として残している。
> データの形と状態遷移は [ARCHITECTURE.md](../ARCHITECTURE.md)、判断の理由は [DESIGN_DECISIONS.md](../DESIGN_DECISIONS.md)。

## Kumu 2.0: Safety Gate を通る送信（既定・2026-11-09）

端末がするのは「発言を作る（＝送信操作）」と、その id で API を呼ぶことだけ。**`status: sent` を書けるのはサーバーだけ**（firestore.rules）。
医師の発言も患者の発言も同じ流れ（[ARCHITECTURE.md](../ARCHITECTURE.md) の 3・4）。

```
端末   addDoc {from, original, status:"analyzing"[, replyTo, answer]}  → 患者 POST /analyze ／ 医師 POST /translate
API    gate.state: SOURCE_CONFIRMED → ANALYZING →（意図が決まらない）AWAITING_INTENT_CLARIFICATION（status "awaiting_intent"）
                                             → VERIFYING → READY_TO_SEND → SENT（status "sent"、translation を書く）
                                                         → NEEDS_REVIEW（status "needs_review"。自動では送らない）
端末   AWAITING のとき: POST /intent {messageId, choice: "i1"… | "free_text" + text | "none"}
       NEEDS_REVIEW のとき: POST /review {messageId, action: "send_unverified" | "rephrase"}
       いつでも（送る前）: POST /cancel {messageId}
医師   患者の未着の発言: POST /reveal {messageId} → {original, lang, state, verified:false, draft}
医師   未解決事項: POST /issue/update {issueId, action: hold | unhold | resolve | dismiss | reopen, note?}
```

| メソッド | パス | 誰が | 成功時 | 断るとき |
|---|---|---|---|---|
| POST | `/analyze` | 患者（自分の発言） | `{status: sent｜awaiting_intent｜needs_review｜stopped, verification, timings}` | 発言者以外 403、処理中・処理済み 409、二重送信 409（2つ目は取り消し）、Gemini の失敗 502（status error・原文は残る） |
| POST | `/translate` | 医師（自分の発言） | 同上 | 同上 |
| POST | `/intent` | 発言者 | 本人の意味で訳し直し、照合して送る | AWAITING 以外 409、選択肢が無い・説明が空 400 |
| POST | `/review` | 発言者 | `send_unverified` → 送る（`gate.override = speaker_ack`）／`rephrase` → 取り消し（`original` を返す） | NEEDS_REVIEW 以外 409 |
| POST | `/cancel` | 発言者 | 取り消し（`original` を返す）。解析が後から終わっても送らない | 送信済み 409 |
| POST | `/reveal` | 医師 | 患者の未着の発言の原文（未検証）。見たことを記録 | 届いた発言 400、患者 403 |
| POST | `/issue/update` | 医師 | 未解決事項の状態 | 閉じた事項の保留・できない遷移 409、無い事項 404 |
| POST | `/teachback/start` | 医師 | `{messageId}`。`previousId`（再説明の前の理解の確認）を渡すと、確認済みにしたとき前の相違も解決 | |
| POST | `/clarify/send` | 医師 | 確認候補の対訳をそのまま送る → すぐ届く（`gate.verification = PRETRANSLATED`）。**編集・自作した質問は医師の発言と同じく Safety Gate を通る**（`{status: sent｜awaiting_intent｜needs_review, messageId}`。確認待ちは `/intent`・`/review`）。元の候補・`issueId` の未解決事項は、質問が**届いたときに**「質問送信済み」・「確認中」になる | 同じ候補の質問がまだ確認中なら 409 |

メッセージの `gate`: `state` `history[]` `run_id` `source_hash` `intent {state, decided_by, interpretations[], question_speaker, frame, confirmed}`
`verification {status, issues[{type, strength, layer, detail, detail_speaker?}], failures}` `draft {translation, back_translation}` `attempts[]` `override` `revealed[]` `timings`。
`translation` は SENT になったときにだけ書く。session の `audit {issues[], memory[], seq}` は Conversation Auditor（サーバーだけが書く）。

## 確認候補と理解の確認（前回、2026-11-09 の 1 回目）

```
患者の発言:
  端末   addDoc {from:"patient", original, status:"analyzing"}       → POST /analyze
  API    translate と meaning を並列 → translation, status "confirmed" → meaning（問診メモ・確認候補）を書き足す
  端末   逆翻訳を見て「送信」                                         → POST /send（status "sent"）
  医師   受け取った → status "received"（端末が直接書く）

医師が確認候補を送る:                                                  POST /clarify/send {messageId, candidateId[, questionDoctor]}
  API    医師の発言 {kind:"clarify", status:"sent", clarify{...}} を作り、候補を asked に
患者が答える:
  端末   addDoc {from:"patient", original, status:"analyzing", replyTo:<質問の id>, answer:{type, index?}} → POST /analyze
         type: option（original = 選んだ選択肢、index = 番号）/ unsure / none → API が訳を付けてそのまま "sent"
         type: free_text → 翻訳と解釈 → "confirmed" → 患者が POST /send
  API    送信済みになったら、元の発言の候補と fact を更新（clinical.apply_answer）

理解の確認:                                                            POST /teachback/start {messageId: 医師の説明}
  API    医師の発言 {kind:"teachback", teachback{explanation_id, status:"asked"}} を作る
  端末   患者は話す画面で自分の言葉で答える（replyTo, answer:{type:"free_text"}）→ POST /analyze → POST /send
  API    teachback_result を答えに、teachback.result / status "assessed" を依頼に書く
  医師   POST /teachback/resolve {messageId: 依頼, action: confirm | reexplain}
```

| メソッド | パス | 誰が | ボディ（`sessionId` に加えて） | 成功時 |
|---|---|---|---|---|
| POST | `/analyze` | 患者 | `messageId` | 200。`status`・`translation`・`meaning`（答えなら `teachback_result`） |
| POST | `/send` | 患者 | `messageId` | 200 `{status:"sent"}`。もう送っていれば何もしない。`confirmed` 以外は 409 |
| POST | `/clarify/send` | 医師 | `messageId` + `candidateId`（そのまま）／ + `questionDoctor`（編集）／ `questionDoctor` + 任意で `messageId`・`factId`（自分で書く） | 200 `{messageId, candidateId}`。編集・自作は Gemini で訳す（選択肢なし） |
| POST | `/clarify/update` | 医師 | `messageId`, `candidateId`, `action: hold｜dismiss｜restore` | 200。できない遷移は 409 |
| POST | `/fact/update` | 医師 | `messageId`, `factId`, `action: confirm｜flag｜reset`, `note?` | 200。できない遷移は 409。未解決の「再確認が必要」（`rechecks[].required`）は、この操作で解決として記録する（記録は消さない） |
| POST | `/teachback/start` | 医師 | `messageId`（送信済みの医師の説明） | 200 `{messageId}` |
| POST | `/teachback/resolve` | 医師 | `messageId`（依頼）, `action: confirm｜reexplain` | 200 |
| POST | `/meaning/retry` | メンバー | `messageId`（`meaning.status` が `error`、または期限切れの `retrying` の発言）。所有者 ID（`meaning.retry_run`）と期限（`meaning.retry_until`、130 秒）を付けて始め、サーバーが止まっても期限が切れたら引き継げる。初回と同じ文脈で解析し、保存時にトランザクションで最新を読み直す（引き継がれた古い処理は書き戻さない）。成功したら答えを元の候補・情報に結びつける（下の「医師の判断の後に届いた答え」）。回答履歴は送った順・重複なし | 200。必要なければ・期限内のやり直し中・ほかの処理に引き継がれたら 409 |

- 医師だけの API を患者・第三者が呼ぶと 403。問診を作ってから `SESSION_MAX_HOURS`（12 時間）を過ぎると 410。
- `/send` と医師の操作は Gemini を使わないので、呼び出し回数（`MAX_CALLS_PER_SESSION`）に数えない。
- 以前の画面のように Firestore に直接 `status: "sent"` を書いても送れる（ルールで許している）が、その場合は答えを元の情報に結びつけない。

## 全体の流れ

音声入力のときは、この前に `POST /transcribe` で文字にして、患者が確認してから下の `addDoc` をする（docs/stt.md）。

```
患者の発言:
  端末   addDoc {from:"patient", original, status:"analyzing"}  → POST /analyze
  API    確認質問を書く（facts は少し後から同じメッセージに書き足す）
           確認質問あり → status "clarifying"（clarification.answer = null）
           確認質問なし → 翻訳まで済ませて status "confirmed"
  端末   （clarifying のとき）clarification.answer を書く        → POST /translate
  API    translation を書く → status "confirmed"
  端末   逆翻訳を見て「送信」→ status "sent"
  医師   受け取った → status "received"

医師の発言:
  端末   addDoc {from:"doctor", original, status:"analyzing"}   → POST /translate
  API    translation を書く → status "sent"（医師の発言は確認なし）
```

## エンドポイント

ベース URL は `gcloud run deploy` の最後に出る `https://unsaid-api-xxxx.asia-northeast1.run.app`。

| メソッド | パス | いつ呼ぶ | 成功時 |
|---|---|---|---|
| POST | `/analyze` | 患者の発言を作った直後 | 200。status は `clarifying` か `confirmed` |
| POST | `/translate` | 患者: `clarification.answer` を書いた後 ／ 医師: 発言を作った直後 | 200。患者は `confirmed`、医師は `sent` |
| POST | `/transcribe?sessionId=&speaker=patient\|doctor` | 音声入力のとき、発言を作る前（ボディは WAV） | 200。`{text, lang}`。Firestore には書かない。詳しくは docs/stt.md |
| POST | `/end` | 医師が「診察を終える」とき（ボディは `{"sessionId"}` だけ） | 200。`{deleted}`。その session の発言と session を消す。医師（`members[0]`）以外は 403 |
| GET | `/health` | 動作確認 | `{"ok": true}` |

- ヘッダー: `Authorization: Bearer <Firebase の ID トークン>`（患者は匿名ログイン、医師はメール＋パスワードでログインした `auth.currentUser.getIdToken()`）
- ボディ: `{"sessionId": "...", "messageId": "..."}` だけ。本文や言語は API が Firestore から読み直す（端末の言うことをそのまま信じない）。
- レスポンス: Firestore に書いた内容と同じ（デバッグ用。画面は onSnapshot を使えばよい）。
- 上限: 1つの session で /analyze・/translate・/transcribe を合わせて `MAX_CALLS_PER_SESSION` 回（既定 200）。超えると 429。
- サーバーは session と発言に `apiCalls`（呼んだ回数）と `expireAt`（Firestore の TTL で消える期限）を書き足す。画面は使わない。
- 待ち時間: `/analyze` は確認質問だけを先に Firestore に書く（facts と先回りの翻訳は並列）。質問ありでも質問なしでも、画面に出るまで Gemini 1回分。
  HTTP のレスポンスは facts まで書き終えてから返るので、**質問の表示は onSnapshot で行う**（`await callApi()` の後に表示すると速くならない）。

| エラー | 意味 | 画面での扱い |
|---|---|---|
| 401 | ID トークンが無い・無効 | ログインし直す |
| 403 | その session のメンバーでない | |
| 404 | session / message が無い | |
| 409 | もう処理済み（二重押し）／同じ発言を処理中（`lockedUntil`）／回答前に /translate を呼んだ | 無視してよい |
| 400 | from が違う、本文が空か 1000 文字超、answer.type が不正 | |
| 502 | Gemini が再試行しても失敗。メッセージの status は `error` になる | 「もう一度」ボタン → 下のコード例の `retry()` |

通信が切れて API に届かなかった・サーバーが途中で落ちたときは、status が `analyzing`（や回答済みの `clarifying`）のまま変わらない。画面は 25 秒進まなければ「もう一度」を出す（`web/ui.js` の `stuckWatch`）。`/analyze` で確認質問の呼び出しだけが失敗したときは、確認なしの翻訳で `confirmed` まで進め、医師に見せる `translation.note` に「自動の確認ができませんでした」と書く。

## メッセージのフィールド（sessions/{sessionId}/messages/{messageId}）

| フィールド | 書く人 | 中身 |
|---|---|---|
| `from` | 端末（作成時） | `"patient"` / `"doctor"` |
| `original` | 端末（作成時） | 話した内容（1〜1000 文字） |
| `status` | 下の表 | `analyzing` → `clarifying` → `confirmed` → `sent` → `received`、失敗時 `error` |
| `createdAt` | 端末（作成時） | `serverTimestamp()`（ルールでサーバーの時刻以外は弾く） |
| `facts` | API | `[{text, doctor_gloss, kind, negated, uncertain, time?}]`。医師画面の補助表示。`status` が `clarifying` / `confirmed` になった直後はまだ無いことがある（1〜数秒後に届く。失敗したら `[]`） |
| `clarification` | API（`answer` だけ患者の端末） | `null` か `{target, question, ui_type, options[], fixed_options{unsure, free_text}, answer}` |
| `translation` | API | `null` か `{lang, text, back_translation, note, clarified_target, clarified_answer}`（`clarified_*` は確認に答えた患者の発言だけ。医師の言語で「しばらく」→「約1週間」。それ以外は `""`） |
| `error` | API | 失敗したときだけ。例外名 |
| `lockedUntil` | API | 処理中だけ。同じ発言を2つのリクエストが同時に処理しないための印（60 秒で切れる） |
| `sentAt` / `receivedAt` | 患者の端末 / 医師の端末（医師の発言の `sentAt` は API） | `serverTimestamp()` |

`clarification` の使い方:
- `target` は必ず `original` の中にある文字列。原文のその部分をハイライトする。
- 選択肢 = `options`（モデルが作る、患者の言語）＋ `fixed_options.unsure`「うまく説明できない」＋ `fixed_options.free_text`「自分で書く」。
- 回答は `clarification.answer = {type, text}` として書く。
  - 選択肢を選んだ: `{type: "option", text: "<選んだ選択肢の文字>"}`
  - 自分で書いた: `{type: "free_text", text: "<入力>"}`
  - 説明できない: `{type: "unsure", text: ""}`（曖昧なまま訳し、`translation.note` で医師に知らせる）

`translation` の使い方:
- 医師画面: `text`（日本語）を主表示、`note` があれば注意書き、`facts[].doctor_gloss` は補助。
- 患者画面（送信前の確認）: `back_translation`（患者の言語に訳し戻したもの）を「医師にはこう伝わります」として見せる。

## 状態ごとの画面

| status | 患者画面 | 医師画面 |
|---|---|---|
| `analyzing` | 「確認しています…」 | 表示しない |
| `clarifying`（`answer` が null） | 確認質問と選択肢 | 表示しない |
| `clarifying`（`answer` あり） | 「翻訳しています…」 | 表示しない |
| `confirmed` | 逆翻訳と「送信」ボタン | 表示しない |
| `sent` | 送信済み | 翻訳を表示、開いたら `received` にする |
| `received` | 既読 | 表示 |
| `error` | 「もう一度」ボタン | 表示しない |

医師の発言は `analyzing` → `sent` なので、患者画面には `sent` になったら `translation.text` を出す。医師画面には `translation.back_translation` を「患者にはこう伝わります」として出せる。

## 画面側のコード例（Firebase JS SDK v10）

```js
import { addDoc, collection, doc, updateDoc, serverTimestamp } from "firebase/firestore";

const API_URL = "https://unsaid-api-xxxx.asia-northeast1.run.app";

async function callApi(path, sessionId, messageId) {
  const token = await auth.currentUser.getIdToken();
  const res = await fetch(API_URL + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ sessionId, messageId }),
  });
  if (!res.ok && res.status !== 409) throw new Error((await res.json()).detail);
}

// 発言する（from は "patient" か "doctor"）
async function say(sessionId, from, text) {
  const ref = await addDoc(collection(db, "sessions", sessionId, "messages"), {
    from, original: text, status: "analyzing", createdAt: serverTimestamp(),
  });
  await callApi(from === "patient" ? "/analyze" : "/translate", sessionId, ref.id);
}

// 患者: 確認質問に答える（type は "option" / "free_text" / "unsure"）
async function answer(sessionId, messageId, type, text = "") {
  const ref = doc(db, "sessions", sessionId, "messages", messageId);
  await updateDoc(ref, { "clarification.answer": { type, text } });
  await callApi("/translate", sessionId, messageId);
}

// 患者: 逆翻訳を見て送信 ／ 医師: 受け取った
const send = (sid, mid) => updateDoc(doc(db, "sessions", sid, "messages", mid), { status: "sent", sentAt: serverTimestamp() });
const receive = (sid, mid) => updateDoc(doc(db, "sessions", sid, "messages", mid), { status: "received", receivedAt: serverTimestamp() });

// 「もう一度」: status が error のメッセージを、止まった所から呼び直す
function retry(sessionId, m) {
  const path = m.from === "patient" && !m.clarification?.answer ? "/analyze" : "/translate";
  return callApi(path, sessionId, m.id);
}
```

`session` は `{patientLang, doctorLang, members: [uid, ...], createdAt}`。`patientLang` は `unsaid_ai.py` の `LANGS` にある言語（`en` / `zh-CN` / `vi`）。医師が QR で作った直後は `null` で、患者が参加するときに1回だけ書く（それまで API は 409）。`doctorLang` は `ja`。
`members[0]` が医師（作った人）、`members[1]` が患者（QR で参加した人）。`from: "doctor"` の発言は医師だけ、`from: "patient"` の発言・回答・送信は患者だけ、既読は医師だけが書ける（`firestore.rules`）。
Firestore のルール案は `firestore.rules`（端末が書けるのは上の表の「端末」の項目だけ）。

## 構成の選び方（Own the Why）

**採用: Cloud Run 上の FastAPI を、端末が発言を作った直後に HTTP で呼ぶ。**

| 案 | 良い点 | 悪い点 | 判断 |
|---|---|---|---|
| **A. HTTP API（FastAPI / Cloud Run）を端末が呼ぶ** | 失敗がその場で返る（「もう一度」が作りやすい）。ローカルで `uvicorn` だけで動かせる。追加のインフラが無い。使わない時は 0 台で課金されない。検証済みの `unsaid_ai.py` をそのまま import できる | 端末の処理が「書く＋呼ぶ」の2段。呼ぶ前に端末が落ちると `analyzing` のまま残る | **採用**。2人・5日で E2E を確実に通すには、詰まったときに原因が見える構成が最優先 |
| B. Firestore トリガー（Eventarc → Cloud Run / Functions） | 端末は書くだけでよい | Eventarc と IAM の設定が増える。イベントは「最低1回」届くので二重処理対策が要る。API 自身の書き込みで再発火しないよう注意が要る。届くまで1秒前後の上乗せ。ローカルで再現しにくい | 不採用。仕組みはきれいだが、デモ前に詰まる箇所が増える |
| C. Cloud Run で Firestore を常時 listen | 端末は書くだけでよい | 常時起動（CPU 常時割り当て）が必要で課金が続く。台数が増えると同じ発言を複数台が処理する | 不採用。Cloud Run の「リクエストが来たら動く」モデルと合わない |
| D. 端末から Gemini を直接（Firebase AI Logic） | サーバーが要らない | プロンプト・スキーマ・再試行が端末側に出る。言語追加や修正のたびにアプリ更新。結果を誰が書いたか（改ざん）をルールで守りにくい | 不採用。Gemini の結果は信頼できる側（サーバー）だけが書く |

その他の判断:
- **API は id だけ受け取り、本文と言語は Firestore から読む。** 端末から本文を受け取ると、Firestore の本文と訳した本文がずれる・他人の session を訳させる、といった穴になる。
- **端末が先に発言を作る。** 自分の吹き出しが 0 秒で出る（Firestore の画面への即時反映）。Gemini の 3〜7 秒の待ちを「確認しています…」として見せられる。
- **確認質問なしの発言は、analyze の流れで翻訳まで済ませる。** 端末の呼び出しが1回で済み、患者は逆翻訳を見てから送れる。
- **失敗は `status: "error"`** にして、同じ API の呼び直しで復帰できるようにした（合意したスキーマへの追加）。
- **`translation` に `back_translation` と `note` を追加**（合意したスキーマは `{lang, text}`）。逆翻訳は VoiceTra 等との差別化ポイント。


## 医師の判断の後に届いた答え（`facts[].rechecks`）

医師が確認済み・要修正にした情報（fact）に、確認質問への答えが届いたとき、答えを送った時刻（`sentAt`）と医師の判断の時刻を比べる。

| 答え | 情報の状態 | 記録 |
|---|---|---|
| 医師の判断より 1 秒以上前に届いていた（医師は原文と訳を見て判断した。解析が遅れただけ） | 変えない | 履歴に `patient_answered_late` |
| 医師の判断の後に届いた・時刻が分からない・差が 1 秒未満（時計のずれ。安全側に倒す） | 患者回答あり（「分からない」なら未確認）に戻し、**再確認が必要** | 履歴に `patient_answered` と `recheck_required`。`rechecks` に、医師の判断（`doctor_event` `doctor_at` `doctor_note` `doctor_status`）・判断したときの答えと内容（`doctor_answer_id` `doctor_resolved_meaning`）・直前の答え（`previous_answer_id`）・新しい答え（`new_answer_id` `new_meaning`）を残す |

医師の判断の履歴は消さない。医師がもう一度判断すると `required: false`・`resolved_by`・`resolved_at` を付ける。
確認候補の `answers` は送った順に並べ、最後が今の答え。後から送った答えがすでにあれば、古い答えは記録だけ（`superseded_by`）で、情報を巻き戻さない。
医師の画面は問診メモに「再確認が必要」、根拠の欄に判断したときの内容と新しい答えを出す。カルテ用の文章では［要再確認］。
