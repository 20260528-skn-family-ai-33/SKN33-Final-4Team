// 팝업: 계좌 · 카드 고르기 — 거래 화면 '거래 N건' 줄 오른쪽 (11/3: 동작 없던 [소비만 보기] 대신)
// props: accountIds · cardIds = 지금 고른 것, onApply(accountIds, cardIds) = 적용 (거래 목록 API 의 accountId · cardId)
// 목록 = 연결된 계좌 · 카드 (GET /api/finance/connect/accounts/ · /cards/). 카드 조회 동의가 없으면 카드는 안내만
import { useState } from 'react'
import Popup from './Popup.jsx'
import HelpPopup from './HelpPopup.jsx'
import { usePopup } from './context.js'
import { useApi } from '../utils/api.js'

// '신한 주거래 우대통장 · ****0303' → ['신한 주거래 우대통장', '****0303'] (끝 4자리는 아래 줄 회색)
const splitName = (name) => {
  const at = name.lastIndexOf(' · ')
  return at < 0 ? [name, null] : [name.slice(0, at), name.slice(at + 3)]
}

function Section({ title, q, idKey, picked, toggle }) {
  const items = q.data?.items ?? []
  const why = q.data ? (items.length === 0 ? `연결된 ${title}가 없어요.` : null)
    : q.error?.code === 'consent_required' ? '조회 동의가 필요해요.' : q.error ? '목록을 불러오지 못했어요.' : '불러오는 중…'
  return (
    <section className="mc-filter-sec" aria-label={title}>
      <div className="mc-eyebrow">{title}</div>
      {why && <p className="mc-muted">{why}</p>}
      {items.map((x) => {
        const [name, last4] = splitName(x.displayName)
        return (
          <label className="mc-filter-item" key={x[idKey]}>
            <input type="checkbox" checked={picked.has(x[idKey])} onChange={() => toggle(x[idKey])} />
            <span className="mc-grow">{name}{last4 && <small>{last4}</small>}</span>
          </label>
        )
      })}
    </section>
  )
}

export default function FilterPopup({ accountIds = [], cardIds = [], onApply }) {
  const { openPopup, closePopup } = usePopup()
  const accounts = useApi('/api/finance/connect/accounts/')
  const cards = useApi('/api/finance/connect/cards/')
  const [picked, setPicked] = useState(() => new Set([...accountIds, ...cardIds]))
  const toggle = (id) => setPicked((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const apply = (ids) => {
    const of = (q, key) => (q.data?.items ?? []).map((x) => x[key]).filter((id) => ids.has(id))
    onApply(of(accounts, 'accountId'), of(cards, 'cardId'))
    closePopup()
  }

  return (
    <Popup label="계좌 · 카드 고르기">
      <h2>계좌 · 카드 고르기</h2>
      <p className="mc-muted">고른 계좌 · 카드의 거래만 보여요. 아무것도 안 고르면 전체예요.</p>
      <Section title="계좌" q={accounts} idKey="accountId" picked={picked} toggle={toggle} />
      <Section title="카드" q={cards} idKey="cardId" picked={picked} toggle={toggle} />
      <div className="mc-inline mc-disc-act">
        <button type="button" className="mc-secondary" onClick={() => apply(new Set())}>모두 해제</button>
        <button type="button" className="mc-primary" onClick={() => apply(picked)}>적용</button>
      </div>
      <button type="button" className="mc-link" onClick={() => openPopup(<HelpPopup />)}>이체 · 저축 제외 기준 보기</button>
    </Popup>
  )
}
