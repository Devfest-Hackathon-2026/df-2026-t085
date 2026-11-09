# ステージング（本番と分けた検証環境）

`feature/unsaid-clinical-meaning` を、本番（main の画面・API・データ）に触れずに動かすための手順。**まだ実行していない**（作業環境に gcloud / Firebase CLI の認証が無かった）。

| | 本番（そのまま） | ステージング |
|---|---|---|
| 画面 | `https://unsaid-devfest.web.app`（Hosting の live） | Hosting のプレビューチャンネル `https://unsaid-devfest--clinical-meaning-<ランダム>.web.app` |
| API | Cloud Run `unsaid-api` | Cloud Run `unsaid-api-staging`（別サービス） |
| Firestore | `(default)` | 名前付きデータベース `staging` |
| ルール | `(default)` のルール（触らない） | `staging` のルールだけを公開 |

画面は**ホスト名に `--` があるとき（プレビューチャンネル）だけ**ステージングの API とデータベースを使う（`web/firebase-config.js`）。本番の URL での動きは変わらない。

## 1. Firestore のデータベースと TTL（初回だけ）

```powershell
gcloud firestore databases create --database=staging --location=asia-northeast1 --project unsaid-devfest
gcloud firestore fields ttls update expireAt --collection-group=sessions --enable-ttl --database=staging --project unsaid-devfest
gcloud firestore fields ttls update expireAt --collection-group=messages --enable-ttl --database=staging --project unsaid-devfest
```

## 2. ルールを staging だけに公開

```powershell
firebase deploy --config firebase.staging.json --only firestore:staging --project unsaid-devfest
```

`firebase.staging.json` には `staging` データベースしか書いていないので、`(default)` のルールは変わらない。
（このブランチの `firestore.rules` は以前の画面の書き方もすべて通すので、仮に `(default)` に公開しても main の画面は壊れない。ただし本番の設定は変えないこと。）

## 3. API（Cloud Run の別サービス）

```powershell
gcloud run deploy unsaid-api-staging --source . --region asia-northeast1 --service-account unsaid-api@unsaid-devfest.iam.gserviceaccount.com --allow-unauthenticated --memory 512Mi --timeout 120 --max-instances 2 --set-env-vars FIRESTORE_DATABASE=staging,FALLBACK_MODEL=gemini-3.5-flash
```

- URL は `https://unsaid-api-staging-799163399675.asia-northeast1.run.app` になる（`web/firebase-config.js` に書いた値）。違ったら書き換える。
- 本番のサービス `unsaid-api` には触れない。サービスアカウントは同じ（Vertex AI・Firestore・Speech-to-Text の権限）。
- `FALLBACK_MODEL` は混雑時の予備。`thinking_level=low` に対応したモデルを選ぶ（2026-11 時点の Vertex AI の資料で `gemini-3.5-flash` は LOW に対応）。

## 4. 画面（Hosting のプレビューチャンネル）

```powershell
firebase hosting:channel:deploy clinical-meaning --expires 7d --project unsaid-devfest
```

プレビューチャンネルは live の画面を置き換えない。表示された URL の `/doctor.html` を開く。
匿名ログインはプロジェクト共通なので、そのまま使える（Firebase コンソールの「承認済みドメイン」にプレビューの URL が要る場合は追加する）。

## 5. 確かめる

```powershell
python smoke.py --api https://unsaid-api-staging-799163399675.asia-northeast1.run.app --key <apiKey> --database staging --lang zh-CN --text "今天在车站突然觉得头晕，眼前发黑，差点晕倒。" --restatement "明天再来就可以了，是吗？"
python eval/compare.py --live
```

## 片付け

```powershell
firebase hosting:channel:delete clinical-meaning --project unsaid-devfest
gcloud run services delete unsaid-api-staging --region asia-northeast1
```

（`staging` データベースの中身は TTL で消える。データベースごと消すときは `gcloud firestore databases delete --database=staging`。）
