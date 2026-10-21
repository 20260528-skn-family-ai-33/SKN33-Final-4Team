// 팝업: 고정지출 추가 / 수정
// props: item = 고칠 고정지출 (없으면 "추가", 있으면 "수정")
// 저장·삭제는 아직 안 되고 닫히기만 한다
// 카테고리 (10/21 결정, 요구사항 TXN-008): 10개 + 기타 중 필수. 항목 이름을 보고 미리 골라 둔다(사용자가 직접 고르면 그대로 둠)
//   저축 · 투자 · 대출 상환 · 카드값은 고정지출로 받지 않는다 (저축 = 저축 확인 · 대출 = 대출 확인 · 카드값 = 카드 청구서)
//   서버로 보낼 때는 화면 이름이 아니라 카테고리 코드로 보낸다 (docs/API_RULES.md 카테고리 코드)
// 결제 수단 (10/21 결정, C-02): 계좌 / 카드 / 모름 중 하나. 선택이라 몰라도 저장된다
//   서버에는 paymentAccountId · paymentCardId 로 보낸다 (둘 다 null = 모름). 이름은 서버가 준 displayName 을 그대로 쓴다
//   고를 수 있는 것: 관리 대상으로 고른 입출금 계좌 · 카드 (연결 해제된 것 제외) — docs/API_RULES.md 계좌 · 카드 표시
import { useState } from 'react'
import Popup from './Popup.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import { usePopup } from './context.js'
import { MONTH_DAYS } from '../utils/days.js'
import { PICK_CATS, guessCat } from '../utils/categories.js'

// 시안 예시값 — 연결 API 가 생기면 서버가 준 목록(displayName)으로 바꾼다
const ACCOUNTS = ['국민 급여통장 · ****5678', '신한 저축예금 · ****1234']
const CARDS = ['신한 Deep Dream · ****4321']
const PAYS = [['account', '계좌'], ['card', '카드'], ['none', '모름']]

// 고칠 항목의 결제 수단
function payOf(item) {
  if (!item) return 'account'
  if (item.card) return 'card'
  if (item.account) return 'account'
  return 'none'
}

export default function FixedPopup({ item }) {
  const { closePopup } = usePopup()
  const editing = !!item
  const disconnected = item?.payStatus === 'disconnected'   // 연결 해제된 계좌·카드면 다시 고르게 비워 둔다
  const [cat, setCat] = useState(item?.cat ?? '')   // '' = 아직 안 고름
  const [catTouched, setCatTouched] = useState(editing)   // 직접 고른 뒤에는 이름으로 바꾸지 않는다
  const [pay, setPay] = useState(() => payOf(item))
  const [payPick, setPayPick] = useState(disconnected ? '' : (item?.account ?? item?.card ?? ''))
  const [error, setError] = useState('')

  const onName = (e) => {
    if (catTouched) return
    setCat(guessCat(e.target.value) ?? '')
  }

  const choosePay = (value) => { setPay(value); setPayPick(''); setError('') }

  const onSubmit = (e) => {
    e.preventDefault()  // 페이지 새로고침 막기
    if (!cat) { setError('카테고리를 골라 주세요.'); return }
    if (pay !== 'none' && !payPick) { setError(pay === 'card' ? '결제 카드를 골라 주세요.' : '출금 계좌를 골라 주세요.'); return }
    closePopup()        // 저장은 API 연결 후
  }

  const options = pay === 'card' ? CARDS : ACCOUNTS

  return (
    <Popup label="고정지출 수정" sub>
      <div className="mc-eyebrow">{editing ? '고정지출 수정' : '고정지출 추가'}</div>
      <h1>{editing ? item.name : '매달 나가는 돈 추가'}</h1>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit}>
        {/* defaultValue = 처음 채워 둘 값 (수정일 때만) */}
        <label className="mc-field">항목 이름<input name="fxName" placeholder="월세, 통신비 등" type="text" defaultValue={item?.name} onChange={onName} /></label>
        <label className="mc-field">금액<MoneyInput name="fxAmount" placeholder="예: 55,000" defaultValue={item?.amount} /></label>
        <label className="mc-field">출금일
          <select defaultValue={item ? `매월 ${item.day}일` : undefined}>{MONTH_DAYS.map((d) => <option key={d}>매월 {d}일</option>)}</select>
        </label>

        <div className="mc-field">결제 수단
          <div className="mc-seg mc-seg-sm" role="group" aria-label="결제 수단" style={{ gridTemplateColumns: 'repeat(3, 1fr)', marginTop: 6 }}>
            {PAYS.map(([value, label]) => (
              <button key={value} type="button" aria-pressed={pay === value} onClick={() => choosePay(value)}>{label}</button>
            ))}
          </div>
        </div>
        {disconnected && pay !== 'none' && !payPick && <p className="mc-note" style={{ padding: 0 }}>연결 해제된 {item.card ? '카드' : '계좌'}예요. 다시 골라 주세요.</p>}
        {pay === 'none' ? (
          <p className="mc-note" style={{ padding: 0 }}>출금 계좌를 몰라도 저장돼요. 나중에 고를 수 있어요.</p>
        ) : (
          <label className="mc-field">{pay === 'card' ? '결제 카드' : '출금 계좌'}
            <select value={payPick} onChange={(e) => { setPayPick(e.target.value); setError('') }}>
              <option value="" disabled>{pay === 'card' ? '카드 선택' : '계좌 선택'}</option>
              {options.map((o) => <option key={o}>{o}</option>)}
            </select>
          </label>
        )}

        <label className="mc-field">카테고리
          <select value={cat} onChange={(e) => { setCat(e.target.value); setCatTouched(true); setError('') }}>
            <option value="" disabled>카테고리 선택</option>
            {PICK_CATS.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <p className="mc-note" style={{ padding: 0 }}>저축·대출 상환·카드값은 여기서 받지 않고 따로 챙겨요.</p>
        <div className="mc-error" role="alert">{error}</div>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit">{editing ? '수정 저장' : '추가하기'}</button></div>
      </form>
      {editing && <button type="button" className="mc-secondary">이 고정지출 삭제</button>}
    </Popup>
  )
}
