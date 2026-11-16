// 팝업: 정기 저축 추가 / 수정 / 중지 (10/22 결정, docs/api/savings.md S-03 · S-04 · S-09 · S-11 · S-12)
// props: item = 고칠 저축 (GET /api/budgets/savings/ 의 한 건, 없으면 "추가"), manage = 마이 > 정기 저축에서 열었는지 (예산이 이미 있을 때만 반영 시점 안내),
//        onSaved = 저장 · 중지 성공 뒤 목록 다시 받기
//   추가 = POST /api/budgets/savings/ → 바로 확정(confirmed, 출처 manual). 다른 은행 적금처럼 자동으로 못 찾는 저축
//   수정 = PATCH /api/budgets/savings/<id>/ — 바꾼 칸만 보낸다
//   중지 = DELETE /api/budgets/savings/<id>/ — 확정 항목만 [더 이상 안 해요]. 지우지 않고 stopped. 후보는 저축 확인 화면의 [아니에요]
// 후보(suggested)는 보내는 · 받는 계좌를 못 바꾼다 (S-11): 'A → B 이체를 3번 봤다'가 근거라서. 계좌 칸을 잠그고 안내한다
//   계좌가 틀렸으면 [아니에요] 후 직접 추가. 이름 · 금액 · 이체일은 고칠 수 있다
// 저축 최소 금액(확정 합계)이 바뀌면 승인 전 예산 초안은 만료, 승인된 이번 달 예산은 그대로 → 다음 달부터 반영 (S-12, BUD-005)
// 금액 1,000원 ~ 1억 원. 이체일 · 계좌는 몰라도 저장된다. 그 달에 없는 날은 말일 (utils/days.js)
// 두 번 저장 막기: 팝업을 열 때 요청 번호를 하나 만들고 저장할 때 같이 보낸다 (fixed 와 같음)
// 계좌 선택지: 금융 연결 입출금 계좌 후보 (보내는 계좌 = 관리 대상만). shortcut: 예적금 목록 API가 없어 받는 계좌도 입출금 계좌 · '연결 안 함'만, 세진이 예적금 후보 API를 주면 바꾼다
// 실패하면 팝업을 닫지 않고 입력을 그대로 둔다
import { useState } from 'react'
import Popup from './Popup.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import { usePopup } from './context.js'
import { dayBad } from '../utils/days.js'
import DayInput from '../components/DayInput.jsx'
import { apiSend, newRequestKey } from '../utils/api.js'
import { payOptionsNote, usePayOptions } from '../utils/fixed.js'

const MIN_AMOUNT = 1_000
const MAX_AMOUNT = 100_000_000
const errText = (err) => Object.values(err.fields ?? {})[0]?.[0] ?? err.message  // 칸 오류가 있으면 그 문구

