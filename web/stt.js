// Kumu の音声入力（画面側）。押して話す → 離すと 16kHz モノラル WAV にして POST /transcribe → 文字が返る。
// 文字は Firestore に書かない。画面の入力欄に入れて、患者が見て（必要なら直して）から say() で発言を作る。
// 音声は端末のメモリにだけあり、送った後は捨てる（保存しない）。
//
// 使い方:
//   import { Recorder, transcribe } from "./stt.js";
//   const rec = new Recorder();
//   button.onpointerdown = () => rec.start();            // 最初の1回だけマイクの許可ダイアログが出る
//   button.onpointerup = async () => {
//     const wav = await rec.stop();
//     const { text } = await transcribe(API_URL, await auth.currentUser.getIdToken(), sessionId, "patient", wav);
//     input.value = text;                               // → 患者が確認して「送る」→ say(sessionId, "patient", input.value)
//   };

export const MAX_SECONDS = 30; // サーバー側の上限（stt.MAX_SECONDS）と同じ
const RATE = 16000; // Speech-to-Text の推奨。WAV でも 1 秒 32KB なので 30 秒で 1MB 弱

export class Recorder {
  constructor() {
    this.stream = null;
    this.media = null;
    this.chunks = [];
    this.timer = null;
  }

  async start() {
    if (this.media) return;
    // getUserMedia は https か localhost でしか動かない
    this.stream ??= await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    this.chunks = [];
    this.media = new MediaRecorder(this.stream); // 形式はブラウザ任せ（Chrome: webm/opus、Safari: mp4/aac）
    this.media.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.media.start();
    this.timer = setTimeout(() => this.media?.state === "recording" && this.media.stop(), MAX_SECONDS * 1000);
  }

  // 録音を止めて WAV（Blob）を返す。短すぎて何も録れていなければ null
  async stop() {
    const media = this.media;
    if (!media) return null;
    clearTimeout(this.timer);
    if (media.state === "recording") {
      await new Promise((ok) => {
        media.onstop = ok;
        media.stop();
      });
    }
    this.media = null;
    const blob = new Blob(this.chunks, { type: media.mimeType });
    this.chunks = [];
    if (!blob.size) return null;
    return toWav16k(blob);
  }

  // 画面を離れるとき。マイクのランプを消す
  close() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}

// ブラウザごとに違う録音形式を、どこでも同じ 16kHz モノラル WAV にそろえる
// （Speech-to-Text が Safari の mp4 を読めない問題を端末側で無くす）
async function toWav16k(blob) {
  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  ctx.close();
  const frames = Math.ceil(decoded.duration * RATE);
  const off = new OfflineAudioContext(1, frames, RATE);
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start();
  const pcm = (await off.startRendering()).getChannelData(0);

  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, RATE, true); v.setUint32(28, RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

// speaker は "patient" か "doctor"。言語はサーバーが session から決める。
// 返り値 { text, lang }。text が "" なら「聞き取れませんでした」と出して、もう一度か文字入力に誘導する
export async function transcribe(apiUrl, idToken, sessionId, speaker, wav) {
  const q = new URLSearchParams({ sessionId, speaker });
  const res = await fetch(`${apiUrl}/transcribe?${q}`, {
    method: "POST",
    headers: { "Content-Type": "audio/wav", Authorization: `Bearer ${idToken}` },
    body: wav,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `transcribe ${res.status}`);
  return body;
}
