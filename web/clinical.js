// 医師画面の「問診メモ」「確認候補」「カルテ用の文章」を、発言の一覧から作る（DOM にも Firebase にも触らない）。
// 状態はサーバー（clinical.py）が明示的なイベントで変えたものをそのまま読む。ここでは並べ替えと表示の形だけを決める。
// node --test web/clinical.test.mjs でテストする。

export const CATEGORY_LABEL = {
  chief_complaint: "主訴", symptom: "症状", associated_symptom: "関連症状", onset: "いつから", duration: "持続",
  frequency: "頻度", course: "経過", quality: "性質", location: "部位", severity: "程度", modifying_factor: "増悪・軽快",
  history: "既往歴", medication: "服薬", allergy: "アレルギー", background: "背景", psychological: "本人の気持ち",
  other: "その他",
};
export const STATUS_LABEL = {
  ai_extracted: "AI抽出", unconfirmed: "未確認", patient_answered: "患者回答あり",
  doctor_confirmed: "医師確認済み", needs_correction: "要修正",
};
export const EVENT_LABEL = {
  extracted: "AIが抽出", ambiguity_found: "AIが曖昧さを指摘", question_sent: "確認質問を送信",
  patient_answered: "患者が回答", patient_unsure: "患者は確定できず", doctor_confirm: "医師が確認",
  doctor_flag: "医師が要修正に", doctor_reset: "医師が取り消し", candidate_dismissed: "医師が確認を省略",
};
const SYMPTOMS = new Set(["chief_complaint", "symptom", "associated_symptom"]);
export const ATTR_ORDER = ["onset", "duration", "frequency", "course", "quality", "location", "severity", "modifying_factor"];
const OTHER_ORDER = ["history", "medication", "allergy", "background", "psychological", "other"];
// 「言及なし」を出す項目（問診で聞き漏らしやすいもの）
export const MUST_ASK = ["history", "medication", "allergy"];
const LEGACY_CATEGORY = { symptom: "symptom", onset: "onset", frequency: "frequency", severity: "severity",
  medication: "medication", allergy: "allergy" };

export const isShown = (m) => ["sent", "received"].includes(m.status);

// ── USSA: 訳の検証の表示・未解決事項 ──
// 訳の照合の結果（Verification Engine）。VERIFIED は「検出した相違なし」で、正しさの証明ではない
export function verificationOf(m) {
  const g = m.gate;
  if (!g) return null;
  const status = g.verification?.status;
  if (g.override === "speaker_ack") return { level: "warn", label: "訳は未検証", status };
  if (status === "AMBIGUOUS") return { level: "soft", label: "訳の一部が未照合", status };
  if (status === "VERIFIED") return { level: "ok", label: "訳の照合: 相違なし", status };
  return null; // PRETRANSLATED（選択肢の対訳）など
}
export const unverified = (m) => m.gate?.override === "speaker_ack";

export const ISSUE_LABEL = {
  contradiction: "前の発言との矛盾", understanding_mismatch: "理解の相違", unanswered_question: "未回答の質問",
  unresolved_clarification: "未解決の確認", quantity_time_mismatch: "数量・日時の食い違い",
  insufficient_explanation: "説明に足りない点", inconsistent_with_past: "前の発言との食い違い",
  meaning_drift: "確認した意味の変化", instruction_misunderstanding: "指示の理解の相違",
};
export const ISSUE_STATUS_LABEL = { OPEN: "", CLARIFICATION_REQUESTED: "確認中", ANSWERED: "回答あり" };
const ACTIVE = ["OPEN", "CLARIFICATION_REQUESTED", "ANSWERED"];
const SEV = { high: 0, medium: 1, low: 2 };
export function activeIssues(audit) {
  const all = (audit?.issues ?? []).filter((i) => ACTIVE.includes(i.status))
    .sort((a, b) => (SEV[a.severity] ?? 1) - (SEV[b.severity] ?? 1));
  return { open: all.filter((i) => !i.held), held: all.filter((i) => i.held) };
}
const key = (t) => (t ?? "").replace(/[。、．，,.!！?？\s（）()「」]/g, "");

// 発言の facts（新しい meaning.facts。無ければ旧い facts を同じ形にする）
export function factsOf(m) {
  if (m.meaning?.facts) return m.meaning.facts;
  // 従来の流れ（CLARIFY_MODE=patient）: 送信前に患者が答えた語を含む項目は、本人の答え（日本語）で置き換える
  const c = m.clarification;
  return (m.facts ?? []).map((f, i) => {
    const answered = Boolean(c?.answer && c.answer.type !== "unsure" && c.target && f.text.includes(c.target)
      && m.translation?.clarified_answer);
    return {
      fact_id: f.fact_id ?? `legacy${i}`, utterance_id: m.id, category: LEGACY_CATEGORY[f.kind] ?? "other",
      normalized_meaning: f.doctor_gloss || f.text, source_quote: f.text, span_start: -1, span_end: -1,
      evidence_status: "legacy", polarity: f.negated ? "denied" : f.uncertain && !answered ? "uncertain" : "present",
      status: answered ? "patient_answered" : "ai_extracted", clarification_status: answered ? "answered" : "none",
      resolved_meaning: answered ? m.translation.clarified_answer : "", history: [], legacy: true,
    };
  });
}

