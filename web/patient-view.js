// 患者の「話す」画面に、どの医師の発言を出し、どの質問に答えるかを決める（DOM にも Firebase にも触らない）。
// patient.html の view() が使う。node --test web/ でテストする。
//
// 質問（確認質問・理解の確認）は、質問ごとに「答えが届いたか」で判定する。
// 届いた答え = その質問を replyTo に持つ自分の発言のうち、送信済み（sent / received）のもの。
// 解析中・意図や照合の確認待ち・取り消し・失敗した答えは、答えとして数えない（質問は開いたまま）。

const SHOWN = ["sent", "received"];
const isShown = (m) => SHOWN.includes(m.status);
export const isQuestion = (m) => m.from === "doctor" && (m.kind === "clarify" || m.kind === "teachback");

// 答えが届いた質問の id
export function answeredIds(msgs) {
  return new Set(msgs.filter((m) => m.from === "patient" && m.replyTo && isShown(m)).map((m) => m.replyTo));
}

// msgs: watchMessages の一覧（古い順）。reopen: 「回答を直す」で開き直した質問の id
export function speakState(msgs, reopen = null) {
  const mine = msgs.filter((m) => m.from === "patient");
  const lastSent = mine.filter(isShown).at(-1) ?? null;
  const after = lastSent ? msgs.indexOf(lastSent) : -1;
  const answered = answeredIds(msgs);
  // まだ答えが届いていない、届いた質問（前に送られたものも含む）
  const open = msgs.filter((m) => isQuestion(m) && isShown(m) && (!answered.has(m.id) || m.id === reopen));
  const openIds = new Set(open.map((m) => m.id));
  // 出す医師の発言 = 自分が最後に送った後のもの（続けて2つ聞かれても1つ目が消えない）＋ それより前の未回答の質問
  const doctors = msgs.filter((m, i) => m.from === "doctor" && isShown(m) && (i > after || openIds.has(m.id)));
  // 文字や声で答えるときの宛先 = 未回答の質問のうち一番新しいもの（選択肢はそれぞれの質問のボタンで答える）
  const openQ = open.at(-1) ?? null;
  // 「回答を直す」: 最後に送ったのが確認質問への答えで、その後に医師が何も言っておらず、開いた質問も無いとき
  const q = lastSent?.replyTo ? msgs.find((m) => m.id === lastSent.replyTo) : null;
  const answeredQ = !doctors.length && q?.kind === "clarify" ? q : null;
  return { doctors, openIds, openQ, answeredQ, lastSent: doctors.length ? null : lastSent };
}
