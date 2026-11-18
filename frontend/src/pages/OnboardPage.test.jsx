// 시작하기 — 시안 O3 카드 세 장: [다음]으로 넘기고 마지막 장 [시작하기] = 내 정보 입력
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import OnboardPage from './OnboardPage.jsx'
import { PATHS } from '../routes.js'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('react-router', () => ({ useNavigate: () => navigate }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <main>{children}{foot}</main> }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('카드는 세 장을 넘긴 뒤 시작하기로 내 정보 입력에 간다', async () => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(() => root.render(<OnboardPage />))
  const next = () => host.querySelector('.mc-o3-next')
  expect(host.querySelector('.mc-o3-dots').getAttribute('aria-label')).toBe('3장 중 1장')
  await act(async () => next().click())
  await act(async () => next().click())
  expect(next().textContent).toBe('시작하기')
  expect(navigate).not.toHaveBeenCalled()
  await act(async () => next().click())
  expect(navigate).toHaveBeenCalledWith(PATHS.profile)
})

