// Firebase と API の接続先。ここだけ書き換えれば web/unsaid.js がそのまま使える。
// firebaseConfig は Firebase コンソール → プロジェクトの設定（歯車）→ 全般 →「マイアプリ」のウェブアプリ（</>）にある。
// この値は秘密ではない（ブラウザに配られる前提の値）。守っているのは firestore.rules と API 側のメンバー確認。
export const firebaseConfig = {
  apiKey: "ここに apiKey",
  authDomain: "unsaid-devfest.firebaseapp.com",
  projectId: "unsaid-devfest",
  storageBucket: "unsaid-devfest.firebasestorage.app",
  messagingSenderId: "ここに messagingSenderId",
  appId: "ここに appId",
};

export const API_URL = "https://unsaid-api-799163399675.asia-northeast1.run.app";
