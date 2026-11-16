import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SalaryBasisResetPopup from './SalaryBasisResetPopup.jsx'
import { PATHS } from '../routes.js'

const { closePopup, toast, navigate } = vi.hoisted(() => ({ closePopup: vi.fn(), toast: vi.fn(), navigate: vi.fn() }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))
vi.mock('../utils/toast.js', () => ({ toast }))
vi.mock('react-router', () => ({ useNavigate: () => navigate }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const choose = (text) => act(async () => [...host.querySelectorAll('.mc-choice')].find((b) => b.querySelector('strong').textContent === text).click())
beforeEach(() => { vi.resetAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('[다시 짜기]는 지킬 돈을 등록 월급 기준 추천값으로 열고, [그대로 두기]는 토스트만 (11/13)', async () => {
  const onDone = vi.fn()
  await act(() => root.render(<SalaryBasisResetPopup onDone={onDone} />))
  expect(host.querySelector('h2').textContent).toBe('월급 확인을 취소했어요')
  await choose('다시 짜기')
  expect(navigate).toHaveBeenCalledWith(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })
  await choose('그대로 두기')
  expect(toast).toHaveBeenCalledWith('이번 예산은 그대로 둘게요')
  expect(onDone).toHaveBeenCalledOnce()
})
