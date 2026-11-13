import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ChatReply from './ChatReply.jsx'
import { policyChoices } from '../utils/policyCards.js'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root, saved, failSave, fetcher
const content = {
  agent: 'policy', result: true, lines: ['추천한 정책이에요.'],
  sections: [
    { policyNo: 'P1', title: '1. 첫 정책', lines: ['지원: 첫 지원', '링크: https://example.org/one'] },
    { policyNo: 'P/2', title: '2. 둘째 정책', lines: ['지원: 둘째 지원'] },
  ],
  choices: [{ value: 'save:P1', label: '1번 저장' }, { value: 'save:P/2', label: '2번 저장' }, { value: 'more', label: '다른 정책 더 보기' }],
}
const button = (text) => [...host.querySelectorAll('button')].find((node) => node.textContent === text)
const render = (value = content) => act(async () => root.render(<ChatReply content={value} title="청년 정책" active={false} />))
const click = (text) => act(async () => button(text).click())
const puts = () => fetcher.mock.calls.filter(([, opts]) => opts?.method === 'PUT')

beforeEach(() => {
  saved = []; failSave = false
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  fetcher = vi.fn(async (url, opts) => {
    if (opts?.method === 'PUT') {
      if (failSave) return { ok: false, status: 503, json: async () => ({ error: { message: '잠시 후 다시 시도해 주세요.' } }) }
      const policyNo = decodeURIComponent(url.split('/')[3])
      saved.push({ policyNo })
      return { ok: true, json: async () => ({ policyNo }) }
    }
    return { ok: true, json: async () => url === '/api/policies/saved/' ? { items: saved } : {} }
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })

it('한 정책만 보여 주고 이전·다음으로 이동한다. 옛 답변도 넘길 수 있다', async () => {
  await render()
  expect(host.querySelector('.mc-policy-title').textContent).toBe('첫 정책')
  expect(host.textContent).toContain('첫 지원')
  expect(host.textContent).not.toContain('둘째 지원')
  expect(button('이전 정책').disabled).toBe(true)
  await click('다음 정책')
  expect(host.querySelector('.mc-policy-title').textContent).toBe('둘째 정책')
  expect(host.textContent).toContain('둘째 지원')
  expect(host.textContent).not.toContain('첫 지원')
  expect(button('다음 정책').disabled).toBe(true)
  await click('이전 정책')
  expect(host.querySelector('a').href).toBe('https://example.org/one')
})

it('지난 답변에서도 현재 보이는 정책 ID로만 저장하며 중복 저장을 막는다', async () => {
  await render()
  await click('다음 정책')
  await click('저장')
  expect(puts()).toHaveLength(1)
  expect(puts()[0][0]).toBe('/api/policies/P%2F2/saved/')
  expect(button('저장됨').disabled).toBe(true)
  await click('이전 정책')
  expect(button('저장').disabled).toBe(false)
  await click('다음 정책')
  expect(button('저장됨').disabled).toBe(true)
})

it('대화를 다시 열어도 이미 저장한 정책 상태를 읽는다', async () => {
  saved = [{ policyNo: 'P1' }]
  await render()
  expect(button('저장됨').disabled).toBe(true)
  expect(puts()).toHaveLength(0)
})

it('저장 실패를 성공으로 표시하지 않고 재시도할 수 있다', async () => {
  failSave = true
  await render()
  await click('저장')
  expect(host.querySelector('[role="alert"]').textContent).toContain('잠시 후')
  expect(button('저장').disabled).toBe(false)
  failSave = false
  await click('저장')
  expect(button('저장됨')).toBeTruthy()
})

it('긴 자격 원문은 접되 마지막 조건까지 제공한다', async () => {
  const text = `자격: ${'조건 설명 '.repeat(100)}마지막 조건`
  await render({ ...content, sections: [{ ...content.sections[0], lines: [text] }] })
  expect(host.querySelector('dd details p').textContent).toBe(text.slice('자격: '.length))
  expect(host.querySelector('summary').textContent).toContain('전체 내용 보기')
})

it('[자세히 보기]는 지금 보이는 정책의 선택지(정책번호)를 고르고, 지난 답변에서도 누를 수 있다', async () => {
  const picked = []
  const detailed = { ...content, choices: [...content.choices, { value: 'detail:P1', label: '첫 정책 자세히 보기' }, { value: 'detail:P/2', label: '둘째 정책 자세히 보기' }] }
  await act(async () => root.render(<ChatReply content={detailed} title="청년 정책" active={false} onDetail={(c) => picked.push(c)} />))
  await click('다음 정책')
  await click('자세히 보기')
  expect(picked).toEqual([{ value: 'detail:P/2', label: '둘째 정책 자세히 보기' }])
  expect(policyChoices(detailed).map((c) => c.value)).toEqual(['more'])
  await render(detailed)  // 보내는 중(onDetail 없음)에는 누를 수 없다
  expect(button('자세히 보기').disabled).toBe(true)
})

it('새 카드에서는 번호별 저장 칩을 제거하고 예전 메시지의 선택지는 보존한다', () => {
  expect(policyChoices(content)).toEqual([content.choices[2]])
  expect(policyChoices({ ...content, sections: [{ title: '예전 정책', lines: [] }] })).toEqual(content.choices)
  expect(policyChoices({ ...content, agent: 'budget' })).toEqual(content.choices)
})

it('카드마다 서버가 알아듣는 추천 번호를 보여 준다 (다른 정책 더 보기의 4번부터)', async () => {
  await render({ ...content, sections: [
    { policyNo: 'P4', title: '4. 넷째 정책', lines: ['지원: 넷째 지원'] },
    { policyNo: 'P5', title: '5. 다섯째 정책', lines: ['지원: 다섯째 지원'] },
  ] })
  expect(host.querySelector('.mc-eyebrow').textContent).toBe('4번 정책')
  expect(host.querySelector('.mc-policy-title').textContent).toBe('넷째 정책')
  await click('다음 정책')
  expect(host.querySelector('.mc-eyebrow').textContent).toBe('5번 정책')
})
