# Cloud Run へのデプロイ手順（Windows / PowerShell）

PowerShell で、リポジトリのフォルダ（`C:\DEV\Project-Xingularity`）に移動してから実行する。
コマンドは1つずつ、1行のまま貼り付けて実行する（途中で改行すると1行目だけが実行されてエラーになる）。

## 0. 先にローカルでテスト（ネット・クレジットを使わない）

```powershell
cd C:\DEV\Project-Xingularity
.\.venv\Scripts\Activate.ps1          # 仮想環境を使っている場合
pip install -r requirements.txt
python test_server.py                 # 「すべて PASS」と出れば OK
```

## 1. 初回だけ: gcloud の準備

```powershell
gcloud auth login
gcloud config set project unsaid-devfest
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com firestore.googleapis.com speech.googleapis.com
```

## 2. 初回だけ: API 専用のサービスアカウントを作る

Cloud Run が Gemini（Vertex AI）と Firestore を使うための「ロボット用アカウント」。必要な権限だけを付ける。

```powershell
gcloud iam service-accounts create unsaid-api --display-name "UNSAID API"

gcloud projects add-iam-policy-binding unsaid-devfest --member "serviceAccount:unsaid-api@unsaid-devfest.iam.gserviceaccount.com" --role roles/aiplatform.user

gcloud projects add-iam-policy-binding unsaid-devfest --member "serviceAccount:unsaid-api@unsaid-devfest.iam.gserviceaccount.com" --role roles/datastore.user

gcloud projects add-iam-policy-binding unsaid-devfest --member "serviceAccount:unsaid-api@unsaid-devfest.iam.gserviceaccount.com" --role roles/speech.client
```

## 3. デプロイ（コードを変えるたびにこれだけ）

```powershell
gcloud run deploy unsaid-api --source . --region asia-northeast1 --service-account unsaid-api@unsaid-devfest.iam.gserviceaccount.com --allow-unauthenticated --memory 512Mi --timeout 120 --max-instances 3
```

- `--max-instances 3` は乱用されたときに台数（と Gemini の呼び出し）が際限なく増えないようにする上限。デモでも1台で足りる。

- 初回は「Artifact Registry のリポジトリを作りますか？」と聞かれるので `Y`。
- 3〜5 分で `Service URL: https://unsaid-api-xxxx.asia-northeast1.run.app` が出る。これを Yusei に渡す（`docs/api.md` の `API_URL`）。
- `--allow-unauthenticated` は「URL を誰でも叩ける」設定。中で Firebase のログイン（ID トークン）と session のメンバーかを確認しているので、メンバー以外は 401 / 403 で弾かれる。
- リージョンは Firestore と同じ `asia-northeast1`（東京）。Gemini は `unsaid_ai.py` の `LOCATION = "global"` のまま。

## 4. 動いているか確認

```powershell
Invoke-RestMethod https://unsaid-api-xxxx.asia-northeast1.run.app/health
# ok が True なら OK

gcloud run services logs read unsaid-api --region asia-northeast1 --limit 50
# analyze 2.8s needs_clarification=True のような行が Gemini の所要時間
```

## 5. Firebase 側の設定（初回だけ、ブラウザで）

