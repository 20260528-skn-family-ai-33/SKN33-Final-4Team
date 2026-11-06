import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import RecommendPage from './RecommendPage.jsx'
import { apiSend } from '../utils/api.js'
import { PATHS } from '../routes.js'

const { navigate, location } = vi.hoisted(() => ({ navigate: vi.fn(), location: { state: null } }))
vi.mock('react-router', () => ({ useNavigate: () => navigate, useLocation: () => location }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), newRequestKey: () => 'synthetic-key' }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <main>{children}{foot}</main> }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup: vi.fn() }) }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let root, host
const data = (id = 'old') => ({
  cycle: { startsOn: '2026-10-25', endsBefore: '2026-11-25' },
  budget: { budgetId: id, status: 'validated', versionNo: 1, salaryConfirmed: true },
  split: { funding: 2000000, fixed: 600000, saving: 300000, cushion: 100000, living: 800000, spare: 200000 },
  categories: [{ category: 'FOOD', categoryName: '식비', allocated: 245000, minAmount: 240001, locked: false, reason: '다른 항목을 더 줄일 수 없어 식비도 줄였어요.' },
               { category: 'TRAVEL_LODGING', categoryName: '여행·숙박', allocated: 0, locked: false }],
})
const render = async (value = data()) => {
  apiSend.mockResolvedValueOnce(value)
  await act(() => root.render(<RecommendPage />))
}
const click = async (text) => act(async () => [...host.querySelectorAll('button')].find(b => b.textContent.includes(text)).click())

