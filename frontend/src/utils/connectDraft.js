// 계좌 연결 첫 화면에서 고른 것(대출 답 · 은행 · 카드사 · 동의 체크)을 이 탭에 잠깐 남겨 둔다 (11/12 피드백)
// [선택한 기관 연결하기]를 누르지 않고 뒤로 갔다가 [저장하고 계속]으로 다시 들어와도, 기관 로그인에서 뒤로 와도 그대로 채운다
// 서버에는 저장하지 않는다. 30분이 지나면 버리고, 로그아웃하면 지운다 (다른 사람이 같은 탭에서 보지 않게)
// 브라우저 저장소를 못 쓰면(사생활 보호 모드 등) 조용히 넘어가고 예전처럼 빈 화면부터
const KEY = 'mc-connect-draft'
const TTL_MS = 30 * 60 * 1000

export function readConnectDraft() {
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(KEY))
    if (!saved || typeof saved.at !== 'number' || Date.now() - saved.at > TTL_MS) return null
    return saved.draft ?? null
  } catch {
    return null
  }
}

export function saveConnectDraft(draft) {
  try { window.sessionStorage.setItem(KEY, JSON.stringify({ at: Date.now(), draft })) } catch { /* 못 남겨도 화면은 그대로 동작 */ }
}

export function clearConnectDraft() {
  try { window.sessionStorage.removeItem(KEY) } catch { /* 없으면 그만 */ }
}
