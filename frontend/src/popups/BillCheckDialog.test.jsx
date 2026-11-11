// 청구서 확인 창 (11/11) — 열리면 그 카드사만 불러오고, 새 청구서가 있으면 재추천으로 · 없으면 안내 · 실패면 다시 시도 · 현대카드는 직접 조회
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import BillCheckDialog from './BillCheckDialog.jsx'
import { apiSend } from '../utils/api.js'
import { runFinanceSync } from '../utils/financeSync.js'

const { closePopup } = vi.hoisted(() => ({ closePopup: vi.fn() }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup, openPopup: vi.fn() }) }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn() }))
vi.mock('../utils/financeSync.js', async (original) => ({ ...await original(), runFinanceSync: vi.fn() }))
vi.mock('../components/CardSyncPanel.jsx', () => ({ default: ({ onChanged }) => <>
  <button type="button" onClick={() => onChanged({ ok: false })}>조회 실패</button>
  <button type="button" onClick={() => onChanged({ ok: true })}>조회 성공</button>
</> }))

const connection = (extra = {}) => ({ connectionId: 'conn-1', businessType: 'card', organizationCode: '0306', organizationName: '합성카드',
  status: 'connected', cardIds: ['card-1'], ...extra })
const notes = (...cardIds) => ({ cardNotes: cardIds.map((cardId) => ({ cardId, reason: 'first_bill_unavailable' })) })
// apiSend: 연결 목록 · 지킬 돈 설정. 지킬 돈 설정은 불러오기(runFinanceSync) 전에는 before(기본: 아직 청구서 없음), 뒤에는 after
let synced  // 불러오기에 성공했는지 (실패 응답은 청구서를 바꾸지 않는다)
const server = (connections, after, before = notes('card-1', 'card-2')) => apiSend.mockImplementation(async (path) =>
  path.startsWith('/api/finance/connections/') ? { items: connections } : synced ? after : before)

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const onFound = vi.fn()
beforeEach(() => {
  vi.clearAllMocks()
  synced = false
  runFinanceSync.mockImplementation(async () => {
    synced = true
    return { items: [{ connectionId: 'conn-1', organizationName: '합성카드', status: 'succeeded', newTransactionCount: 0 }] }
  })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})
const open = () => act(async () => root.render(<BillCheckDialog cardIds={['card-1']} onFound={onFound} />))
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent === text)

it('그 카드사만 불러오고, 새 청구서가 보이면 창을 닫고 재추천으로 간다', async () => {
  server([connection(), connection({ connectionId: 'conn-2', cardIds: ['card-9'] })], notes())
  await open()
  expect(runFinanceSync).toHaveBeenCalledExactlyOnceWith({ connectionIds: ['conn-1'] })
  expect(closePopup).toHaveBeenCalledOnce()
  expect(onFound).toHaveBeenCalledOnce()
})

it('불러와도 청구서가 그대로 없으면 아직 조회되지 않았다고 알리고, 확인을 누르면 원래 화면에 남는다', async () => {
  server([connection()], notes('card-1'))
  await open()
  expect(host.textContent).toContain('아직 청구서가 조회되지 않았어요')
  expect(onFound).not.toHaveBeenCalled()
  await act(async () => button('확인').click())
  expect(closePopup).toHaveBeenCalledOnce()
})

it('불러오기가 실패하면 이유를 보여 주고 다시 시도할 수 있다', async () => {
  server([connection()], notes())
  runFinanceSync.mockResolvedValueOnce({ items: [{ connectionId: 'conn-1', organizationName: '합성카드', status: 'failed', error: { message: '카드사 점검 중이에요.' } }] })
  await open()
  expect(host.textContent).toContain('청구서를 불러오지 못했어요')
  expect(host.textContent).toContain('합성카드 · 카드사 점검 중이에요.')
  expect(onFound).not.toHaveBeenCalled()
  await act(async () => button('다시 시도').click())
  expect(runFinanceSync).toHaveBeenCalledTimes(2)
  expect(onFound).toHaveBeenCalledOnce()
})

