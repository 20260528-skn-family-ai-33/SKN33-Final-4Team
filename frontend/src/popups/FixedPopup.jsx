// 팝업: 고정지출 추가 / 수정
// props: item = 고칠 고정지출 (GET /api/budgets/fixed/ 의 한 건, 없으면 "추가"),
//        onSaved = 저장 · 중지 성공 뒤 목록 다시 받기
// 추가 POST · 수정 PATCH(바꾼 칸만) · 중지 DELETE (docs/api/fixed.md). 실패하면 팝업을 닫지 않고 입력을 그대로 둔다
// 중지 (fixed.md 3. 고정지출 중지): 확정된 항목만. 행은 지우지 않고 stopped, 이번 주기 미납 납부 예정도 취소. 지난 기록은 남는다
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
//   고를 수 있는 것: 관리 대상으로 고른 입출금 계좌 · 카드 (연결 해제된 것 제외) — 금융 연결 후보 목록에서 거른다. 최종 검사는 서버
import { useState } from 'react'
import Popup from './Popup.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import { usePopup } from './context.js'
import { MONTH_DAYS } from '../utils/days.js'
import { PICK_CATS, catCode, guessCat } from '../utils/categories.js'
import { apiSend, newRequestKey } from '../utils/api.js'
import { payOptionsNote, usePayOptions } from '../utils/fixed.js'

const PAYS = [['account', '계좌'], ['card', '카드'], ['none', '모름']]
const MIN_AMOUNT = 1_000
const MAX_AMOUNT = 100_000_000
const errText = (err) => Object.values(err.fields ?? {})[0]?.[0] ?? err.message  // 칸 오류가 있으면 그 문구

// 고칠 항목의 결제 수단
function payOf(item) {
  if (!item) return 'account'
  if (item.paymentCard) return 'card'
  if (item.paymentAccount) return 'account'
  return 'none'
}

// 금융 연결 후보 중 고를 수 있는 것만 { id, name } — 관리 대상 · 연결 중 (최종 검사는 서버)
const pickable = (items, idKey) => items.filter((o) => o.isSelected && o.status !== 'disconnected').map((o) => ({ id: o[idKey], name: o.displayName }))

