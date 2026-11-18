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
  expect(host.textContent).toContain('아래 다섯 탭을 하나씩 짚어 드릴게요.')
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
  expect(host.querySelector('.mc-tour-dots').getAttribute('aria-label')).toBe(`${GUIDE_STEPS.length - 2}단계 중 1단계`)
  expect(host.querySelectorAll(".mc-tour-dots i.on")).toHaveLength(1)  // 1 / 5 = 막대 하나
})

// 카드는 기기 틀 안에 절대 배치 → bottom 도 기기 안 좌표(d.height 기준). 데스크톱에서 틀 위 여백(24px)이 겹쳐 들어가지 않게
it('기기 틀이 화면에서 내려와 있어도 안내 카드 bottom 을 기기 안 좌표로 계산한다', async () => {
  const device = document.createElement('div'); device.className = 'mc-device'
  device.innerHTML = '<nav id="mc-nav"><button class="mc-home"></button></nav>'
  document.body.append(device); device.append(host)
  const rect = (top, left, width, height) => () => ({ top, left, width, height, right: left + width, bottom: top + height })
  device.getBoundingClientRect = rect(24, 0, 393, 852)
  device.querySelector('.mc-home').getBoundingClientRect = rect(810, 176, 40, 40)
  await act(() => root.render(<GuideTour onClose={() => {}} />))
  await next()
  expect(host.querySelector('.mc-tour-card').style.bottom).toBe('90px')  // 852 - (806 - 30) + 14
  device.remove()
})
