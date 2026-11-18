import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import CushionPage from './CushionPage.jsx'
import { useApi } from '../utils/api.js'

vi.mock('../utils/api.js', () => ({ useApi: vi.fn() }))
vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children }) => <main>{children}</main> }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup: vi.fn() }) }))

it('쿠션 금액은 계속 보여 주고 저축 계좌 조회 누락, 빈 목록, 잔액이 있는 계좌를 구분한다', async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const host = document.createElement('div')
  const root = createRoot(host)
  const reload = vi.fn()
  const render = async (savings) => {
    useApi.mockReturnValue({ data: { cushion: { now: 123000 }, savings }, reload })
    await act(() => root.render(<CushionPage />))
  }
  try {
    await render(undefined)
    expect(host.textContent).toContain('123,000원')
    expect(host.textContent).not.toContain('연결된 저축 계좌가 없어요')
    await act(() => host.querySelector('[role=alert] button').click())
    expect(reload).toHaveBeenCalledOnce()
    await render({ items: [], total: null })
    expect(host.textContent).toContain('연결된 저축 계좌가 없어요')
    await render({ items: [{ accountId: 'a', displayName: '청년적금', balance: 765432 }], total: 765432 })
    expect(host.textContent).toContain('청년적금765,432원')
    expect(host.querySelector('[role=alert]')).toBeNull()
  } finally {
    await act(() => root.unmount())
  }
})
