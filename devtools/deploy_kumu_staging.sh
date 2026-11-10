#!/usr/bin/env bash
# 独立ステージング（kumu-devfest-2026）へ、同じコミットの API・Firestore のルール・画面をまとめて出す。
# チームのプロジェクト（unsaid-devfest）には何もしない: プロジェクトはすべて --project で明示し、既定のプロジェクト（.firebaserc）は使わない。
#
#   STAGING_ACCESS_SHA256=<参加コードの SHA-256> devtools/deploy_kumu_staging.sh   # API → ルール → 画面 → 版の確認
#
# 参加コードそのものは Git にもこのスクリプトにも置かない（SHA-256 だけを Cloud Run の環境変数に渡す）。
#
# 前提: gcloud と firebase が kumu-devfest-2026 を操作できる認証、作業ツリーが綺麗（コミット済み）、
#       web/firebase-config.js の KUMU_STAGING に Firebase のウェブ設定と API の URL が入っている（初回の作成手順は docs/staging.md の B）。
set -euo pipefail
cd "$(dirname "$0")/.."

P=kumu-devfest-2026
REGION=asia-northeast1
SERVICE=kumu-api-staging
SA="kumu-api-staging@${P}.iam.gserviceaccount.com"
ORIGINS="https://${P}.web.app,https://${P}.firebaseapp.com"
GCLOUD=${GCLOUD:-gcloud}
FIREBASE=${FIREBASE:-firebase}

branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = "feature/unsaid-clinical-meaning" ] || { echo "ブランチが違う: $branch"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "コミットしていない変更がある（画面・API・ルールの版をそろえるため、コミットしてから出す）"; exit 1; }
[ -n "${STAGING_ACCESS_SHA256:-}" ] || { echo "STAGING_ACCESS_SHA256 が空（参加者の制限なしでは出さない）"; exit 1; }
COMMIT=$(git rev-parse --short=12 HEAD)
RULES_SHA=$(sha256sum firestore.rules | cut -c1-16)
echo "commit=$COMMIT rules=$RULES_SHA"

# 1. API（Cloud Run）。ソースからビルドする（Cloud Build・Artifact Registry）
"$GCLOUD" run deploy "$SERVICE" --source . --project "$P" --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated \
  --memory 512Mi --timeout 120 --min-instances 0 --max-instances 1 --concurrency 20 \
  --set-env-vars "UNSAID_VERTEX_PROJECT=${P},UNSAID_VERTEX_LOCATION=global,FIREBASE_PROJECT=${P},STT_PROJECT=${P},ALLOWED_ORIGINS=${ORIGINS},BUILD_COMMIT=${COMMIT},TRANSLATION_RETRIES=1,STAGING_ACCESS_SHA256=${STAGING_ACCESS_SHA256},MAX_CALLS_PER_SESSION=80,DAILY_CALL_LIMIT=300" \
  --quiet

# 2. Firestore のルール（このプロジェクトの (default) データベースだけ）
"$FIREBASE" deploy --config firebase.kumu.json --only firestore:rules --project "$P" --non-interactive

# 3. 画面（Hosting）。版の印を一緒に置く（Git には入れない）
printf '{"commit":"%s","rules_sha256_16":"%s"}\n' "$COMMIT" "$RULES_SHA" > web/build.json
"$FIREBASE" deploy --config firebase.kumu.json --only hosting --project "$P" --non-interactive
rm -f web/build.json

# 4. 版の確認（画面のファイル・API・ルールが、このコミットと一致するか）
.venv/bin/python devtools/check_kumu_staging.py --commit "$COMMIT"
