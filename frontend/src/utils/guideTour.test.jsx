import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { apiSend } from './api.js'
import { askGuideReplay, markGuideTourSeen, needsGuideTour, takeGuideReplay } from './guideTour.js'

vi.mock('./api.js', () => ({ apiSend: vi.fn() }))
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks() })
afterEach(() => { localStorage.clear(); sessionStorage.clear() })

const me = { userId: 'u1', onboardingCompleted: true }

it('첫 예산을 승인한 회원만, 아직 안 봤을 때 보여 준다 (서버 칸이 없으면 이 브라우저 기록)', async () => {
  expect(needsGuideTour({ ...me, onboardingCompleted: false })).toBe(false)
  expect(needsGuideTour(null)).toBe(false)
  expect(needsGuideTour(me)).toBe(true)
  await markGuideTourSeen(me)
  expect(needsGuideTour(me)).toBe(false)
  expect(needsGuideTour({ ...me, userId: 'u2' })).toBe(true)  // 다른 회원은 따로
  expect(apiSend).not.toHaveBeenCalled()  // 서버 칸이 없으면 서버에 보내지 않는다
})

it('서버가 guideTourSeen 을 주면 서버를 따르고, 다 보면 서버에 남긴다', async () => {
  expect(needsGuideTour({ ...me, guideTourSeen: true })).toBe(false)
  expect(needsGuideTour({ ...me, guideTourSeen: false })).toBe(true)
  await markGuideTourSeen({ ...me, guideTourSeen: false })
  expect(apiSend).toHaveBeenCalledWith('/api/auth/me/guide-tour/', 'POST', {})
})

it('마이메뉴 [다시 보기] 표시는 홈이 한 번만 읽는다', () => {
  expect(takeGuideReplay()).toBe(false)
  askGuideReplay()
  expect(takeGuideReplay()).toBe(true)
  expect(takeGuideReplay()).toBe(false)
})
