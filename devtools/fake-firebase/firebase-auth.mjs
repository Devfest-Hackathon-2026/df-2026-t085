// 端末ごとの uid は globalThis.__fakeUid（テストが切り替える）
const user = () => ({ uid: globalThis.__fakeUid ?? "u1", getIdToken: async () => "t" });
export const getAuth = () => ({ authStateReady: async () => {}, get currentUser() { return user(); } });
export const signInAnonymously = async () => ({ user: user() });
