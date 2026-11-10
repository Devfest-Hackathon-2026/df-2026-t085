# ステージング（本番と分けた検証環境）

> **2026-11-10 時点の状況**: 下の手順はチームのプロジェクト `unsaid-devfest` の権限が要る。評価を担当するアカウントにはまだ権限が無いため、
> 本物の Gemini の評価は自分のプロジェクト（`kumu-devfest-2026`）で行った（`UNSAID_VERTEX_PROJECT`、下の「B」）。
> ステージングをどちらで作るかは、チームで決める（A: チームのプロジェクト / B: 自分のプロジェクト）。

`feature/unsaid-clinical-meaning`（Kumu 2.0）を、本番（main の画面・API・データ）に触れずに動かすための手順。
**まだ実行していない**（作業環境に gcloud / Firebase CLI の認証が無かった）。本番のリソースを上書きするコマンドは無い。

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
**このブランチの `firestore.rules` を `(default)` に公開してはいけない。** Safety Gate を迂回できないよう、端末が `status: sent` を書くことを禁止したので、
以前の画面（main）では患者が送れなくなる。本番に入れるときは、新しい画面とルールを同時に切り替える。

公開する前に、本物のルールエンジンでルールを確かめられる（Java 21 と firebase-tools が要る。2026-11-10 にエミュレーターで 10 件成功）:

```powershell
npm i --no-save firebase-tools@14 @firebase/rules-unit-testing@4 firebase@11
npm run test:rules
npm run test:emulator   # サーバーの pytest を、偽の Firestore ではなくエミュレーターで
```

## 3. API（Cloud Run の別サービス）

```powershell
gcloud run deploy unsaid-api-staging --source . --region asia-northeast1 --service-account unsaid-api@unsaid-devfest.iam.gserviceaccount.com --allow-unauthenticated --memory 512Mi --timeout 120 --max-instances 2 --set-env-vars FIRESTORE_DATABASE=staging,FALLBACK_MODEL=gemini-3.5-flash,TRANSLATION_RETRIES=1
```

- URL は `https://unsaid-api-staging-799163399675.asia-northeast1.run.app` になる（`web/firebase-config.js` に書いた値）。違ったら書き換える。
- 本番のサービス `unsaid-api` には触れない。サービスアカウントは同じ（Vertex AI・Firestore・Speech-to-Text の権限）。
- `--timeout 120` は、意図の解析・照合・訳し直し・再照合まで含めた最悪の時間（`LOCK_SEC` 110 秒）より長くする。
- 役割ごとのモデルは `SOURCE_MODEL` `VERIFY_MODEL` `AUDIT_MODEL` `TRANSLATE_MODEL` で替えられる（既定はすべて `gemini-3.8-flash`）。
  照合を軽いモデルにするのは、`eval/compare.py --live` で精度が落ちないと確かめてから。
- `FALLBACK_MODEL` は混雑時の予備。`thinking_level=low` に対応したモデルを選ぶ（2026-11 時点の Vertex AI の資料で `gemini-3.5-flash` は LOW に対応）。

## 4. 画面（Hosting のプレビューチャンネル）

```powershell
firebase hosting:channel:deploy clinical-meaning --expires 7d --project unsaid-devfest
```

プレビューチャンネルは live の画面を置き換えない。表示された URL の `/doctor.html` を開く。
ログイン（医師のメール＋パスワード・患者の匿名）はプロジェクト共通なので、そのまま使える（Firebase コンソールの「承認済みドメイン」にプレビューの URL が要る場合は追加する）。

## 5. 確かめる

```powershell
python smoke.py --api https://unsaid-api-staging-799163399675.asia-northeast1.run.app --key <apiKey> --database staging --lang zh-CN --text "今天在车站突然觉得头晕，眼前发黑，差点晕倒。" --restatement "明天再来就可以了，是吗？"
python eval/compare.py --live --pivot
python devtools/browser_check.py --out shots   # （BASE を書き換えればプレビューチャンネルにも使える）
```

## 片付け

```powershell
firebase hosting:channel:delete clinical-meaning --project unsaid-devfest
gcloud run services delete unsaid-api-staging --region asia-northeast1
```

（`staging` データベースの中身は TTL で消える。データベースごと消すときは `gcloud firestore databases delete --database=staging`。）


---

## B. 自分のプロジェクト（例: `kumu-devfest-2026`）で、ステージングを丸ごと別に作る場合

チームの本番（`unsaid-devfest` の Cloud Run・Firestore・Hosting）には一切触れない。代わりに、次のものを**新しく作る（課金が発生しうる）**。
実行する前に、作るものと費用をチームで確認する。

