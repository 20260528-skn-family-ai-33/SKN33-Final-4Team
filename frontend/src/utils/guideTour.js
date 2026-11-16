// 쿠숑이 가이드 투어를 봤는지 (11/16).
// 서버가 GET /api/auth/me/ 에 guideTourSeen(참 · 거짓)을 주면 그것을 따르고, 다 보면 POST /api/auth/me/guide-tour/ 로 남긴다.
// 서버 칸이 아직 없으면(회원 모델 마이그레이션 전 — 김나은 담당) 이 브라우저에만 남긴다. 그때는 다른 기기에서 한 번 더 보일 수 있다.
// 마이메뉴 [쿠숑이 가이드 다시 보기] = 이 탭(sessionStorage)에 표시해 두고 홈으로 → 홈이 한 번 읽고 지운다
import { apiSend } from './api.js'

const seenKey = (userId) => `moneycushion.guideTour.${userId}`
const REPLAY = 'moneycushion.guideTour.replay'

// 첫 예산을 승인한 회원이 아직 안 봤으면 참
export function needsGuideTour(me) {
  if (!me || me.onboardingCompleted !== true) return false
  if (typeof me.guideTourSeen === 'boolean') return !me.guideTourSeen
  if (!me.userId) return false
  try {
    return !window.localStorage.getItem(seenKey(me.userId))
  } catch {
    return false  // 저장소가 막혀 있으면 매번 띄우지 않는다
  }
}

export function askGuideReplay() {
  try { window.sessionStorage.setItem(REPLAY, '1') } catch { /* 막혀 있으면 다시 보기만 안 뜬다 */ }
}

export function takeGuideReplay() {
  try {
    const asked = window.sessionStorage.getItem(REPLAY)
    if (asked) window.sessionStorage.removeItem(REPLAY)
    return Boolean(asked)
  } catch {
    return false
  }
}

export async function markGuideTourSeen(me) {
  if (me?.userId) {
    try { window.localStorage.setItem(seenKey(me.userId), new Date().toISOString()) } catch { /* 서버 기록이 있으면 그걸로 충분 */ }
  }
  if (me?.guideTourSeen === false) {
    try { await apiSend('/api/auth/me/guide-tour/', 'POST', {}) } catch { /* 못 남겨도 이번 화면은 닫는다 (다음에 한 번 더 보일 뿐) */ }
  }
}