1. [Firebase コンソール](https://console.firebase.google.com/) で `unsaid-devfest` を開く（無ければ「プロジェクトを追加」→ 既存の GCP プロジェクトを選ぶ）。
2. Authentication → Sign-in method → **匿名** を有効にする。
3. Firestore Database → ルール → `firestore.rules` の中身を貼り付けて「公開」。

## 初回だけ: 問診の記録を自動で消す（TTL）

API は session と発言に `expireAt`（最後に使ってから 24 時間後）を書く。Firestore の TTL ポリシーを付けると、
医師が「診察を終える」を押し忘れた session も自動で消える（消えるのは期限から通常 24 時間以内）。
session を消しても下の messages は消えないので、2つとも付ける。

```powershell
gcloud firestore fields ttls update expireAt --collection-group=sessions --enable-ttl --project unsaid-devfest
gcloud firestore fields ttls update expireAt --collection-group=messages --enable-ttl --project unsaid-devfest
```

普段は医師の「診察を終える」で、その場で session と発言をすべて消す（`POST /end`）。

## 調整できる環境変数

| 変数 | 既定 | 中身 |
|---|---|---|
| `HEDGE_SEC` | `5` | Gemini がこの秒数で返らなければ同じ依頼をもう1本投げ、早い方を使う。`0` で使わない |
| `MAX_CALLS_PER_SESSION` | `200` | 1つの問診で API を呼べる回数（/analyze・/translate・/transcribe の合計）。超えると 429 |
| `TTL_HOURS` | `24` | `expireAt` を「最後に使ってから何時間後」にするか |
| `GEMINI_MODEL` | `gemini-3.8-flash` | 使うモデル。比べ方は `eval/compare_models.py` |
| `TRANSLATE_MODEL` | 空 | 翻訳だけ別のモデルにするとき。空なら `GEMINI_MODEL` |
| `FALLBACK_MODEL` | 空 | 混雑（429）が続いたときの予備のモデル（下の「デモの日だけ」） |
| `ANALYZE_MODE` | `split` | `single` で確認質問と facts を1回の呼び出しに戻す |
| `ALLOWED_ORIGINS` | `*` | API を呼べる画面の URL（カンマ区切り） |

## デモの日だけ: 起動待ちを無くす

0 台から起動すると最初の1回が数秒遅い。デモ・審査の日（11/10、11/14）は1台起こしたままにする。
起こしたままの間は少額の課金が続くので、終わったら 0 に戻す。

```powershell
gcloud run services update unsaid-api --region asia-northeast1 --min-instances 1   # デモ前
gcloud run services update unsaid-api --region asia-northeast1 --min-instances 0   # デモ後
```

当日は他のチームも Gemini を使うので混雑（429）が増える。予備のモデルを入れておくと、元のモデルで2回弾かれた時点で切り替える（`thinking_level` に対応したモデルを選ぶ。空なら切り替えない）。

```powershell
gcloud run services update unsaid-api --region asia-northeast1 --update-env-vars FALLBACK_MODEL=<予備のモデル名>
```

本番では `--set-env-vars ALLOWED_ORIGINS=https://<App Hosting の URL>` を付けて、呼べるサイトを自分たちの画面だけにする。

## つまずいたとき

| 症状 | 原因と対処 |
|---|---|
| デプロイ中に `PERMISSION_DENIED` / build が失敗 | 新しいプロジェクトでは Cloud Build 用の権限が足りないことがある。次を実行して再デプロイ: `$n = gcloud projects describe unsaid-devfest --format "value(projectNumber)"` のあと `gcloud projects add-iam-policy-binding unsaid-devfest --member "serviceAccount:$n-compute@developer.gserviceaccount.com" --role roles/cloudbuild.builds.builder` |
| API が 502、ログに `403 PERMISSION_DENIED` (aiplatform) | 手順 2 の `roles/aiplatform.user` が付いていない |
| API が 502、ログに Firestore の `403` | 手順 2 の `roles/datastore.user` が付いていない |
| `/transcribe` が 502、ログに `403` (speech) | `speech.googleapis.com` の有効化か `roles/speech.client` が抜けている（docs/stt.md） |
| API が 401 | 画面から `Authorization: Bearer <ID トークン>` が送られていない。匿名ログインが有効か確認 |
| ブラウザのコンソールに CORS エラー | `ALLOWED_ORIGINS` に画面の URL が入っていない（未設定なら全許可） |

## ローカルで API を動かす（任意）

```powershell
gcloud auth application-default login   # 初回だけ。自分の権限で Gemini と Firestore に繋がる
uvicorn main:app --reload --port 8080
# http://localhost:8080/docs で API を試せる（ID トークンが要る）
```

## 画面なしで端から端まで確かめる（スモークテスト）

Firebase の設定（手順 5）が終わったら、本物の Firebase / Firestore / Gemini を通して一連の流れを確かめられる。
`firestore.rules` が意図どおりに弾くかも一緒に確かめる。`--key` は Firebase コンソールの「プロジェクトの設定 → マイアプリ」にある `apiKey`。

```powershell
python smoke.py --api https://unsaid-api-xxxx.asia-northeast1.run.app --key <apiKey>
python smoke.py --api https://unsaid-api-xxxx.asia-northeast1.run.app --key <apiKey> --lang vi --text "Dạo này tôi hay bị đau đầu."
```

最後に「すべて OK」と出れば成功。FAIL のときは、その行の HTTP コードと中身が原因（403 で Firestore と出たらルール、502 なら `gcloud run services logs read ...` のログを見る）。
テストで作った session（`smoke-` で始まる）は Firestore に残るので、気になれば Firebase コンソールから消す。
