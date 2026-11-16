import { useState } from 'react'
import { Link } from 'react-router'
import { apiSend, useApi } from '../utils/api.js'
import { daysLeft } from '../utils/policyCards.js'
import { PATHS } from '../routes.js'
import ApiStatus from './ApiStatus.jsx'

const STATUS = { open: '신청 중', always: '상시 신청', upcoming: '신청 예정', unknown: '신청 기간 확인 필요', closed: '신청 마감' }
const URGENT_DAYS = 7  // 마감 일주일 안이면 배지를 강조색으로

// 마감 · 수집 종료 정책은 아래 '지난 정책'으로 접는다
const isPast = (policy) => policy.applyState === 'closed' || policy.active === false

function officialLink(value) {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && url.hostname ? url.href : null
  } catch { return null }
}

// 상태 배지 + 신청 중이고 마감일이 있으면 D-day ('오늘 마감' · '마감 D-3')
function Badges({ policy }) {
  const past = isPast(policy)
  const left = policy.applyState === 'open' && policy.applyEnd ? daysLeft(policy.applyEnd) : null
  return <div className="mc-saved-badges">
    <span className={`mc-saved-badge${past ? ' is-closed' : ''}`}>{policy.active === false ? '목록에서 빠짐' : STATUS[policy.applyState] ?? STATUS.unknown}</span>
    {left !== null && left >= 0 && <span className={`mc-saved-badge${left <= URGENT_DAYS ? ' is-urgent' : ''}`}>{left === 0 ? '오늘 마감' : `마감 D-${left}`}</span>}
  </div>
}

function PolicyCard({ policy, onRemove, busy }) {
  const href = officialLink(policy.link)
  const apply = `${STATUS[policy.applyState] ?? STATUS.unknown}${policy.applyEnd ? ` · 마감일 ${policy.applyEnd}` : ''}`
  const rows = [['지역', policy.region], ['지원 형태', policy.supportType], ['지원', policy.support], ['신청', apply],
    ['저장일', policy.savedAt?.slice(0, 10) ?? '확인 필요']].filter(([, value]) => value)
  return <article className="mc-card mc-saved-card" aria-label={policy.name}>
    <Badges policy={policy} />
    <div className="mc-policy-box">
      <div className="mc-policy-head"><h3 className="mc-policy-title">{policy.name}</h3></div>
      {policy.active === false && <p className="mc-policy-note">현재 정책 목록에서 빠진 정책이에요. 운영 기관의 최신 공고를 확인해 주세요.</p>}
      <dl className="mc-policy-table">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    </div>
    <div className="mc-policy-actions">
      {href && <a className="mc-secondary" href={href} target="_blank" rel="noopener noreferrer" aria-label={`${policy.name} 공고 원문 보기 (새 탭)`}>공고 보기 ↗</a>}
      <button type="button" className="mc-secondary" disabled={busy} onClick={() => onRemove(policy)} aria-label={`${policy.name} 저장 해제`}>저장 해제</button>
    </div>
    {!href && <p className="mc-note">등록된 링크가 없어요. 온통청년에서 정책명으로 찾아 주세요.</p>}
  </article>
}

export default function SavedPolicies() {
  const { data, error, reload } = useApi('/api/policies/saved/', { keepData: true })
  const [hidden, setHidden] = useState(() => new Set())  // 해제한 정책은 다시 받아오기 전에도 바로 뺀다
  const [undo, setUndo] = useState(null)  // 방금 해제한 정책 (되돌리기)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState('')

  const send = async (policy, method) => {
    setBusy(true); setFailed('')
    try {
      await apiSend(`/api/policies/${encodeURIComponent(policy.policyNo)}/saved/`, method)
      setHidden((prev) => {
        const next = new Set(prev)
        if (method === 'DELETE') next.add(policy.policyNo)
        else next.delete(policy.policyNo)
        return next
      })
      setUndo(method === 'DELETE' && policy.active !== false ? policy : null)  // 목록에서 빠진 정책은 다시 저장할 수 없다
      reload()
    } catch (e) {
      setFailed(e.message || '처리하지 못했어요. 다시 시도해 주세요.')
    } finally {
      setBusy(false)
    }
  }

  if (!data) return <ApiStatus error={error} onRetry={reload} />
  const items = data.items.filter((policy) => !hidden.has(policy.policyNo))
  const now = items.filter((policy) => !isPast(policy))
  const past = items.filter(isPast)
  const card = (policy) => <PolicyCard key={policy.policyNo} policy={policy} busy={busy} onRemove={(p) => send(p, 'DELETE')} />
  const notices = <>
    {undo && <p className="mc-saved-undo" role="status">‘{undo.name}’ 저장을 해제했어요.
      <button type="button" className="mc-link" disabled={busy} onClick={() => send(undo, 'PUT')}>되돌리기</button></p>}
    {failed && <p className="mc-error" role="alert">{failed}</p>}
  </>
  if (!items.length) return <>{notices}<div className="mc-card">
    <h2>아직 저장한 정책이 없어요</h2>
    <p className="mc-muted">청년 정책 추천에서 관심 있는 정책을 저장해 보세요.</p>
    <Link className="mc-secondary" to={PATHS.assistant}>챗봇에서 정책 찾아보기</Link>
  </div></>
  return <div className="mc-saved-policies">
    {notices}
    <p className="mc-muted">신청할 수 있는 정책 {now.length}개 · 최근 저장한 순서예요.</p>
    {now.length ? now.map(card) : <p className="mc-note">지금 신청할 수 있는 저장 정책이 없어요.</p>}
    {past.length > 0 && <details className="mc-saved-past">
      <summary>지난 정책 {past.length}개 (마감 · 목록에서 빠짐)</summary>
      {past.map(card)}
    </details>}
  </div>
}