// 表示する文: 患者が確認質問に答えたら「元の言い方：答え」
export function factText(f) {
  const r = f.resolved_meaning;
  if (!r) return f.normalized_meaning;
  return r.includes(f.normalized_meaning) ? r : `${f.normalized_meaning}：${r}`;
}

// 問診メモ。症状ごとに属性（いつから・経過など）をまとめる。同じ症状・同じ属性を言い直したら最新を出し、前の値は prev に残す
export function clinicalMemo(msgs) {
  const symptoms = []; // {item, attrs: Map(category → item)}
  const others = new Map(); // category → [item]
  const item = (f, m) => ({ text: factText(f), fact: f, msg: m, ref: `${m.id}:${f.fact_id}`, prev: [] });
  const upsert = (list, it) => {
    const i = list.findIndex((x) => key(x.fact.normalized_meaning) === key(it.fact.normalized_meaning));
    if (i < 0) { list.push(it); return it; }
    const old = list[i];
    list[i] = { ...it, prev: key(old.text) !== key(it.text) ? [...old.prev, old.text] : old.prev, attrs: old.attrs };
    return list[i];
  };
  for (const m of msgs) {
    if (m.from !== "patient" || !isShown(m)) continue;
    const facts = factsOf(m);
    const mine = new Map(); // fact_id → symptom entry（この発言の症状）
    for (const f of facts.filter((f) => SYMPTOMS.has(f.category))) {
      const it = upsert(symptoms, { ...item(f, m), attrs: undefined });
      it.attrs ??= new Map();
      it.chief = it.chief || f.category === "chief_complaint";
      mine.set(f.fact_id, it);
    }
    for (const f of facts.filter((f) => ATTR_ORDER.includes(f.category))) {
      const target = mine.get(f.about_fact_id) ?? [...mine.values()][0] ?? symptoms.at(-1);
      const it = item(f, m);
      if (!target) { others.set("other", [...(others.get("other") ?? []), it]); continue; }
      // 同じ属性は1つの発言の中なら並べ、別の発言で言い直したら置き換える（前の値は prev）
      const list = target.attrs.get(f.category);
      if (list && list[0].msg.id === m.id) list.push(it);
      else target.attrs.set(f.category, [{ ...it, prev: list ? list.map((x) => x.text) : [] }]);
    }
    for (const f of facts.filter((f) => !SYMPTOMS.has(f.category) && !ATTR_ORDER.includes(f.category))) {
      const list = others.get(f.category) ?? [];
      upsert(list, item(f, m));
      others.set(f.category, list);
    }
  }
  symptoms.sort((a, b) => Number(Boolean(b.chief)) - Number(Boolean(a.chief)));
  const count = symptoms.length + [...others.values()].flat().length;
  const notMentioned = count ? MUST_ASK.filter((c) => !others.has(c)) : [];
  return { symptoms, others, count, otherOrder: OTHER_ORDER.filter((c) => others.has(c)), notMentioned };
}

// 確認候補（送信済みの患者の発言の meaning.candidates）。重要なもの（priority 順）から
const PRI = { high: 0, medium: 1, low: 2 };
export function candidates(msgs) {
  const all = [];
  for (const m of msgs) {
    if (m.from !== "patient" || !isShown(m)) continue;
    for (const c of m.meaning?.candidates ?? []) all.push({ ...c, mid: m.id, msg: m });
  }
  const by = (st) => all.filter((c) => st.includes(c.status)).sort((a, b) => PRI[a.priority] - PRI[b.priority]);
  return {
    open: by(["proposed", "unresolved"]), // 医師が選ぶもの（unresolved は「分からない」と答えた → 聞き直すか省くか）
    held: by(["held"]),
    asked: by(["asked"]), // 回答待ち
    done: by(["answered", "dismissed"]),
  };
}

// 理解の確認: 説明（医師の発言）ごとの最新の依頼と、その答え
export function teachbacks(msgs) {
  const byExp = new Map();
  for (const m of msgs) {
    if (m.kind !== "teachback") continue;
    byExp.set(m.teachback.explanation_id, m); // 古い順に並んでいるので、最後が最新
  }
  return byExp;
}
export const answersTo = (msgs, qid) => msgs.filter((m) => m.replyTo === qid && m.from === "patient");

export const TB_LABEL = {
  inconsistent: "認識の相違の可能性", partial: "一部が伝わっていない可能性", unclear: "判定できない部分あり",
  consistent: "説明と一致", error: "自動の比較ができませんでした",
};

