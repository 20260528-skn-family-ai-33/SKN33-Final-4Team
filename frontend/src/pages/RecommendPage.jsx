// 추천 예산 확인
// 금액은 시안 예시값 — 예산 추천 API 가 생기면 바꾼다. 막대 길이는 금액으로 계산한다
// 생활비·저축·쿠션 적립·여윳돈은 지킬 돈 설정에서 넘겨받은 값 (바로 들어오면 생활 안정형 추천값)
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import Icon from '../components/Icon.jsx'
import LockIcon from '../components/LockIcon.jsx'
import { usePopup } from '../popups/context.js'
import AdjustChatPopup from '../popups/AdjustChatPopup.jsx'
import { usePicked } from '../utils/usePicked.js'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'
import { BUDGET_CATS, catIcon } from '../utils/categories.js'
import { manwon } from '../utils/adjust.js'
import { FIXED, MONTHLY, recommendFor } from '../utils/direction.js'

// 카테고리별 추천 금액 (시안 예시값, 생활비 1,120,000원 기준). 목록·아이콘은 utils/categories.js
const BASE_LIVING = 1_120_000
const AMOUNT = {
  식비: 330_000, '주거·통신': 110_000, '교통·차량': 90_000, 생활용품: 60_000, '패션·쇼핑': 80_000,
  '의료·건강': 50_000, '뷰티·미용': 60_000, 교육: 50_000, '문화·여가': 170_000, '여행·숙박': 60_000, 기타: 60_000,
}
// 내 정보 입력에서 받은 카테고리 최소 금액 (시안 예시값). 추천 금액은 이 금액 이상으로만 나온다
const MIN = { 식비: 300_000, '교통·차량': 80_000 }

// 생활비가 바뀌면 위 예시 금액을 같은 비율로 다시 나눈다 (1만원 단위, 최소 금액 이상, 남는 차이는 기타에)
// 시안용 임시 계산 — 카테고리 예산 ML 모델이 생기면 그 결과로 바꾼다
function splitLiving(living) {
  const r10k = (n) => Math.round(n / 10_000) * 10_000
  const out = Object.fromEntries(BUDGET_CATS.map((n) => [n, Math.max(MIN[n] ?? 0, r10k(((AMOUNT[n] ?? 0) * living) / BASE_LIVING))]))
  const diff = living - Object.values(out).reduce((a, b) => a + b, 0)
  if ('기타' in out) out.기타 = Math.max(0, out.기타 + diff)
  return out
}
// 카테고리 막대 색 진하기: 첫 칸 100% → 마지막 칸 18% (카테고리 수가 바뀌어도 자동으로 나눔)
const shade = (i) => `color-mix(in srgb,var(--mc-brand) ${Math.round(100 - (82 * i) / Math.max(BUDGET_CATS.length - 1, 1))}%,var(--mc-paper))`
const pct = (part, whole) => (part / whole) * 100

export default function RecommendPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const { state } = useLocation()
  const rec = recommendFor(state?.dir)
  const LIVING = state?.livingBudget ?? rec.livingBudget
  const SAVING = state?.savingFloor ?? rec.savingFloor
  const CUSHION = state?.cushionMonthly ?? rec.cushionMonthly
  const SPARE = state?.spare ?? MONTHLY - FIXED - LIVING - SAVING - CUSHION
  // [CSS 이름(색), 이름, 금액]
  const SPLIT = [
    ['fix', '고정지출', FIXED], ['save', '저축', SAVING], ['live', '생활비', LIVING],
    ['cushion', '머니 쿠션 적립', CUSHION], ['left', '여윳돈', SPARE],   // 비정기 준비금은 10/24 결정 AB로 2차
  ]
  const amount = splitLiving(LIVING)
  const CATS = BUDGET_CATS.map((n) => [n, catIcon(n), amount[n] ?? 0])
  // 최소 금액 합계가 생활비보다 크면 예산을 못 만든다 → 부족액 안내 후 수정 받기
  const MIN_SHORT = Object.values(MIN).reduce((a, b) => a + b, 0) - LIVING
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
                <span className="mc-catname"><span className="mc-cat-ic"><Icon name={icon} /></span>{n}{MIN[n] && <em className="mc-min-tag">최소 {manwon(MIN[n])}</em>}</span>
                <strong>{won(v)}</strong>
                <button type="button" className="mc-lockt" aria-label={`${n} 금액 고정`} aria-pressed={locks.has(n)} onClick={() => locks.toggle(n)}>
                  <LockIcon />
                </button>
              </div>
            ))}
          </div>
          {MIN_SHORT > 0 && (
            <p className="mc-min-warn" role="alert">카테고리 최소 금액 합계가 생활비보다 <b>{won(MIN_SHORT)}</b> 많아요. 내 정보에서 최소 금액을 줄여 주세요.</p>
          )}
          <div className="mc-chat-alt">
            <span>꼭 바꾸고 싶은 부분이 있나요?</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<AdjustChatPopup />)}>챗봇과 조정하기 ›</button>
          </div>
        </div>
        <p className="mc-note">최소 표시는 내 정보에서 정한 카테고리 최소 금액이에요. 그 밑으로는 줄지 않고, 자물쇠를 걸면 금액 그대로 고정돼요. 시안 예시: 생활비를 기본 비율로 나눴어요. 카테고리 배분 방법(거래 이력·모델·통계 비중)은 확정 전이에요.</p>
        <div className="mc-error" role="alert"></div>
      </form>
    </Layout>
  )
}