beforeEach(() => {
  vi.clearAllMocks()
  location.state = null
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('서버 금액·최소액·설명·여윳돈을 그대로 표시하고 제외 항목 자물쇠를 숨긴다', async () => {
  await render()
  expect(host.textContent).toContain('245,000원')
  expect(host.textContent).toContain('240,001원')
  expect(host.textContent).toContain('200,000원')
  expect(host.textContent.match(/다른 항목을 더 줄일 수 없어/g)).toHaveLength(1)
  expect(host.querySelector('[aria-label="여행·숙박 금액 고정"]')).toBeNull()
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('우선순위 감액 안내를 서버가 보낸 그대로 보여준다', async () => {
  const response = data()
  response.categories[0].reason = '식비(1순위): 평소 기준보다 25,000원 줄었어요.'
  await render(response)
  expect(host.textContent).toContain('식비(1순위): 평소 기준보다 25,000원 줄었어요.')
})

it('여윳돈 충당 제안을 승인 전 표시하고 서버 금액을 다시 계산하지 않는다', async () => {
  const response = data()
  response.recommendation = { spareTransfer: 50000 }
  response.split = { ...response.split, living: 850000, spare: 150000 }
  await render(response)
  expect(host.textContent).toContain('여윳돈 50,000원을 생활비에 포함한 추천안')
  expect(host.textContent).toContain('150,000원')
  expect(host.textContent).toContain('850,000원')
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('명시적으로 승인한 뒤에만 승인 API를 호출한다', async () => {
  await render()
  apiSend.mockResolvedValueOnce({})
  await act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(apiSend).toHaveBeenLastCalledWith('/api/budgets/old/approve/', 'POST', {}, { requestKey: 'synthetic-key' })
  expect(navigate).toHaveBeenCalledWith(PATHS.home)
})

it('자물쇠 요청 중 승인과 중복 자물쇠 클릭을 막는다', async () => {
  await render()
  let resolve
  apiSend.mockImplementationOnce(() => new Promise(r => { resolve = r }))
  await act(async () => host.querySelector('[aria-label="식비 금액 고정"]').click())
  expect(host.querySelector('[aria-label="식비 금액 고정"]').disabled).toBe(true)
  expect(host.querySelector('button[type=submit]').disabled).toBe(true)
  await act(async () => resolve({ locked: true, lockedAmount: 245000 }))
  expect(host.querySelector('[aria-label="식비 금액 고정"]').getAttribute('aria-pressed')).toBe('true')
})

it.each(['infeasible', 'needs_upper_allocation_review', 'needs_policy'])('추천 결과 %s를 성공 초안으로 보여주지 않는다', async outcome => {
  location.state = { autoRecommend: true }
  apiSend.mockResolvedValueOnce({ ...data(), budget: { ...data().budget, status: 'approved' } })
    .mockResolvedValueOnce({ task: { taskId: 'task' } })
    .mockResolvedValueOnce({ status: 'succeeded', result: { outcome, summary: '생활비를 다시 확인해 주세요.' } })
  await act(() => root.render(<RecommendPage />))
  expect(host.textContent).toContain('생활비를 다시 확인해 주세요.')
  expect(host.querySelector('button[form="mc-form"]')).toBeNull()
  expect(apiSend.mock.calls.some(([url]) => url.includes('/approve/'))).toBe(false)
})

it('추천 성공 후 서버가 다시 보내준 최종 생활비와 여윳돈을 표시한다', async () => {
  location.state = { autoRecommend: true }
  const next = data('new')
  next.split = { ...next.split, living: 850000, spare: 150000 }
  apiSend.mockResolvedValueOnce({ ...data(), budget: null })
    .mockResolvedValueOnce({ task: { taskId: 'task' } })
    .mockResolvedValueOnce({ status: 'succeeded', result: { outcome: 'proposed', budgetId: 'new' } })
    .mockResolvedValueOnce(next)
  await act(() => root.render(<RecommendPage />))
  expect(host.textContent).toContain('850,000원')
  expect(host.textContent).toContain('150,000원')
  expect(host.querySelector('button[form="mc-form"]')).not.toBeNull()
})

it('완료 작업의 예산 ID와 다른 예전 초안은 승인하지 못하게 한다', async () => {
  location.state = { autoRecommend: true }
  apiSend.mockResolvedValueOnce({ ...data(), budget: null })
    .mockResolvedValueOnce({ task: { taskId: 'task' } })
    .mockResolvedValueOnce({ status: 'succeeded', result: { outcome: 'proposed', budgetId: 'new' } })
    .mockResolvedValueOnce(data('old'))
  await act(() => root.render(<RecommendPage />))
  expect(host.textContent).toContain('새 추천 예산을 확인하지 못했어요.')
  expect(host.querySelector('button[form="mc-form"]')).toBeNull()
})

it('성공 후 조회에 실패하면 기존 초안으로 승인을 계속하지 않는다', async () => {
  location.state = { autoRecommend: true }
  apiSend.mockResolvedValueOnce({ ...data(), budget: null })
    .mockResolvedValueOnce({ task: { taskId: 'task' } })
    .mockResolvedValueOnce({ status: 'succeeded', result: { outcome: 'proposed', budgetId: 'new' } })
    .mockRejectedValueOnce(new Error('조회 실패'))
  await act(() => root.render(<RecommendPage />))
  expect(host.textContent).toContain('새 추천 예산을 확인하지 못했어요.')
  expect(host.querySelector('button[form="mc-form"]')).toBeNull()
})

it('승인 만료 후 다시 조회에 실패해도 예전 초안 승인 버튼을 남기지 않는다', async () => {
  await render()
  apiSend.mockRejectedValueOnce(Object.assign(new Error('만료된 초안'), { status: 409 }))
    .mockRejectedValueOnce(new Error('조회 실패'))
  await act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(host.textContent).toContain('만료된 초안')
  expect(host.querySelector('button[form="mc-form"]')).toBeNull()
})

it('새 추천 요청이 실패하면 승인된 예산을 유지하고 승인 API는 부르지 않는다', async () => {
  await render({ ...data(), budget: { ...data().budget, status: 'approved' } })
  apiSend.mockRejectedValueOnce(new Error('추천 요청 실패'))
  await click('다시 추천받기')
  expect(host.textContent).toContain('추천 요청 실패')
  expect(host.querySelector('button[form="mc-form"]')).toBeNull()
})
