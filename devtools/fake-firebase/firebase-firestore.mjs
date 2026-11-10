// Firestore の偽物（サーバー側の状態だけを持つ。ローカルキャッシュは無い）。
// ルールは firestore.rules のうち、web/unsaid.js の receive / joinSession に関わる部分だけを同じ条件で断る。
// runTransaction は本物（Firestore エミュレーターで確かめた動き）と同じ順に判定する: 書き込みのときに、まずルールを
// その時点の文書で評価し（通らなければ PERMISSION_DENIED。やり直さない）、通れば、読んだ後に文書が変わっていたら
// トランザクションをやり直す。直列に実行したときと同じ結果になる、という前提は置かない。
const S = (globalThis.__fakeFs ??= { docs: new Map(), writes: [], denied: [] });
const deny = (path) => { S.denied.push(path); return Object.assign(new Error("permission-denied"), { code: "permission-denied" }); };
const tick = () => new Promise((r) => setTimeout(r, 1)); // 通信の往復

export const getFirestore = () => ({});
export const collection = (db, ...p) => ({ path: p.join("/") });
export const doc = (db, ...p) => ({ path: p.join("/"), id: p.at(-1) });
export const query = (c) => c;
export const orderBy = () => null;
export const onSnapshot = () => () => {};
export const serverTimestamp = () => "TS";
export const arrayUnion = (...xs) => ({ __union: xs });
const snap = (ref) => {
  const d = S.docs.get(ref.path);
  return { id: ref.id, exists: () => d !== undefined, data: () => (d ? structuredClone(d) : undefined) };
};
export const getDoc = async (ref) => { await tick(); return snap(ref); };
export const getDocFromServer = getDoc; // 偽物にはキャッシュが無いので同じ

function apply(ref, change, { dryRun = false } = {}) {
  const cur = S.docs.get(ref.path);
  if (!cur) throw deny(ref.path);
  if (ref.path.includes("/messages/")) {
    // 医師: 送信済み（sent）の患者の発言だけを received にできる
    if (!(cur.from === "patient" && cur.status === "sent" && change.status === "received")) throw deny(ref.path);
  } else if ("patientLang" in change && cur.patientLang != null) {
    throw deny(ref.path); // patientLang は null のときに1回だけ
  }
  if (dryRun) return;
  const next = { ...cur };
  for (const [k, v] of Object.entries(change)) next[k] = v?.__union ? [...new Set([...(cur[k] ?? []), ...v.__union])] : v;
  S.docs.set(ref.path, next);
  bump(ref.path);
  S.writes.push({ path: ref.path, change });
}
export async function updateDoc(ref, change) { await tick(); apply(ref, change); }
export const addDoc = async () => { throw new Error("not used"); };

const ver = new Map(); // 文書ごとの版（書き込みのたびに増える）
const bump = (path) => ver.set(path, (ver.get(path) ?? 0) + 1);
export async function runTransaction(db, fn) {
  for (let attempt = 0; attempt < 5; attempt++) {
    const ops = [], seen = new Map();
    const out = await fn({
      get: async (ref) => { await tick(); seen.set(ref.path, ver.get(ref.path) ?? 0); return snap(ref); },
      update: (ref, c) => ops.push([ref, c]),
    });
    await tick();
    // 1) ルール（その時点の文書で）。2) 読んだ後に変わっていたら、やり直し
    const stale = [...seen].some(([p, v]) => (ver.get(p) ?? 0) !== v);
    if (ops.length && stale) {
      for (const [ref, c] of ops) apply(ref, c, { dryRun: true }); // 通らなければここで PERMISSION_DENIED
    }
    if (stale) continue;
    for (const [ref, c] of ops) apply(ref, c);
    return out;
  }
  throw Object.assign(new Error("aborted"), { code: "aborted" });
}
