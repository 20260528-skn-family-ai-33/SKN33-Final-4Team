// 추천 예산 확인
// 금액은 시안 예시값 — 예산 추천 API 가 생기면 바꾼다. 막대 길이는 금액으로 계산한다
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import Icon from '../components/Icon.jsx'
import LockIcon from '../components/LockIcon.jsx'
import { usePopup } from '../popups/context.js'
import AdjustChatPopup from '../popups/AdjustChatPopup.jsx'
import { usePicked } from '../utils/usePicked.js'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const MONTHLY = 2_800_000
// [CSS 이름(색), 이름, 금액]
const SPLIT = [
  ['fix', '고정지출', 770_000], ['save', '저축', 410_000], ['live', '생활비', 1_120_000],
  ['irr', '비정기 준비금', 78_000], ['cushion', '머니 쿠션 적립', 210_000], ['left', '여윳돈', 212_000],
]
const LIVING = 1_120_000
const CATS = [
  ['식비', 'Utensils', 400_000], ['주거/통신', 'House', 120_000], ['교통/차량', 'Bus', 100_000],
  ['생활용품', 'ShoppingBasket', 80_000], ['건강/미용', 'HeartPulse', 120_000], ['여가/관계', 'Users', 300_000],
]
const SHADES = [100, 78, 60, 45, 32, 20]  // 카테고리 막대 색 진하기 (%)
const shade = (i) => `color-mix(in srgb,var(--mc-brand) ${SHADES[i]}%,var(--mc-paper))`
const pct = (part, whole) => (part / whole) * 100

export default function RecommendPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const locks = usePicked()  // 자물쇠 건 카테고리

  const onSubmit = (e) => {
    e.preventDefault()
    navigate(PATHS.home)  // 저장은 API 연결 후
  }

  return (
    <Layout title="추천 예산 확인" showBack foot={<button className="mc-primary" form="mc-form" type="submit">이 예산으로 시작하기</button>}>
      <StepBar step={7} />
      <div className="mc-eyebrow">추천 예산 확인</div>
      <h1>카테고리별 예산을<br />확인하고 고쳐 주세요</h1>
      <p className="mc-muted">내 정보와 소비 통계로 맞춘 추천 예산이에요.</p>

      <div className="mc-card">
        <div className="mc-row"><h2>한 달 나눠보기</h2><span className="mc-muted">월 {won(MONTHLY)}</span></div>
        <div className="mc-split" role="img" aria-label={SPLIT.map(([, n, v]) => `${n} ${won(v)}`).join(', ')}>
          {SPLIT.map(([k, n, v]) => <span key={n} className={`mc-split-${k}`} style={{ width: `${pct(v, MONTHLY)}%` }}></span>)}
        </div>
        <div className="mc-split-legend">
          {SPLIT.map(([k, n, v]) => <div key={n}><i className={`mc-split-${k}`}></i><span>{n}</span><strong>{won(v)}</strong></div>)}
        </div>
      </div>

      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <div className="mc-card" style={{ marginBottom: 0 }}>
          <div className="mc-row"><h2>생활비 카테고리</h2><span className="mc-muted">{won(LIVING)}</span></div>
          <div className="mc-split" role="img" aria-label={CATS.map(([n, , v]) => `${n} ${Math.round(pct(v, LIVING))}%`).join(', ')}>
            {CATS.map(([n, , v], i) => <span key={n} style={{ width: `${pct(v, LIVING)}%`, background: shade(i) }}></span>)}
          </div>
          <div className="mc-cat-legend">
            {CATS.map(([n, , v], i) => <div key={n}><i style={{ background: shade(i) }}></i><span>{n}</span><b>{Math.round(pct(v, LIVING))}%</b></div>)}
          </div>
          <div className="mc-row mc-cat-head"><span>카테고리별 예산</span><span>고정</span></div>
          <div className="mc-cat-list">
            {CATS.map(([n, icon, v]) => (
              <div className="mc-catrow" key={n}>
                <span className="mc-catname"><span className="mc-cat-ic"><Icon name={icon} /></span>{n}</span>
                <strong>{won(v)}</strong>
                <button type="button" className="mc-lockt" aria-label={`${n} 금액 고정`} aria-pressed={locks.has(n)} onClick={() => locks.toggle(n)}>
                  <LockIcon />
                </button>
              </div>
            ))}
          </div>
          <div className="mc-chat-alt">
            <span>꼭 바꾸고 싶은 부분이 있나요?</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<AdjustChatPopup />)}>챗봇과 조정하기 ›</button>
          </div>
        </div>
        <p className="mc-note">시안 예시: 생활비를 기본 비율로 나눴어요. 카테고리 배분 방법(거래 이력·모델·통계 비중)은 확정 전이에요.</p>
        <div className="mc-error" role="alert"></div>
      </form>
    </Layout>
  )
}
