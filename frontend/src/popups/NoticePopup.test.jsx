// 종 알림 묶음 (11/18 시안 N3): 지금 해 볼 일 · 확인해 주세요 · 안내
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import NoticePopup from './NoticePopup.jsx'

vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup: vi.fn() }) }))
vi.mock('./Popup.jsx', () => ({ default: ({ children }) => <div>{children}</div> }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('해 볼 일 · 확인 · 안내로 묶고, 해 볼 일에는 행동 글자를 붙인다', async () => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  const go = vi.fn()
  await act(() => root.render(<NoticePopup items={[
    { key: 'p', title: '카테고리 확인', count: 3, text: '거래 3건', go },
    { key: 'd', title: '머니쿠션 적립', text: '쿠션 모으기 잊지 마세요!', go, group: 'act', act: '쿠션 확인하기', tone: 'lime' },
    { key: 'w', title: '월급 입금', text: '월급이 아직 안 들어왔어요' },
  ]} />))
  expect([...host.querySelectorAll('.mc-notice-grp')].map((h) => h.textContent)).toEqual(['지금 해 볼 일', '확인해 주세요', '안내'])
  expect(host.querySelector('.mc-notice-act').textContent).toBe('쿠션 확인하기 ›')
  expect(host.querySelector('.mc-tone-lime')).not.toBeNull()
  await act(async () => host.querySelector('.mc-notice-act').closest('button').click())
  expect(go).toHaveBeenCalled()
})

it('묶음이 하나뿐이면 머리글을 생략한다', async () => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(() => root.render(<NoticePopup items={[{ key: 'p', text: '거래 3건', go: vi.fn() }]} />))
  expect(host.querySelector('.mc-notice-grp')).toBeNull()
})
