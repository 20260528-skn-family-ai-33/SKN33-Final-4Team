// 팝업: 고정지출 추가 / 수정
// props: item = 고칠 고정지출 (없으면 "추가", 있으면 "수정")
// 저장·삭제는 아직 안 되고 닫히기만 한다
// 카테고리 (10/21 결정, 요구사항 TXN-008): 10개 + 기타 중 필수. 항목 이름을 보고 미리 골라 둔다(사용자가 직접 고르면 그대로 둠)
//   저축 · 투자 · 대출 상환 · 카드값은 고정지출로 받지 않는다 (저축 = 저축 확인 · 대출 = 대출 확인 · 카드값 = 카드 청구서)
//   서버로 보낼 때는 화면 이름이 아니라 카테고리 코드로 보낸다 (docs/API_RULES.md 카테고리 코드)
// 주기 · 상태 (10/21 결정, C-04): 매월만. [추가하기] = 바로 확정(confirmed, 출처 manual)
//   온보딩 뒤에 추가하면 이번 달 몫은 여윳돈으로 먼저 챙기고(모자라면 예산 조정 제안), 다음 달부터 고정비로 반영 (TXN-008 · BUD-005)
// 같은 비용 찾기 (10/21 결정, C-08 · C-16): 이름은 별명이라 이름으로 맞추지 않는다
//   1차엔 이 팝업에서 실제 결제를 고르지 않는다 (보여 줄 결제 목록 API 가 없음, C-16)
//   카드로 저장하면 목록에서 같은 카드 · 금액 ±10% · 날짜 ±3일로 찾은 결제를 "이 결제가 맞나요?" / [고르기]로 묻는다 (FixedPage · EvidencePopup)
//   카드로 내는 고정지출은 카드값 안에 들어 있어 따로 '나갈 돈'으로 세지 않는다 (COM-004)
// 두 번 저장 막기 (10/21 결정, C-10): 팝업을 열 때 요청 번호를 하나 만들고 저장할 때 같이 보낸다. 저장 중에는 버튼을 잠근다
// 금액 · 출금일 (10/21 결정, C-03): 금액 1,000원 ~ 1억 원. 출금일은 비워도 됨('모름'). 그 달에 없는 날은 말일 (utils/days.js dayIn)
//   고정지출 합계가 월급보다 커도 여기서는 막지 않는다 (예산 만들 때 부족액 안내, BUD-001)
// 결제 수단 (10/21 결정, C-02): 계좌 / 카드 / 모름 중 하나. 선택이라 몰라도 저장된다
//   서버에는 paymentAccountId · paymentCardId 로 보낸다 (둘 다 null = 모름). 이름은 서버가 준 displayName 을 그대로 쓴다
//   고를 수 있는 것: 관리 대상으로 고른 입출금 계좌 · 카드 (연결 해제된 것 제외) — docs/API_RULES.md 계좌 · 카드 표시
import { useState } from 'react'
import Popup from './Popup.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import { usePopup } from './context.js'
import { MONTH_DAYS } from '../utils/days.js'
import { PICK_CATS, guessCat } from '../utils/categories.js'
import { newRequestKey } from '../utils/api.js'

// 시안 예시값 — 연결 API 가 생기면 서버가 준 목록(displayName)으로 바꾼다
const ACCOUNTS = ['국민 급여통장 · ****5678', '신한 저축예금 · ****1234']
const CARDS = ['신한 Deep Dream · ****4321']
const PAYS = [['account', '계좌'], ['card', '카드'], ['none', '모름']]
const MIN_AMOUNT = 1_000
const MAX_AMOUNT = 100_000_000

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
  const [requestKey] = useState(newRequestKey)   // 이 팝업의 요청 번호 (다시 눌러도 같은 번호)
  const [saving, setSaving] = useState(false)

  const onName = (e) => {
    if (catTouched) return
    setCat(guessCat(e.target.value) ?? '')
  }

  const choosePay = (value) => { setPay(value); setPayPick(''); setError('') }

  const onSubmit = (e) => {
    e.preventDefault()  // 페이지 새로고침 막기
    const amount = Number(new FormData(e.currentTarget).get('fxAmount') || 0)
    if (amount < MIN_AMOUNT) { setError('금액은 1,000원 이상 입력해 주세요.'); return }
    if (amount > MAX_AMOUNT) { setError('금액은 1억 원까지 입력할 수 있어요.'); return }
    if (!cat) { setError('카테고리를 골라 주세요.'); return }
    if (pay !== 'none' && !payPick) { setError(pay === 'card' ? '결제 카드를 골라 주세요.' : '출금 계좌를 골라 주세요.'); return }
    if (saving) return
    setSaving(true)
    // 저장은 API 연결 후: await apiSend('/api/budgets/fixed/', 'POST', body, { requestKey }) → 실패하면 setSaving(false) + 에러 표시
    void requestKey
    closePopup()
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
          <select defaultValue={item ? (item.day ? `매월 ${item.day}일` : '모름') : undefined}>
            {MONTH_DAYS.map((d) => <option key={d}>매월 {d}일</option>)}
            <option>모름</option>
          </select>
        </label>
        <p className="mc-note" style={{ padding: 0 }}>31일처럼 그 달에 없는 날은 그 달 말일에 나가는 걸로 봐요.</p>

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
        {pay === 'card' && <p className="mc-note" style={{ padding: 0 }}>카드로 내는 돈은 카드값 안에 들어 있어서 따로 빼지 않아요. 저장하면 이 카드에서 비슷한 결제를 찾아 맞는지 물어볼게요.</p>}

        <label className="mc-field">카테고리
          <select value={cat} onChange={(e) => { setCat(e.target.value); setCatTouched(true); setError('') }}>
            <option value="" disabled>카테고리 선택</option>
            {PICK_CATS.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <p className="mc-note" style={{ padding: 0 }}>매달 나가는 돈만 넣어요. 1년에 한 번 내는 건 비정기지출 준비금, 커피처럼 자주 사는 건 생활비 예산에서 챙겨요. 저축·대출 상환·카드값도 따로 챙겨요.</p>
        <div className="mc-error" role="alert">{error}</div>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit" disabled={saving}>{saving ? '저장 중…' : editing ? '수정 저장' : '추가하기'}</button></div>
      </form>
      {editing && <button type="button" className="mc-secondary">이 고정지출 삭제</button>}
    </Popup>
  )
}