export default function FixedPopup({ item, onSaved }) {
  const { closePopup } = usePopup()
  const editing = !!item
  const method = item?.paymentAccount ?? item?.paymentCard
  const disconnected = method?.status === 'disconnected'   // 연결 해제된 계좌·카드면 다시 고르게 비워 둔다
  const [cat, setCat] = useState(item?.categoryName ?? '')   // '' = 아직 안 고름
  const [catTouched, setCatTouched] = useState(editing)   // 직접 고른 뒤에는 이름으로 바꾸지 않는다
  const [pay, setPay] = useState(() => payOf(item))
  const [payPick, setPayPick] = useState(disconnected ? '' : (item?.paymentAccount?.accountId ?? item?.paymentCard?.cardId ?? ''))
  const [error, setError] = useState('')
  const [requestKey] = useState(newRequestKey)   // 이 팝업의 요청 번호 (다시 눌러도 같은 번호)
  const [saving, setSaving] = useState(false)
  const [stopAsk, setStopAsk] = useState(false)   // [더 이상 안 나가요]를 한 번 누르면 확인 문구를 보여 준다
  const canStop = editing && item.status === 'confirmed'   // 후보(suggested)는 중지가 아니라 [고정지출 아니에요]
  // 계좌 · 카드는 따로 받는다 — 한쪽이 실패해도 다른 결제 수단 · 팝업 입력은 그대로 (N-F2)
  const accountLoad = usePayOptions('/api/finance/connect/accounts/')
  const cardLoad = usePayOptions('/api/finance/connect/cards/')
  const accounts = pickable(accountLoad.items, 'accountId')
  const cards = pickable(cardLoad.items, 'cardId')

  const onName = (e) => {
    if (catTouched) return
    setCat(guessCat(e.target.value) ?? '')
  }

  const choosePay = (value) => { setPay(value); setPayPick(''); setError('') }

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
    e.preventDefault()  // 페이지 새로고침 막기
    const form = new FormData(e.currentTarget)
    const name = String(form.get('fxName') || '').trim()
    const amount = Number(form.get('fxAmount') || 0)
    const day = form.get('fxDay') ? Number(form.get('fxDay')) : null
    if (!name) { setError('항목 이름을 입력해 주세요.'); return }
    if (amount < MIN_AMOUNT) { setError('금액은 1,000원 이상 입력해 주세요.'); return }
    if (amount > MAX_AMOUNT) { setError('금액은 1억 원까지 입력할 수 있어요.'); return }
    if (!cat) { setError('카테고리를 골라 주세요.'); return }
    if (pay !== 'none' && !payPick) { setError(pay === 'card' ? '결제 카드를 골라 주세요.' : '출금 계좌를 골라 주세요.'); return }
    if (saving) return
    const full = {
      name, amount, paymentDay: day, category: catCode(cat),
      paymentAccountId: pay === 'account' ? payPick : null, paymentCardId: pay === 'card' ? payPick : null,
    }
    if (!editing) { send('/api/budgets/fixed/', 'POST', full, { requestKey }); return }
    // 수정은 바꾼 칸만 (안 보낸 칸 = 그대로). 결제 수단은 둘 중 하나라도 바뀌면 두 칸을 같이 보낸다
    const before = { name: item.name, amount: item.amount, paymentDay: item.paymentDay, category: item.category }
    const body = Object.fromEntries(Object.entries(before).filter(([k, v]) => full[k] !== v).map(([k]) => [k, full[k]]))
    if (full.paymentAccountId !== (item.paymentAccount?.accountId ?? null) || full.paymentCardId !== (item.paymentCard?.cardId ?? null)) {
      Object.assign(body, { paymentAccountId: full.paymentAccountId, paymentCardId: full.paymentCardId })
    }
    if (Object.keys(body).length === 0) { closePopup(); return }
    send(`/api/budgets/fixed/${item.recurringId}/`, 'PATCH', body)
  }

  // 지금 저장된 계좌·카드가 후보 목록에 없어도(관리 대상에서 뺐어도) 이름은 보이게 둔다
  const listed = pay === 'card' ? cards : accounts
  const load = pay === 'card' ? cardLoad : accountLoad
  const loadNote = payOptionsNote(load.status, listed.length, pay === 'card' ? '카드' : '계좌')
  const options = !payPick || listed.some((o) => o.id === payPick) ? listed : [{ id: payPick, name: method?.displayName }, ...listed]

  return (
    <Popup label="고정지출 수정" sub>
      <div className="mc-eyebrow">{editing ? '고정지출 수정' : '고정지출 추가'}</div>
      <h1>{editing ? item.name : '매달 나가는 돈 추가'}</h1>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit}>
        {/* defaultValue = 처음 채워 둘 값 (수정일 때만) */}
        <label className="mc-field">항목 이름<input name="fxName" placeholder="월세, 통신비 등" type="text" maxLength={100} defaultValue={item?.name} onChange={onName} /></label>
        <label className="mc-field">금액<MoneyInput name="fxAmount" placeholder="예: 55,000" defaultValue={item?.amount} /></label>
        <label className="mc-field">출금일
          <select name="fxDay" defaultValue={item?.paymentDay ?? ''}>
            {MONTH_DAYS.map((d) => <option key={d} value={d}>매월 {d}일</option>)}
            <option value="">모름</option>
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
        {disconnected && pay !== 'none' && !payPick && <p className="mc-note" style={{ padding: 0 }}>연결 해제된 {item.paymentCard ? '카드' : '계좌'}예요. 다시 골라 주세요.</p>}
        {pay === 'none' ? (
          <p className="mc-note" style={{ padding: 0 }}>출금 계좌를 몰라도 저장돼요. 나중에 고를 수 있어요.</p>
        ) : (
          <label className="mc-field">{pay === 'card' ? '결제 카드' : '출금 계좌'}
            <select value={payPick} onChange={(e) => { setPayPick(e.target.value); setError('') }}>
              <option value="" disabled>{pay === 'card' ? '카드 선택' : '계좌 선택'}</option>
              {options.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </label>
        )}
        {/* 목록을 못 받았으면 빈 목록이라고 하지 않고 이유를 보여 준다. 저장된 결제 수단은 그대로 남는다 (모름으로 바꾸지 않음) */}
        {pay !== 'none' && loadNote && (
          <p className="mc-note" role={load.status === 'ready' || load.status === 'loading' ? undefined : 'alert'} style={{ padding: 0 }}>
            {loadNote}
            {['error', 'consent', 'login'].includes(load.status) && <> <button type="button" className="mc-link" disabled={load.retrying} onClick={load.retry}>다시 불러오기</button></>}
          </p>
        )}
        {pay === 'card' && <p className="mc-note" style={{ padding: 0 }}>카드로 내는 돈은 카드값 안에 들어 있어서 따로 빼지 않아요. 저장하면 이 카드에서 비슷한 결제를 찾아 맞는지 물어볼게요.</p>}

        <label className="mc-field">카테고리
          <select value={cat} onChange={(e) => { setCat(e.target.value); setCatTouched(true); setError('') }}>
            <option value="" disabled>카테고리 선택</option>
            {PICK_CATS.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <p className="mc-note" style={{ padding: 0 }}>매달 나가는 돈만 넣어요. 커피처럼 자주 사는 건 생활비 예산에서 챙겨요. 저축·대출 상환·카드값도 따로 챙겨요.</p>
        <div className="mc-error" role="alert">{error}</div>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit" disabled={saving}>{saving ? '저장 중…' : editing ? '수정 저장' : '추가하기'}</button></div>
      </form>
      {canStop && !stopAsk && <button type="button" className="mc-secondary" onClick={() => setStopAsk(true)}>더 이상 안 나가요</button>}
      {canStop && stopAsk && (
        <div className="mc-fixed-stop" role="alert">
          <p><b>{item.name} 고정지출을 그만할까요?</b><br />다음 달 예산부터 빠지고, 이번 주기에 아직 안 낸 회차도 '꼭 나갈 돈'에서 빠져요. 앱의 고정지출 관리만 중지돼요. 자동이체 · 구독 해지는 그 서비스에서 해 주세요.</p>
          <div className="mc-inline">
            <button type="button" className="mc-secondary mc-danger" disabled={saving}
              onClick={() => send(`/api/budgets/fixed/${item.recurringId}/`, 'DELETE')}>{saving ? '처리 중…' : '그만하기'}</button>
            <button type="button" className="mc-secondary" onClick={() => setStopAsk(false)}>취소</button>
          </div>
        </div>
      )}
    </Popup>
  )
}
