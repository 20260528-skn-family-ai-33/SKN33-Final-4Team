import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ProfilePage from './ProfilePage.jsx'
import { apiSend, useApi } from '../utils/api.js'
import { PATHS } from '../routes.js'

const { navigate, location } = vi.hoisted(() => ({ navigate: vi.fn(), location: { state: null } }))
vi.mock('react-router', () => ({ useNavigate: () => navigate, useLocation: () => location }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn() }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <main>{children}{foot}</main> }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup: vi.fn() }) }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root, profile
const REGIONS = [
  { code: '11000', name: '서울특별시', regions: [{ code: '11110', name: '종로구' }, { code: '11620', name: '관악구' }] },
  { code: '36110', name: '세종특별자치시', regions: [{ code: '36110', name: '세종특별자치시' }] },
]
const submit = () => act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
const pick = (label, value) => act(async () => {
  const select = host.querySelector(`select[aria-label="${label}"]`)
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
})
beforeEach(async () => {
  vi.clearAllMocks()
  location.state = null
  profile = {
    birthDate: '1998-04-12', gender: 'female', hasCar: false, housingType: 'alone', regionCode: '11620',
    priorities: ['FOOD', 'TRANSPORT_CAR', 'CULTURE_LEISURE'].map((category, i) => ({ category, rank: i + 1 })),
    categoryMinimums: [],
  }
  useApi.mockImplementation((path) => ({ data: path === '/api/policies/regions/' ? { items: REGIONS } : profile }))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(() => root.render(<ProfilePage />))
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

it.each([
  [{ onboardingNextStep: 'salary' }, PATHS.salary],
  [{ onboardingNextStep: 'connect' }, PATHS.connect],
  [{}, PATHS.connect],
])('서버의 프로필 저장 응답 %j에 따라 다음 단계로 이동한다', async (response, destination) => {
  apiSend.mockResolvedValue(response)
  await act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(apiSend).toHaveBeenCalledWith('/api/profile/', 'PUT', expect.not.objectContaining({ onboardingNextStep: expect.anything() }))
  expect(navigate).toHaveBeenCalledWith(destination)
})

it('프로필 저장 실패 시 이동하지 않는다', async () => {
  apiSend.mockRejectedValue(new Error('저장 실패'))
  await act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(navigate).not.toHaveBeenCalled()
  expect(host.textContent).toContain('저장 실패')
})

it('마이에서 들어오면 단계 표시줄 없이 저장하고 마이로 돌아간다', async () => {
  location.state = { from: 'my' }
  await act(() => root.render(<ProfilePage key="my" />))
  expect(host.querySelector('.mc-step')).toBeNull()
  expect(host.querySelector('button[type=submit]').textContent).toBe('저장')

  apiSend.mockResolvedValue({ onboardingNextStep: 'salary' })
  await act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(navigate).toHaveBeenCalledWith(PATHS.my, { replace: true, state: { budgetNote: true } })
})

it('사는 지역은 저장된 값으로 시도 · 시군구를 채우고 PUT 에 보낸다', async () => {
  // 저장된 값이 다 있으면 마지막 확인 목록부터. 줄을 누르면 그 질문으로 돌아간다
  expect(host.querySelector('select[aria-label="시도"]')).toBeNull()
  expect(host.querySelector('[data-step="region"] b').textContent).toBe('서울특별시 관악구')
  await act(async () => host.querySelector('[data-step="region"]').click())
  expect(host.querySelector('select[aria-label="시도"]').value).toBe('11000')
  expect(host.querySelector('select[aria-label="시군구"]').value).toBe('11620')
  await pick('시군구', '11110')
  apiSend.mockResolvedValue({})
  await submit()
  expect(apiSend).toHaveBeenCalledWith('/api/profile/', 'PUT', expect.objectContaining({ regionCode: '11110' }))
})

it('사는 지역을 고르지 않으면 저장하지 않는다 (필수)', async () => {
  profile = { ...profile, regionCode: null }
  await act(() => root.render(<ProfilePage key="empty" />))
  expect(host.querySelector('select[aria-label="시군구"]').disabled).toBe(true)
  await submit()
  expect(apiSend).not.toHaveBeenCalled()
  expect(host.textContent).toContain('사는 지역을 골라 주세요.')
  await pick('시도', '36110')
  await pick('시군구', '36110')
  apiSend.mockResolvedValue({})
  await submit()
  expect(apiSend).toHaveBeenCalledWith('/api/profile/', 'PUT', expect.objectContaining({ regionCode: '36110' }))
})

it('처음 가입하면 한 번에 하나씩 묻고, [다음]은 빠진 대답을 막는다', async () => {
  profile = { birthDate: null, gender: null, hasCar: null, housingType: null, regionCode: null, priorities: [], categoryMinimums: [] }
  await act(() => root.render(<ProfilePage key="fresh" />))
  expect(host.querySelector('#mc-birth')).not.toBeNull()
  expect(host.querySelector('select[aria-label="시도"]')).toBeNull()
  expect(host.querySelector('button[type=submit]').textContent).toBe('다음')
  await submit()
  expect(host.textContent).toContain('생년월일 6자리를 확인해 주세요.')
  await act(async () => {
    const input = host.querySelector('#mc-birth')
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '980412')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await submit()
  expect(host.querySelector('#mc-birth')).toBeNull()
  expect(host.querySelector('.mc-o3-pill').textContent).toContain('2 / 7')
  expect([...host.querySelectorAll('button')].some((b) => b.textContent === '여성')).toBe(true)
  expect(apiSend).not.toHaveBeenCalled()
})