| 作るもの | コマンドの例 | 費用 |
|---|---|---|
| API の有効化 | `gcloud services enable aiplatform.googleapis.com run.googleapis.com firestore.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com speech.googleapis.com --project kumu-devfest-2026` | 有効化自体は無料 |
| Firebase の追加・ウェブアプリ・匿名ログイン | Firebase コンソールで既存の GCP プロジェクトに Firebase を追加 → ウェブアプリを登録 → Authentication で「匿名」と「メール / パスワード」を有効 → 医師のアカウントを作り、その UID で Firestore に `doctors/{uid}` を作る（[deploy.md](deploy.md) の手順 5） | 無料枠 |
| Firestore（`(default)` で可。チームのとは別のプロジェクトなので混ざらない） | `gcloud firestore databases create --location=asia-northeast1 --project kumu-devfest-2026` ＋ TTL（上の 1 と同じ） | 少量なら無料枠 |
| ルール | `firebase deploy --only firestore:rules --project kumu-devfest-2026` | 無料 |
| Cloud Run（API） | `gcloud run deploy unsaid-api-staging --source . --region asia-northeast1 --project kumu-devfest-2026 --allow-unauthenticated --memory 512Mi --timeout 120 --max-instances 2 --set-env-vars UNSAID_VERTEX_PROJECT=kumu-devfest-2026,FIREBASE_PROJECT=kumu-devfest-2026,STT_PROJECT=kumu-devfest-2026` | 使った分（Cloud Build・Artifact Registry を含む） |
| Hosting（画面） | `firebase hosting:channel:deploy clinical-meaning --project kumu-devfest-2026 --expires 7d` | 少量なら無料枠 |

サーバーの接続先はすべて環境変数で替えられる（コードの変更は不要）:

| 環境変数 | 何の接続先か | 既定 |
|---|---|---|
| `UNSAID_VERTEX_PROJECT` / `UNSAID_VERTEX_LOCATION` / `UNSAID_MODEL` | Gemini（Vertex AI） | `unsaid-devfest` / `global` / `gemini-3.8-flash` |
| `FIREBASE_PROJECT` | Firebase（ID トークンの確認・Firestore） | `unsaid-devfest` |
| `FIRESTORE_DATABASE` | Firestore のデータベース | `(default)` |
| `STT_PROJECT` / `STT_LOCATION` | Speech-to-Text | `unsaid-devfest` / `us` |

**画面だけはコードの変更が要る**: `web/firebase-config.js` はチームのプロジェクトのウェブ設定（apiKey・appId など）と API の URL を
決め打ちしている。自分のプロジェクトで動かすには、そのプロジェクトのウェブ設定（Firebase コンソールで表示される、秘密ではない値）と
Cloud Run の URL を、ホスト名（`kumu-devfest-2026--…web.app`）で切り替える分岐を足す。本番の URL での動きは変えない。

サービスアカウントの権限（Cloud Run の実行アカウントに Vertex AI ユーザー・Firestore ユーザー・Speech-to-Text）は、
自分のプロジェクトの中の変更。チームのプロジェクトの権限は変えない。

### B の安全の設定（2026-11-10 追加）

**参加者の制限**（匿名ログインだけでは Gemini を使えない）:
- Cloud Run に `STAGING_ACCESS_SHA256`（参加コードの SHA-256）を渡すと、Gemini・Speech-to-Text を使う API は、
  医師が問診ごとに参加コードで解錠する（`/staging/unlock`）まで 403。患者・第三者は解錠できない。間違いは問診ごとに 5 回まで
- 解錠の印（`stagingAccess`）はサーバーだけが書ける（ルールで端末の書き込みを禁止。エミュレーターのテストで確認）
- 参加コードは Git に置かない。医師の画面で 1 回入れると、そのタブの sessionStorage にだけ残る
- CORS（`ALLOWED_ORIGINS`）と推測しにくい URL は補助で、認証の代わりにしない

**回数・同時実行の上限**: 問診ごと `MAX_CALLS_PER_SESSION=80`、プロジェクト全体で 1 日（UTC）`DAILY_CALL_LIMIT=300`（Firestore の `_limits/` に数える）、
Cloud Run `--max-instances 1 --concurrency 20`。

**費用の上限**: Vertex AI（Agent Platform）と Cloud Run に spend cap の予算（Cloud Billing → 予算とアラート →「Spend cap enforcement」。
プロジェクト 1 つ・サービス 1 つずつ。月の 1 日からの総額で数える。止まるまで少し遅れる）。ほかのサービス（Cloud Build・Artifact Registry・
Firestore・Hosting・Speech-to-Text）は spend cap の対象外なので、プロジェクト全体にアラートだけの予算を置く。

**TTL**: `sessions`・`messages`・`_limits` の `expireAt`（コレクショングループ）に TTL ポリシー。端末が作る問診と発言にも最初から `expireAt` を付ける
（ルールで必須。48 時間以内）。サーバーが書くたびに 24 時間後へ延ばす。TTL の削除は期限の後すぐではなく、通常 24 時間以内。

**テストの後の片付け**（課金が続くものから）:

| もの | 扱い |
|---|---|
| Cloud Run `kumu-api-staging` | 削除（`gcloud run services delete`） |
| Artifact Registry `cloud-run-source-deploy` のイメージ | リポジトリごと削除（保存の料金が続く） |
| Cloud Storage のソースのバケット（`run-sources-…`） | 削除 |
| Firestore のデータ | 診察終了（/end）か TTL で消える。空のデータベースは残してよい |
| Hosting | `firebase hosting:disable`（画面を止める） |
| サービスアカウント・有効にした API・spend cap | 料金は無い。残してよい（消すなら最後） |
