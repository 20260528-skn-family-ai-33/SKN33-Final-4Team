// 팝업: 대출 전체 완납 처리 확인 (docs/api/loans.md 13번, R19) — '이번 회차 납부'와 다른 동작
//   POST /api/finance/loans/<id>/payoff/ — 요청 번호는 팝업을 열 때 만든다 (다시 눌러도 같은 번호)
//   오늘 뒤의 안 낸 회차는 취소되고, 오늘 · 지난 회차와 일부라도 낸 회차는 그대로 남는다 → [납부 거래 확인]으로 정리
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { PATHS } from '../routes.js'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { apiSend, newRequestKey } from '../utils/api.js'
import LoanDueRow from '../components/LoanDueRow.jsx'

const todayKst = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })  // YYYY-MM-DD

export default function LoanPayoffPopup({ loan, onDone, onCheckDue }) {
  const { closePopup } = usePopup()
  const navigate = useNavigate()
  const [requestKey] = useState(newRequestKey)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  const today = todayKst()
  const dues = loan.recentDues ?? []
  const canceled = dues.filter((d) => d.dueDate > today && d.paidAmount === 0 && d.paymentState === 'unpaid')
  const remaining = dues.filter((d) => !canceled.includes(d) && d.paymentState !== 'paid')

  const submit = async () => {
    if (pending.current) return
    pending.current = true
    setSaving(true)
    setError('')
    try {
      const saved = await apiSend(`/api/finance/loans/${loan.loanId}/payoff/`, 'POST', {}, { requestKey })
      onDone?.(saved)
      closePopup()
    } catch (err) {
      if (err.code === 'not_authenticated') navigate(PATHS.login, { replace: true })
      setError(err.status ? err.message : '서버에 연결하지 못했어요. 다시 시도해 주세요.')
    } finally {
      pending.current = false
      setSaving(false)
    }
  }

  return (
    <Popup label="대출 전체 완납 처리" sub>
      <div className="mc-eyebrow">{loan.productName ?? '대출'} · {loan.lenderName ?? '기관 확인 필요'}</div>
      <h1>이 대출을 전부 갚았나요?</h1>
      {/* 11/9: 긴 설명 목록 → 완납하면 바뀌는 것 표 + 회차 카드 */}
      <p className="mc-muted">남은 원금을 모두 갚아 <strong>상환을 끝내는 처리</strong>예요. 이번 달 한 번 낸 건 [납부 거래 확인]에서 확인해요.</p>
      <div className="mc-card">
        <div className="mc-keyvalue"><span>남은 금액</span><strong>0원으로 표시</strong></div>
        <div className="mc-keyvalue"><span>지킬 돈</span><strong>월 상환액이 빠져요</strong></div>
        <div className="mc-keyvalue"><span>되돌리기</span><strong>[완납 취소]로 바로</strong></div>
        <p className="mc-note" style={{ paddingBottom: 0 }}>직접 알려 주신 완납으로 오늘 날짜에 저장돼요. 승인한 예산은 그대로이고, 예산 초안은 다시 만들어야 해요.</p>
      </div>
      {canceled.length > 0 && <>
        <h3 className="mc-loan-h">취소되는 앞으로의 납부</h3>
        <div className="mc-card mc-loan-dues">{canceled.map((d) => <LoanDueRow key={d.dueId} d={d} tag="취소돼요" />)}</div>
      </>}
      {remaining.length > 0 && <>
        <h3 className="mc-loan-h">그대로 남는 납부 <small>실제로 냈다면 출금을 연결해 주세요</small></h3>
        <div className="mc-card mc-loan-dues">{remaining.map((d) => (
          <LoanDueRow key={d.dueId} d={d} onOpen={onCheckDue && (() => onCheckDue(d.dueId))} />))}
        </div>
      </>}
      {error && <p className="mc-error" role="alert">{error}</p>}
      <div className="mc-foot">
        <button type="button" className="mc-secondary" onClick={closePopup} disabled={saving}>닫기</button>
        <button type="button" className="mc-primary" onClick={submit} disabled={saving}>{saving ? '처리 중…' : '전부 갚았어요, 상환 종료'}</button>
      </div>
    </Popup>
  )
}