// ── カルテ用の文章（医師が編集してから貼る）。問診メモと同じデータから作り、原文に無い診断名・検査結果は足さない ──
const MARK = { doctor_confirmed: "［医］", patient_answered: "［患］", unconfirmed: "［未］", needs_correction: "［要修正］" };
const pol = (f) => (f.polarity === "denied" ? "（なし）" : f.polarity === "uncertain" ? "（本人も不確か）" : "");
const line = (it) => `${it.text}${pol(it.fact)}${MARK[it.fact.status] ?? ""}${unverified(it.msg) ? "［訳未検証］" : ""}`;

export function karteText(msgs, session, today = new Date(), audit = session?.audit) {
  const memo = clinicalMemo(msgs);
  const cands = candidates(msgs);
  const tbs = teachbacks(msgs);
  const langName = { en: "英語", "zh-CN": "中国語", vi: "ベトナム語" }[session?.patientLang] ?? session?.patientLang ?? "";
  if (!memo.count && !tbs.size) return "";
  const L = [`【問診記録（UNSAID）】${langName}話者・${today.toLocaleDateString("ja-JP")}`,
    "※患者の申告を機械翻訳と AI で整理したもの（診断ではない）。［医］医師確認済み［患］確認質問への患者回答［未］未確認［要修正］医師が要修正とした項目。印なしは AI 抽出（医師未確認）。"];
  const chief = memo.symptoms.filter((s) => s.chief);
  if (memo.symptoms.length) {
    L.push("", "■主訴");
    L.push(chief.length ? chief.map(line).join("、") : `${line(memo.symptoms[0])}（主な訴え）`);
    L.push("", "■現病歴（患者の申告）");
    for (const s of memo.symptoms) {
      L.push(`・${line(s)}`);
      for (const a of ATTR_ORDER) {
        const xs = s.attrs?.get(a);
        if (xs?.length) L.push(`　${CATEGORY_LABEL[a]}：${xs.map(line).join("／")}`);
      }
    }
  }
  const confirmed = cands.done.filter((c) => c.status === "answered");
  if (confirmed.length) {
    L.push("", "■確認した内容（確認質問と患者の回答）");
    for (const c of confirmed) {
      const a = c.answers.at(-1);
      L.push(`・「${c.source_quote}」（${c.meaning_doctor}）→ ${a?.meaning_doctor ?? ""}`);
    }
  }
  const pending = [
    ...cands.open.map((c) => `・「${c.source_quote}」：${c.unresolved || c.meaning_doctor}${c.status === "unresolved" ? "（患者は確定できず）" : "（未確認）"}`),
    ...cands.held.map((c) => `・「${c.source_quote}」：${c.unresolved || c.meaning_doctor}（保留）`),
    ...cands.asked.map((c) => `・「${c.source_quote}」：${c.unresolved || c.meaning_doctor}（回答待ち）`),
    ...cands.done.filter((c) => c.status === "dismissed").map((c) => `・「${c.source_quote}」：${c.unresolved || c.meaning_doctor}（医師の判断で確認を省略）`),
  ];
  const flagged = [...memo.symptoms, ...[...memo.others.values()].flat()].filter((it) => it.fact.status === "needs_correction");
  const iss = activeIssues(audit);
  const issues = [...iss.open, ...iss.held].map((i) =>
    `・${ISSUE_LABEL[i.type] ?? i.type}：${i.description}${i.held ? "（保留）" : ""}${ISSUE_STATUS_LABEL[i.status] ? `（${ISSUE_STATUS_LABEL[i.status]}）` : ""}`);
  if (pending.length || flagged.length || issues.length) {
    L.push("", "■未確認事項");
    L.push(...pending, ...flagged.map((it) => `・${it.text}（要修正）`), ...issues);
  }
  if (memo.otherOrder.length || memo.notMentioned.length) {
    L.push("", "■背景・既往・服薬・アレルギーなど");
    for (const c of memo.otherOrder) L.push(`・${CATEGORY_LABEL[c]}：${memo.others.get(c).map(line).join("、")}`);
    if (memo.notMentioned.length) L.push(`・言及なし：${memo.notMentioned.map((c) => CATEGORY_LABEL[c]).join("・")}`);
  }
  if (tbs.size) {
    L.push("", "■説明と理解の確認（teach-back）");
    const byId = new Map(msgs.map((m) => [m.id, m]));
    for (const [expId, q] of tbs) {
      const exp = byId.get(expId);
      const r = q.teachback.result;
      // AI の判定と医師の判断を分けて書く（AI の判定だけで「理解した」とはしない）
      const ai = r ? `${TB_LABEL[r.overall]}（AI判定）` : "回答待ち";
      const doc = { doctor_confirmed: "医師が理解を確認", needs_reexplain: "医師: 再説明が必要" }[q.teachback.status] ?? "医師未確認";
      L.push(`・説明「${exp?.original ?? ""}」→ ${ai}／${doc}`);
      for (const p of r?.points ?? []) if (p.verdict !== "match") L.push(`　${p.explanation}`);
    }
  }
  return L.join("\n");
}
