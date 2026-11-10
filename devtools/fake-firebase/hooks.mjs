// node の module.registerHooks 用: web/unsaid.js が読む Firebase（gstatic の URL）と接続先の設定を、このフォルダの偽物に差し替える。
// web/unsaid-client.test.mjs が使う（本物の画面の部品を、ネットに出ずに node で動かすため）
export function resolve(spec, ctx, next) {
  const m = spec.match(/^https:\/\/www\.gstatic\.com\/firebasejs\/[\d.]+\/(firebase-[a-z]+)\.js$/);
  if (m) return { url: new URL(`./${m[1]}.mjs`, import.meta.url).href, shortCircuit: true };
  if (spec === "./firebase-config.js") return { url: new URL("./config.mjs", import.meta.url).href, shortCircuit: true };
  return next(spec, ctx);
}
