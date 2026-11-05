import { useRef, useState } from 'react'
import { Navigate, useNavigate } from 'react-router'
import { apiSend, useApi } from '../utils/api.js'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

function Review({ item, onResolved }) {
  const [cardId, setCardId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const saving = useRef(false)
  const submit = async (event) => {
    event.preventDefault()
    if (!cardId || saving.current || !item.canResolve) return
    saving.current = true
    setBusy(true)
    setError('')
    try {
      await apiSend(`/api/finance/card-reviews/${item.reviewId}/`, 'PUT', { cardId })
      onResolved(item.reviewId)
    } catch (err) {
      setError(Object.values(err.fields ?? {}).flat()[0] ?? err.message)
    } finally {
      saving.current = false
      setBusy(false)
    }
  }
  return <form className="mc-card mc-form" onSubmit={submit}>
    <h2>{item.merchantName || '가맹점명 없음'}</h2>
    <p>{item.occurredOn} {item.occurredTime || ''} · {item.issuerName || '카드사'}</p>
    <strong>{won(item.amount)}</strong>
    {item.isCanceled && <p className="mc-muted">취소된 거래예요.</p>}
    {item.canceledAmount != null && <p className="mc-muted">확인된 취소액 {won(item.canceledAmount)}</p>}
    {!item.canResolve ? <p role="alert">금액이나 취소 정보를 다시 확인해야 해요. 연결 관리에서 내역을 불러와 주세요.</p> : <>
      <label className="mc-field">이 거래의 결제 카드
        <select value={cardId} onChange={(event) => setCardId(event.target.value)} disabled={busy}>
          <option value="">카드를 선택해 주세요</option>
          {item.candidates.map((card) => <option key={card.cardId} value={card.cardId}>
            {card.displayName}{card.nameMatches ? ' · 카드명 일치' : ''}{!card.isSelected ? ' · 예산 관리 대상 아님' : ''}
          </option>)}
        </select>
      </label>
      <p className="mc-note">카드사 앱의 거래 상세와 비교해 선택해 주세요. 카드명 일치는 참고 정보이며, 확인은 이 거래 한 건에만 적용돼요.</p>
      {item.candidates.length === 0 && <p role="alert">선택할 카드가 없어요. 연결 관리에서 카드 연결을 확인해 주세요.</p>}
      <button className="mc-secondary" type="submit" disabled={!cardId || busy}>{busy ? '확인하는 중…' : '이 카드로 결제한 거래 확인'}</button>
    </>}
    {error && <p className="mc-error" role="alert">{error}</p>}
  </form>
}

export default function CardReviewList({ cardIds, onChanged }) {
  const navigate = useNavigate()
  const query = useApi('/api/finance/card-reviews/')
  const [resolved, setResolved] = useState(new Set())
  if (query.error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  if (query.error) return <div>
    <p className="mc-error" role="alert">{query.error.status ? query.error.message : '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.'}</p>
    {query.error.code === 'consent_required'
      ? <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connect)}>카드 조회 동의하러 가기</button>
      : <button type="button" className="mc-secondary" onClick={query.reload}>다시 시도</button>}
  </div>
  if (!query.data) return <p role="status">확인할 거래를 불러오는 중이에요.</p>
  const items = query.data.items.filter((item) => !resolved.has(item.reviewId)
    && (!cardIds || item.candidates.some((card) => cardIds.includes(card.cardId))))
  return <section aria-label="결제 카드 확인 거래">
    <p role="status">{items.length ? `확인할 거래 ${items.length}건` : '결제 카드를 확인할 거래가 없어요.'}</p>
    {items.map((item) => <Review key={item.reviewId} item={item} onResolved={(id) => {
      setResolved((previous) => new Set([...previous, id]))
      query.reload()
      onChanged?.()
    }} />)}
    {items.length === 0 && <p className="mc-note">조회 기간과 남은 정보도 확인되어야 예산을 계산할 수 있어요.</p>}
  </section>
}
