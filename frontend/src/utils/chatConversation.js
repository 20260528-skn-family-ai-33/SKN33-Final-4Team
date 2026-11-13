const KEY = 'mc-chat-conversation'

export function savedId() {
  try { return sessionStorage.getItem(KEY) } catch { return null }
}

export function saveId(id) {
  try { if (id) sessionStorage.setItem(KEY, id); else sessionStorage.removeItem(KEY) } catch { /* 저장소 없이도 대화 가능 */ }
}
