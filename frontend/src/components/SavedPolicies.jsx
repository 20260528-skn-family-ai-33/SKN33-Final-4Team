import { Link } from 'react-router'
import { useApi } from '../utils/api.js'
import { PATHS } from '../routes.js'
import ApiStatus from './ApiStatus.jsx'

const STATUS = { open: '신청 중', always: '상시 신청', upcoming: '신청 예정', unknown: '신청 기간 확인 필요', closed: '신청 마감' }

function officialLink(value) {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && url.hostname ? url.href : null
  } catch { return null }
}

export default function SavedPolicies() {
  const { data, error, reload } = useApi('/api/policies/saved/')
  if (error || !data) return <ApiStatus error={error} onRetry={reload} />
  if (!data.items.length) return <div className="mc-card">
    <h2>아직 저장한 정책이 없어요</h2>
    <p className="mc-muted">청년 정책 추천에서 관심 있는 정책을 저장해 보세요.</p>
    <Link className="mc-secondary" to={PATHS.assistant}>챗봇에서 정책 찾아보기</Link>
  </div>
  return <div className="mc-saved-policies">
    <p className="mc-muted">저장한 정책 {data.items.length}개 · 최근 저장한 순서예요.</p>
    {data.items.map((policy) => {
      const href = officialLink(policy.link)
      return <article className="mc-card" key={policy.policyNo} aria-label={policy.name}>
        <h3 className="mc-policy-title">{policy.name}</h3>
        <p>{STATUS[policy.applyState] ?? STATUS.unknown}{policy.applyEnd && ` · 마감일 ${policy.applyEnd}`}</p>
        {policy.active === false && <p className="mc-note">현재 정책 목록에서 빠진 정책이에요. 운영 기관의 최신 공고를 확인해 주세요.</p>}
        <p className="mc-muted">저장일 {policy.savedAt?.slice(0, 10) ?? '확인 필요'}</p>
        {href ? <a className="mc-secondary" href={href} target="_blank" rel="noopener noreferrer" aria-label={`${policy.name} 공고 원문 보기 (새 탭)`}>공고 원문 보기 ↗</a>
          : <p className="mc-note">등록된 링크가 없어요. 온통청년에서 정책명으로 찾아 주세요.</p>}
      </article>
    })}
  </div>
}
