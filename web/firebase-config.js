// Firebase と API の接続先。ここだけ書き換えれば web/unsaid.js がそのまま使える。
// firebaseConfig は Firebase コンソール → プロジェクトの設定（歯車）→ 全般 →「マイアプリ」のウェブアプリ（</>）にある。
// この値は秘密ではない（ブラウザに配られる前提の値）。守っているのは firestore.rules と API 側のメンバー確認。
// 接続先は「画面を開いたホスト名」だけで決まる。画面・API・Firestore が別のプロジェクトに混ざらないように、1 つのホストは 1 つの組だけを使う。

const TEAM = {
  firebaseConfig: {
    apiKey: "AIzaSyD3bKPbi_VzbkLhGq0ssZwhi7HnvBn8Zp4",
    authDomain: "unsaid-devfest.firebaseapp.com",
    projectId: "unsaid-devfest",
    storageBucket: "unsaid-devfest.firebasestorage.app",
    messagingSenderId: "799163399675",
    appId: "1:799163399675:web:82ff5e00debd34473d1f5e",
  },
  api: "https://unsaid-api-799163399675.asia-northeast1.run.app",
  db: "(default)",
};

// チームのプロジェクトのステージング: Hosting のプレビューチャンネル（unsaid-devfest--<チャンネル名>-<ランダム>.web.app）で開いたときだけ。
// 本番（unsaid-devfest.web.app）とは API（Cloud Run の別サービス）も Firestore（別の名前付きデータベース）も分ける。手順は docs/staging.md の A
const TEAM_STAGING = {
  ...TEAM,
  api: "https://unsaid-api-staging-799163399675.asia-northeast1.run.app",
  db: "staging",
};

// 独立ステージング（自分のプロジェクト kumu-devfest-2026。docs/staging.md の B）。チームのプロジェクトとは何も共有しない。
// 値はプロジェクトに Firebase のウェブアプリを登録した後に入れる（空のままこのホストで開くと、下で止める）
const KUMU_STAGING = {
  firebaseConfig: {
    apiKey: "",
    authDomain: "kumu-devfest-2026.firebaseapp.com",
    projectId: "kumu-devfest-2026",
    storageBucket: "",
    messagingSenderId: "",
    appId: "",
  },
  api: "",
  db: "(default)",
  gate: true,  // 参加コードで問診ごとに解錠する（main.py の STAGING_ACCESS_SHA256）
};

export function pick(hostname) {
  if (hostname === "kumu-devfest-2026.web.app" || hostname === "kumu-devfest-2026.firebaseapp.com"
      || hostname.startsWith("kumu-devfest-2026--")) return { name: "kumu-staging", ...KUMU_STAGING };
  if (hostname.startsWith("unsaid-devfest--")) return { name: "team-staging", ...TEAM_STAGING };
  return { name: "team", ...TEAM };
}

const target = pick(location.hostname);
if (!target.firebaseConfig.apiKey || !target.api) {
  throw new Error(`接続先「${target.name}」の設定が空です（web/firebase-config.js）`);
}

export const TARGET = target.name;
export const firebaseConfig = target.firebaseConfig;
export const API_URL = target.api;
export const FIRESTORE_DB = target.db;
export const STAGING = target.name !== "team";
export const ACCESS_GATE = target.gate === true;
