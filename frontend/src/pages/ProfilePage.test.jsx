import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ProfilePage from './ProfilePage.jsx'
import { apiSend, useApi } from '../utils/api.js'
import { PATHS } from '../routes.js'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('react-router', () => ({ useNavigate: () => navigate }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn() }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <main>{children}{foot}</main> }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup: vi.fn() }) }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(async () => {
  vi.clearAllMocks()
  useApi.mockReturnValue({ data: {
    birthDate: '1998-04-12', gender: 'female', hasCar: false, housingType: 'alone',
    priorities: ['FOOD', 'TRANSPORT_CAR', 'CULTURE_LEISURE'].map((category, i) => ({ category, rank: i + 1 })),
    categoryMinimums: [],
  } })
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
