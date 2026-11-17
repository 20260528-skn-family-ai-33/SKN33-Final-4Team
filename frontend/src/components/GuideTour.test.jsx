import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import GuideTour, { GUIDE_STEPS } from './GuideTour.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })

const title = () => host.querySelector('#mc-tour-title').textContent
const next = () => act(async () => host.querySelector('.mc-tour-next').click())

it('쿠숑이가 인사한 뒤 홈 · 거래 · 예산 · 쿠션 · 챗봇 순서로 탭을 소개하고 마지막에 닫는다', async () => {
  const onClose = vi.fn()
  await act(() => root.render(<GuideTour onClose={onClose} />))
  const dialog = host.querySelector('[role="dialog"]')
  expect(dialog.getAttribute('aria-modal')).toBe('true')
  expect(title()).toBe('안녕하세요! 쿠숑이에요')
  expect(host.textContent).toContain('앞으로 여러분의 돈 관리를 제가 함께 도와드릴게요.')
  expect(host.querySelector('.mc-mascot').dataset.mood).toBe('hello')
  expect(host.querySelector('.mc-mascot').getAttribute('src')).toBe('/mascots/kushong/hello.svg')
  const seen = [title()]
  for (let i = 1; i < GUIDE_STEPS.length; i++) {
    await next()
    seen.push(title())
  }
  expect(seen).toEqual(['안녕하세요! 쿠숑이에요', '홈', '거래', '예산', '쿠션', '챗봇', '준비 끝!'])
  expect(host.querySelector('.mc-tour-next').textContent).toBe('시작하기')
  expect(host.querySelector('.mc-tour-skip')).toBeNull()  // 마지막 단계에는 건너뛰기 · 이전이 없다
  expect(onClose).not.toHaveBeenCalled()
  await next()
  expect(onClose).toHaveBeenCalledOnce()
})

it('건너뛰기 · Esc 로 바로 닫고, 이전으로 한 단계 돌아갈 수 있다', async () => {
  const onClose = vi.fn()
  await act(() => root.render(<GuideTour onClose={onClose} />))
  await next()
  await next()
  expect(title()).toBe('거래')
  await act(async () => [...host.querySelectorAll('.mc-tour-skip')].find((b) => b.textContent === '이전').click())
  expect(title()).toBe('홈')
  await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
  expect(onClose).toHaveBeenCalledOnce()
  await act(async () => [...host.querySelectorAll('.mc-tour-skip')].find((b) => b.textContent === '건너뛰기').click())
  expect(onClose).toHaveBeenCalledTimes(2)
})

it('비출 탭을 찾지 못하면(기기 틀 밖) 화면 가운데 카드로 보여 준다', async () => {
  await act(() => root.render(<GuideTour onClose={() => {}} />))
  await next()
  expect(host.querySelector('.mc-tour-spot')).toBeNull()
  expect(host.querySelector('.mc-tour-card').classList.contains('is-center')).toBe(true)
  expect(host.querySelector('.mc-tour-dots').getAttribute('aria-label')).toBe(`${GUIDE_STEPS.length}단계 중 2단계`)
})