export default function SavingPopup({ item, manage = false, onSaved }) {
  const { closePopup } = usePopup()
  const editing = !!item
  const suggested = item?.status === 'suggested'   // 찾은 저축(후보) → 계좌 잠금
  const [error, setError] = useState('')
  const [dayText, setDayText] = useState(item?.transferDay ? String(item.transferDay) : '')  // 이체일 글자 ('' = 모름, 11/12 숫자 입력)
  const [requestKey] = useState(newRequestKey)
  const [saving, setSaving] = useState(false)
  const load = usePayOptions('/api/finance/connect/accounts/')  // 실패를 빈 목록으로 바꾸지 않는다 (N-F2)
  const live = load.items.filter((a) => a.status !== 'disconnected')
  // 지금 저장된 계좌는 후보에 없어도 보이게 둔다
  const withCurrent = (list, current) => (current && !list.some((a) => a.accountId === current.accountId) ? [current, ...list] : list)
  const fromOptions = withCurrent(live.filter((a) => a.isSelected), item?.fromAccount)
  const toOptions = withCurrent(live, item?.toAccount)

  const send = async (path, verb, body, options) => {
    setSaving(true)
    setError('')
    try {
      await apiSend(path, verb, body, options)
      onSaved?.()
      closePopup()
    } catch (err) {
      setError(errText(err))
      setSaving(false)
    }
  }

  const onSubmit = (e) => {
    e.preventDefault()
    const form = new FormData(e.currentTarget)
    const name = String(form.get('svName') || '').trim()
    const amount = Number(form.get('svAmount') || 0)
    if (!name || name.length > 100) { setError('이름은 1~100자로 입력해 주세요.'); return }
    if (amount < MIN_AMOUNT || amount > MAX_AMOUNT) { setError('금액은 1,000원 ~ 1억 원으로 입력해 주세요.'); return }
    if (dayBad(dayText)) { setError('이체일은 1~31 중에서 입력해 주세요.'); return }
    if (saving) return
    const full = {
      name, amount, transferDay: form.get('svDay') ? Number(form.get('svDay')) : null,
      fromAccountId: form.get('svFrom') || null, toAccountId: form.get('svTo') || null,
    }
    if (!editing) { send('/api/budgets/savings/', 'POST', full, { requestKey }); return }
    // 수정은 바꾼 칸만. 후보는 계좌 칸을 보내지 않는다 (S-11)
    const before = { name: item.name, amount: item.amount, transferDay: item.transferDay,
      ...(suggested ? {} : { fromAccountId: item.fromAccount?.accountId ?? null, toAccountId: item.toAccount?.accountId ?? null }) }
    const body = Object.fromEntries(Object.entries(before).filter(([k, v]) => full[k] !== v).map(([k]) => [k, full[k]]))
    if (Object.keys(body).length === 0) { closePopup(); return }
    send(`/api/budgets/savings/${item.savingId}/`, 'PATCH', body)
  }

  return (
    <Popup label={editing ? '정기 저축 수정' : '정기 저축 추가'} sub>
      <div className="mc-eyebrow">{editing ? '정기 저축 수정' : '정기 저축 추가'}</div>
      <h1>{editing ? item.name : '매달 하는 저축 추가'}</h1>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit}>
        <label className="mc-field">이름<input name="svName" placeholder="청약, 적금 등" type="text" maxLength={100} defaultValue={item?.name} /></label>
        <label className="mc-field">매달 저축액<MoneyInput name="svAmount" placeholder="예: 100,000" defaultValue={item?.amount} /></label>
        <label className="mc-field">이체일
          <DayInput id="mc-sv-day" name="svDay" what="이체일" optional value={dayText} onChange={(d) => { setDayText(d); setError('') }} />
        </label>

        <label className="mc-field">보내는 계좌
          <select name="svFrom" defaultValue={item?.fromAccount?.accountId ?? ''} disabled={suggested}>
            {fromOptions.map((a) => <option key={a.accountId} value={a.accountId}>{a.displayName}</option>)}
            <option value="">모름</option>
          </select>
        </label>
        <label className="mc-field">받는 저축 계좌
          <select name="svTo" defaultValue={item?.toAccount?.accountId ?? ''} disabled={suggested}>
            {toOptions.map((a) => <option key={a.accountId} value={a.accountId}>{a.displayName}</option>)}
            <option value="">연결 안 함 · 모름</option>
          </select>
        </label>
        {/* 목록을 못 받았으면 이유 + 다시 불러오기. 저장된 계좌는 그대로 남고 '모름'으로 바꾸지 않는다 */}
        {!suggested && payOptionsNote(load.status, live.length, '계좌') && (
          <p className="mc-note" role={load.status === 'ready' || load.status === 'loading' ? undefined : 'alert'} style={{ padding: 0 }}>
            {payOptionsNote(load.status, live.length, '계좌')}
            {['error', 'consent', 'login'].includes(load.status) && <> <button type="button" className="mc-link" disabled={load.retrying} onClick={load.retry}>다시 불러오기</button></>}
          </p>
        )}
        {suggested
          ? <p className="mc-note mc-saving-lock" style={{ padding: 0 }}>찾은 저축의 계좌는 바꿀 수 없어요. 계좌가 다르면 [아니에요]를 누르고 직접 추가해 주세요.</p>
          : <p className="mc-note" style={{ padding: 0 }}>다른 은행 적금처럼 앱에 연결 안 한 계좌면 '연결 안 함'으로 두면 돼요.</p>}

        {manage && <p className="mc-note" style={{ padding: 0 }}>저축 금액이 바뀌면 다음 달 예산부터 반영돼요. 이번 달 예산은 그대로예요.</p>}
        <div className="mc-error" role="alert">{error}</div>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit" disabled={saving}>{saving ? '저장 중…' : editing ? '수정 저장' : '추가하기'}</button></div>
      </form>
      {/* 중지 = DELETE. 확정된 저축만. 지난 기록은 남는다 */}
      {editing && !suggested && (
        <button type="button" className="mc-secondary" disabled={saving}
          onClick={() => send(`/api/budgets/savings/${item.savingId}/`, 'DELETE')}>{saving ? '처리 중…' : '더 이상 안 해요'}</button>
      )}
    </Popup>
  )
}
