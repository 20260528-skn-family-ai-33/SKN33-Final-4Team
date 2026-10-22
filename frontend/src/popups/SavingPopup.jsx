// 팝업: 정기 저축 추가 / 수정 / 중지 (10/22 결정, docs/api/savings.md S-03 · S-04 · S-09 · S-11 · S-12)
// props: item = 고칠 저축 (없으면 "추가", 있으면 "수정"), manage = 마이 > 정기 저축에서 열었는지 (예산이 이미 있을 때만 반영 시점 안내)
//   추가 = POST /api/budgets/savings/ → 바로 확정(confirmed, 출처 manual). 다른 은행 적금처럼 자동으로 못 찾는 저축
//   수정 = PATCH /api/budgets/savings/<id>/ — 바꾼 칸만 보낸다
//   중지 = DELETE /api/budgets/savings/<id>/ — 확정 항목만 [더 이상 안 해요]. 지우지 않고 stopped. 후보는 저축 확인 화면의 [아니에요]
// 후보(suggested)는 보내는 · 받는 계좌를 못 바꾼다 (S-11): 'A → B 이체를 3번 봤다'가 근거라서. 계좌 칸을 잠그고 안내한다
//   계좌가 틀렸으면 [아니에요] 후 직접 추가. 이름 · 금액 · 이체일은 고칠 수 있다
// 저축 최소 금액(확정 합계)이 바뀌면 승인 전 예산 초안은 만료, 승인된 이번 달 예산은 그대로 → 다음 달부터 반영 (S-12, BUD-005)
// 금액 1,000원 ~ 1억 원. 이체일 · 계좌는 몰라도 저장된다. 그 달에 없는 날은 말일 (utils/days.js)
// 두 번 저장 막기: 팝업을 열 때 요청 번호를 하나 만들고 저장할 때 같이 보낸다 (fixed 와 같음)
// 저장은 아직 안 되고 닫히기만 한다
import { useState } from 'react'
import Popup from './Popup.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import { usePopup } from './context.js'
import { MONTH_DAYS } from '../utils/days.js'
import { newRequestKey } from '../utils/api.js'

// 시안 예시값 — 연결 API 가 생기면 서버가 준 목록(displayName)으로 바꾼다
const FROM_ACCOUNTS = ['국민 급여통장 · ****5678']   // 보내는 계좌: 관리 대상 입출금 계좌만
const TO_ACCOUNTS = ['신한 자유적금 · ****9012', '국민 주택청약 · ****3456']   // 받는 계좌: 예적금도 됨
const MIN_AMOUNT = 1_000
const MAX_AMOUNT = 100_000_000
const UNKNOWN = '모름'

export default function SavingPopup({ item, manage = false }) {
  const { closePopup } = usePopup()
  const editing = !!item
  const suggested = item?.status === 'suggested'   // 찾은 저축(후보) → 계좌 잠금
  const [error, setError] = useState('')
  const [requestKey] = useState(newRequestKey)
  const [saving, setSaving] = useState(false)

  const onSubmit = (e) => {
    e.preventDefault()
    const form = new FormData(e.currentTarget)
    const name = String(form.get('svName') || '').trim()
    const amount = Number(form.get('svAmount') || 0)
    if (!name || name.length > 100) { setError('이름은 1~100자로 입력해 주세요.'); return }
    if (amount < MIN_AMOUNT || amount > MAX_AMOUNT) { setError('금액은 1,000원 ~ 1억 원으로 입력해 주세요.'); return }
    if (saving) return
    setSaving(true)
    // 저장은 API 연결 후: 추가 apiSend('/api/budgets/savings/', 'POST', body, { requestKey }) / 수정 PATCH (바꾼 칸만, 후보는 계좌 칸을 보내지 않음)
    void requestKey
    closePopup()
  }

  return (
    <Popup label={editing ? '정기 저축 수정' : '정기 저축 추가'} sub>
      <div className="mc-eyebrow">{editing ? '정기 저축 수정' : '정기 저축 추가'}</div>
      <h1>{editing ? item.name : '매달 하는 저축 추가'}</h1>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit}>
        <label className="mc-field">이름<input name="svName" placeholder="청약, 적금 등" type="text" maxLength={100} defaultValue={item?.name} /></label>
        <label className="mc-field">매달 저축액<MoneyInput name="svAmount" placeholder="예: 100,000" defaultValue={item?.amount} /></label>
        <label className="mc-field">이체일
          <select defaultValue={item ? (item.day ? `매월 ${item.day}일` : UNKNOWN) : UNKNOWN}>
            {MONTH_DAYS.map((d) => <option key={d}>매월 {d}일</option>)}
            <option>{UNKNOWN}</option>
          </select>
        </label>

        <label className="mc-field">보내는 계좌
          <select defaultValue={item?.from ?? UNKNOWN} disabled={suggested}>
            {FROM_ACCOUNTS.map((a) => <option key={a}>{a}</option>)}
            <option>{UNKNOWN}</option>
          </select>
        </label>
        <label className="mc-field">받는 저축 계좌
          <select defaultValue={item?.to ?? UNKNOWN} disabled={suggested}>
            {TO_ACCOUNTS.map((a) => <option key={a}>{a}</option>)}
            <option value={UNKNOWN}>연결 안 함 · 모름</option>
          </select>
        </label>
        {suggested
          ? <p className="mc-note mc-saving-lock" style={{ padding: 0 }}>찾은 저축의 계좌는 바꿀 수 없어요. 계좌가 다르면 [아니에요]를 누르고 직접 추가해 주세요.</p>
          : <p className="mc-note" style={{ padding: 0 }}>다른 은행 적금처럼 앱에 연결 안 한 계좌면 '연결 안 함'으로 두면 돼요.</p>}

        {manage && <p className="mc-note" style={{ padding: 0 }}>저축 금액이 바뀌면 다음 달 예산부터 반영돼요. 이번 달 예산은 그대로예요.</p>}
        <div className="mc-error" role="alert">{error}</div>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit" disabled={saving}>{saving ? '저장 중…' : editing ? '수정 저장' : '추가하기'}</button></div>
      </form>
      {/* 중지 = DELETE. 확정된 저축만. 지난 기록은 남는다 */}
      {editing && !suggested && <button type="button" className="mc-secondary" onClick={closePopup}>더 이상 안 해요</button>}
    </Popup>
  )
}
