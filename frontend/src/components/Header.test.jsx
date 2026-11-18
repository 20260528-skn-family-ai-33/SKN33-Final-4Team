// 뒤로 가기 (11/18 검수): 주소로 바로 열어 앞 화면 기록이 없으면 backTo, 없으면 홈으로 — 눌러도 반응이 없거나 앱 밖으로 나가지 않게
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, it } from 'vitest'
import Header from './Header.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
afterEach(async () => { await act(() => root.unmount()); host.remove() })

const mount = (entries, backTo) => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  return act(() => root.render(<MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}><Routes>
    <Route path="/" element={<p>홈 화면</p>} />
    <Route path="/budgets" element={<p>예산 화면</p>} />
    <Route path="/my" element={<Header title="마이" showBack backTo={backTo} />} />
  </Routes></MemoryRouter>))
}
const back = () => act(async () => host.querySelector('[aria-label="뒤로"]').click())

it('바로 연 화면은 backTo 가 없으면 홈으로', async () => {
  await mount(['/my'])
  await back()
  expect(host.textContent).toBe('홈 화면')
})

it('바로 연 화면은 backTo 로', async () => {
  await mount(['/my'], '/budgets')
  await back()
  expect(host.textContent).toBe('예산 화면')
})
