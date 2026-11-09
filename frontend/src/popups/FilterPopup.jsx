// 팝업: 필터 (거래 유형 · 카테고리 · 계좌 · 카드) — 거래 화면 '거래 N건' 줄 오른쪽 (11/3: 동작 없던 [소비만 보기] 대신, 11/9: 카테고리 칩도 이 안으로)
// props: categories = 고를 카테고리 [{ code, name }], pendingCount = 확인 필요 건수, types · categoryCodes · accountIds · cardIds = 지금 고른 것
//        onApply(categoryCodes, accountIds, cardIds, types) = 적용 (거래 목록 API 의 category · accountId · cardId · transactionType)
// 목록 = 연결된 계좌 · 카드 (GET /api/finance/connect/accounts/ · /cards/). 카드 조회 동의가 없으면 카드는 안내만
import { useState } from 'react'
import Popup from './Popup.jsx'
import HelpPopup from './HelpPopup.jsx'
import { usePopup } from './context.js'
import { useApi } from '../utils/api.js'
import { PENDING } from '../utils/categories.js'

// '신한 주거래 우대통장 · ****0303' → ['신한 주거래 우대통장', '****0303'] (끝 4자리는 아래 줄 회색)
// 거래 유형 (API transactionType). 이체 = 남에게 보낸 이체 + 내 계좌 이체
const TYPES = [{ code: 'spending', name: '소비' }, { code: 'income', name: '입금' }, { code: 'transfer', name: '이체' }]

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

export default function FilterPopup({ categories = [], pendingCount = 0, categoryCodes = [], types = [], accountIds = [], cardIds = [], onApply }) {
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
  const [cats, setCats] = useState(categoryCodes)
  const [typ, setTyp] = useState(types)
  const toggleType = (code) => setTyp((prev) => prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code])
  // 확인 필요는 다른 카테고리와 섞지 않는다 — 고르면 그것만, 다른 걸 고르면 확인 필요는 꺼진다
  const toggleCat = (code) => setCats((prev) => code === PENDING.code ? (prev.includes(code) ? [] : [code])
    : prev.includes(code) ? prev.filter((c) => c !== code) : [...prev.filter((c) => c !== PENDING.code), code])
  const apply = (ids, codes, typeCodes) => {
    const of = (q, key) => (q.data?.items ?? []).map((x) => x[key]).filter((id) => ids.has(id))
    onApply(codes, of(accounts, 'accountId'), of(cards, 'cardId'), typeCodes)
    closePopup()
  }

  return (
    <Popup label="필터">
      <h2>필터</h2>
      <p className="mc-muted">고른 조건의 거래만 보여요. 아무것도 안 고르면 전체예요.</p>
      <section className="mc-filter-sec" aria-label="거래 유형">
        <div className="mc-eyebrow">거래 유형</div>
        <div className="mc-chipbar">
          {TYPES.map((t) => (
            <button type="button" key={t.code} aria-pressed={typ.includes(t.code)} onClick={() => toggleType(t.code)}>{t.name}</button>
          ))}
        </div>
      </section>
      <section className="mc-filter-sec" aria-label="카테고리">
        <div className="mc-eyebrow">카테고리</div>
        <p className="mc-muted">카테고리는 소비 거래에만 있어요.</p>
        <div className="mc-chipbar">
          <button type="button" className="mc-chip-warn" aria-pressed={cats.includes(PENDING.code)} onClick={() => toggleCat(PENDING.code)}>{PENDING.name} {pendingCount}</button>
          {categories.map((c) => (
            <button type="button" key={c.code} aria-pressed={cats.includes(c.code)} onClick={() => toggleCat(c.code)}>{c.name}</button>
          ))}
        </div>
      </section>
      <Section title="계좌" q={accounts} idKey="accountId" picked={picked} toggle={toggle} />
      <Section title="카드" q={cards} idKey="cardId" picked={picked} toggle={toggle} />
      <div className="mc-inline mc-disc-act">
        <button type="button" className="mc-secondary" onClick={() => apply(new Set(), [], [])}>모두 해제</button>
        <button type="button" className="mc-primary" onClick={() => apply(picked, cats, typ)}>적용</button>
      </div>
      <button type="button" className="mc-link" onClick={() => openPopup(<HelpPopup />)}>이체 · 저축 제외 기준 보기</button>
    </Popup>
  )
}
