import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SalaryGapPopup from './SalaryGapPopup.jsx'
import { apiSend } from '../utils/api.js'
import { PATHS } from '../routes.js'

const { closePopup, toast, navigate } = vi.hoisted(() => ({ closePopup: vi.fn(), toast: vi.fn(), navigate: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn() }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))
vi.mock('../utils/toast.js', () => ({ toast }))
vi.mock('react-router', () => ({ useNavigate: () => navigate }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const URL = '/api/budgets/salary/gap/'
const LESS = { diff: -300000, received: 2200000, registered: 2500000, basis: null }
const MORE = { ...LESS, diff: 300000, received: 2800000 }
const labels = () => [...host.querySelectorAll('.mc-choice strong')].map((el) => el.textContent)
const choose = (text) => act(async () => [...host.querySelectorAll('.mc-choice')].find((b) => b.querySelector('strong').textContent === text).click())
const open = async (gap, onDone = vi.fn()) => { await act(() => root.render(<SalaryGapPopup gap={gap} onDone={onDone} />)); return onDone }
beforeEach(() => { vi.resetAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('덜 들어오든 더 들어오든 선택지는 [이번 예산 다시 짜기] [그대로 두기] 둘 (11/13 정리)', async () => {
  await open(LESS)
  expect(host.querySelector('h2').textContent).toBe('월급이 300,000원 덜 들어왔어요')
  expect(labels()).toEqual(['이번 예산 다시 짜기', '그대로 두기'])
  await act(() => root.render(<SalaryGapPopup key="more" gap={MORE} />))
  expect(host.querySelector('h2').textContent).toBe('월급이 300,000원 더 들어왔어요')
  expect(labels()).toEqual(['이번 예산 다시 짜기', '그대로 두기'])
})

it('다시 짜기는 이번 주기만 받은 금액으로 정한 뒤 지킬 돈을 새 추천값으로 연다', async () => {
  apiSend.mockResolvedValue({ incomeBasis: 2800000, salaryAmount: 2800000 })
  await open(MORE)
  await choose('이번 예산 다시 짜기')
  expect(apiSend).toHaveBeenCalledWith(URL, 'POST', { answer: 'apply', salaryAmount: 2800000 })
  expect(closePopup).toHaveBeenCalledOnce()
  expect(navigate).toHaveBeenCalledWith(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })
})

it('그대로 두기는 기준을 바꾸지 않고(keep) 토스트만', async () => {
  apiSend.mockResolvedValue({ incomeBasis: null, salaryAmount: 2200000 })
  const onDone = await open(LESS)
  await choose('그대로 두기')
  expect(apiSend).toHaveBeenCalledWith(URL, 'POST', { answer: 'keep', salaryAmount: 2200000 })
  expect(toast).toHaveBeenCalledWith('지금 예산 그대로 둘게요')
  expect(onDone).toHaveBeenCalledOnce()
  expect(navigate).not.toHaveBeenCalled()
})

it('받은 월급이 바뀌어 거절되면 창에 안내하고 홈을 다시 불러온다', async () => {
  const err = Object.assign(new Error('받은 월급이 바뀌었어요. 홈을 새로고침해 주세요.'), { fields: { salaryAmount: ['x'] } })
  apiSend.mockRejectedValue(err)
  const onDone = await open(LESS)
  await choose('그대로 두기')
  expect(host.querySelector('[role=alert]').textContent).toBe('받은 월급이 바뀌었어요. 홈을 새로고침해 주세요.')
  expect(onDone).toHaveBeenCalledOnce()
  expect(closePopup).not.toHaveBeenCalled()
})
