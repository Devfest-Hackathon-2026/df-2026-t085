// 端末ごとの uid は globalThis.__fakeUid（テストが切り替える）
// 医師のログイン画面は飛ばす（偽物では誰でもメール＋パスワードでログイン済みとして扱う）
const user = () => ({ uid: globalThis.__fakeUid ?? "u1", providerData: [{ providerId: "password" }], getIdToken: async () => "t" });
export const getAuth = () => ({ authStateReady: async () => {}, get currentUser() { return user(); } });
export const signInAnonymously = async () => ({ user: user() });
export const signInWithEmailAndPassword = async () => ({ user: user() });
export const signOut = async () => {};
