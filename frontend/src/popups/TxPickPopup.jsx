// 팝업: 어떤 카테고리인가요? — "카테고리 확인 필요" 거래 고르기
// 열면 거래 한 건 보기(GET /api/finance/transactions/<id>/)로 확인 필요 이유 · 후보 카테고리를 받아 위에 보여 준다 (10/30 A안)
// 고르면 거래 분류 수정(PATCH { category, applyToFuture }) → 목록 새로고침 (docs/api/transactions.md)
// '앞으로도 적용'은 서버가 canApplyToFuture 로 허락한 거래(페이 충전·이체 아님)에만 보여 준다 (10/30)
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'
import { apiSend, useApi } from '../utils/api.js'
import { toast } from '../utils/toast.js'

// props: tx = 고를 거래, categories = 고를 수 있는 카테고리 [{ code, name }], onSaved = 저장한 뒤 부를 함수(목록 새로고침)
export default function TxPickPopup({ tx, categories, onSaved }) {
  const { closePopup } = usePopup()
  const { data } = useApi(`/api/finance/transactions/${tx.id}/`)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [applyToFuture, setApplyToFuture] = useState(false)
  const suggested = data?.suggestion?.candidates ?? []
  const others = categories.filter((c) => !suggested.some((s) => s.category === c.code))

  async function pick(code) {
    setSaving(true)
    setError(null)
    try {
      await apiSend(`/api/finance/transactions/${tx.id}/`, 'PATCH', { category: code, applyToFuture: Boolean(data?.canApplyToFuture && applyToFuture) })
      onSaved?.()
      closePopup()
      toast('카테고리를 저장했어요')
    } catch (err) {
      setError(err.message)
      setSaving(false)
    }
  }

  const option = (code, name) => (
    <button type="button" className="mc-lock-opt" key={code} disabled={saving} onClick={() => pick(code)}>
      <span className="mc-grow">{name}</span>
    </button>
  )

  return (
    <Popup label="카테고리 고르기">
      <h2>어떤 카테고리인가요?</h2>
      <p className="mc-muted" style={{ margin: '4px 0 12px' }}>{tx.name} · {won(tx.net ?? tx.amount)}</p>
      {data?.suggestion?.reason && <p className="mc-pend-why">{data.suggestion.reason}</p>}
      {data?.canApplyToFuture && (
        <label className="mc-field mc-check">
          <input type="checkbox" checked={applyToFuture} onChange={(e) => setApplyToFuture(e.target.checked)} />
          앞으로 이 가게 거래도 이 카테고리로 분류
        </label>
      )}
      {/* PR #114 리뷰: 앞으로 들어올 거래뿐 아니라 지금 '확인 필요'인 같은 가게 거래도 바뀐다는 걸 알려 준다 */}
      {data?.canApplyToFuture && applyToFuture && <p className="mc-note" style={{ padding: '0 0 8px', marginTop: -8 }}>지금 확인이 필요한 같은 가게 거래도 함께 바뀌어요.</p>}
      {suggested.length > 0 && (
        <>
          <p className="mc-muted">추천</p>
          <div className="mc-lock-list">{suggested.map((s) => option(s.category, s.categoryName))}</div>
          <p className="mc-muted">다른 카테고리</p>
        </>
      )}
      <div className="mc-lock-list">{others.map((c) => option(c.code, c.name))}</div>
      {error && <p className="mc-error" role="alert">{error}</p>}
    </Popup>
  )
}
