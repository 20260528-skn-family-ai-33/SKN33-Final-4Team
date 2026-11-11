// 연결 상세 팝업 — 목록 API 한 항목만으로 계좌 · 카드 정보와 그 수단으로 나가는 돈을 보여 준다
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import AccountPopup from './AccountPopup.jsx'

vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup: vi.fn() }) }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
afterEach(async () => { await act(() => root.unmount()); host.remove() })
const render = async (props) => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(() => root.render(<AccountPopup {...props} />))
}

it('계좌: 전체 이름 · 끝 4자리 · 잔액 · 이 계좌로 나가는 고정지출', async () => {
  await render({ kind: 'account', orgName: '국민은행', related: [{ id: 'r1', type: 'fixed', name: '월세', amount: 500_000, line: '매월 25일 · 국민 급여통장 · ****5678' }],
    item: { displayName: '국민 아주 긴 주거래 우대통장 · ****5678', name: '아주 긴 주거래 우대통장', balance: 1_000, isSelected: true, status: 'connected', fetchedAt: null } })
  expect(host.querySelector('h1').textContent).toBe('국민 아주 긴 주거래 우대통장')
  expect(host.textContent).toContain('****5678')
  expect(host.textContent).toContain('1,000원')
  expect(host.querySelector('.mc-acct-due').innerHTML).toBe('매월 25일<br>500,000원')  // 날짜 줄 바꾼 뒤 금액
})

it('카드: 잔액 칸이 없고, 고정지출을 못 불러오면 그렇게 말한다', async () => {
  await render({ kind: 'card', orgName: '현대카드', related: null,
    item: { displayName: '현대카드M CHECK · ****1234', cardType: '국내전용', cardStatus: null, isSelected: false, status: 'relogin', fetchedAt: null } })
  expect(host.textContent).not.toContain('잔액')
  expect(host.textContent).toContain('다시 연결 필요')
  expect(host.textContent).toContain('불러오지 못했어요')
})
