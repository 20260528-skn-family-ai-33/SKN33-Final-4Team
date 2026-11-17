import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router'
import ApiStatus from './ApiStatus.jsx'
import KushongRunner from './KushongRunner.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('로딩에 첨부된 애니메이션 그림을 쓰고 화면 읽기에는 숨긴다', async () => {
  await act(() => root.render(<KushongRunner size={120} />))
  const image = host.querySelector('img.mc-run-dog')
  expect(image.parentElement.getAttribute('aria-hidden')).toBe('true')
  expect(image.getAttribute('width')).toBe('120')
  expect(image.getAttribute('src')).toBe('/mascots/kushong/runner-loading.svg')
})

it('불러오는 중에는 쿠숑이 그림과 함께 글자 안내를 그대로 알린다', async () => {
  await act(() => root.render(<MemoryRouter><ApiStatus /></MemoryRouter>))
  expect(host.querySelector('[role="status"]').textContent).toBe('불러오는 중…')
  expect(host.querySelector('.mc-run-card img.mc-run-dog')).not.toBeNull()
})