it('카드번호를 묻는 카드사 · 다시 로그인이 필요한 연결은 자동으로 불러오지 않고 조회 칸을 보여 준다', async () => {
  let protect = notes('card-1')                             // 아직 청구서 없음 → 조회 칸
  apiSend.mockImplementation(async (path) => path.startsWith('/api/finance/connections/')
    ? { items: [connection({ organizationCode: '0302', organizationName: '현대카드' })] } : protect)
  await open()
  expect(runFinanceSync).not.toHaveBeenCalled()
  protect = notes()                                         // 직접 불러온 뒤에는 청구서가 보인다
  await act(async () => button('조회 실패').click())        // 실패면 조회 칸이 그대로 남는다
  expect(onFound).not.toHaveBeenCalled()
  expect(button('조회 성공')).toBeDefined()
  await act(async () => button('조회 성공').click())        // 성공하면 바로 청구서 확인
  expect(onFound).toHaveBeenCalledOnce()
})

// 리뷰: 다른 불러오기가 돌고 있으면 서버가 그 작업을 돌려줘서 결과에 이 카드사가 없다 → 끝난 뒤 한 번 더 불러온다
it('다른 불러오기 결과가 오면 이 카드사로 한 번 더 불러온다', async () => {
  server([connection()], notes())
  runFinanceSync.mockResolvedValueOnce({ items: [{ connectionId: 'bank-1', organizationName: '합성은행', status: 'succeeded' }] })
  await open()
  expect(runFinanceSync).toHaveBeenCalledTimes(2)
  expect(runFinanceSync).toHaveBeenLastCalledWith({ connectionIds: ['conn-1'] })
  expect(onFound).toHaveBeenCalledOnce()
})

// 리뷰: 자동으로 되는 카드사 + 현대카드가 섞이면 자동 쪽을 먼저 불러와 확인하고, 없을 때만 현대카드 조회 칸을 보여 준다
it('카드사가 섞여 있으면 자동으로 되는 곳부터 불러와 확인한다', async () => {
  const hyundai = connection({ connectionId: 'conn-h', organizationCode: '0302', organizationName: '현대카드', cardIds: ['card-2'] })
  server([connection(), hyundai], notes())
  await act(async () => root.render(<BillCheckDialog cardIds={['card-1', 'card-2']} onFound={onFound} />))
  expect(runFinanceSync).toHaveBeenCalledExactlyOnceWith({ connectionIds: ['conn-1'] })
  expect(onFound).toHaveBeenCalledOnce()                     // 합성카드 청구서가 나와서 현대카드 입력 없이 끝

  vi.clearAllMocks()
  runFinanceSync.mockResolvedValue({ items: [{ connectionId: 'conn-1', organizationName: '합성카드', status: 'succeeded' }] })
  server([connection(), hyundai], notes('card-1', 'card-2'))
  await act(() => root.unmount())
  root = createRoot(host)
  await act(async () => root.render(<BillCheckDialog cardIds={['card-1', 'card-2']} onFound={onFound} />))
  expect(onFound).not.toHaveBeenCalled()
  expect(host.textContent).toContain('카드사 확인이 필요해요')  // 아직 없으면 현대카드만 직접 조회
})

// 11/12: 홈 새로고침 등으로 이미 청구서가 들어와 있으면 다시 불러오지 않고 바로 넘어간다
it('창을 열 때 이미 청구서가 들어와 있으면 불러오지 않고 바로 재추천으로 간다', async () => {
  server([connection()], notes(), notes())
  await open()
  expect(runFinanceSync).not.toHaveBeenCalled()
  expect(apiSend).not.toHaveBeenCalledWith('/api/finance/connections/', 'GET')
  expect(onFound).toHaveBeenCalledOnce()
})
