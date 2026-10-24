// 고정지출 확인 · 고정지출 관리 — 한 화면을 두 곳에서 쓴다 (docs/api/fixed.md)
//   첫 설정 흐름: 월급 다음, 저축 확인 전. 자동으로 찾은 후보를 확인한다 (아래 OnboardingView)
//   마이 > 고정지출 · 홈 '꼭 나갈 돈' (location.state.from === 'my' | 'home'): 확정된 고정지출 + [수정] · 중지 · 미납 [고르기] (ManageView)
// 여기 나오는 건 자동으로 찾은 후보(suggested). [확인하고 다음] = 남은 후보를 모두 확정(confirmed), [고정지출 아니에요] = 무시(ignored)
// 감지 조건 (요구사항 TXN-008): 같은 받는 곳으로 한 달에 1건 · 매달 같은 날 ±3일 · 같은 금액(±10%) · 3달 연속 → 매일 사는 커피 같은 건 안 잡힌다
// 목록은 시안 예시값 — 고정지출 감지 API 가 생기면 바꾼다
// 결제 확인 (10/21 결정, C-14 · C-16): 직접 추가한 카드 고정지출은 서버가 비슷한 결제를 찾아 suggestion 으로 준다 (API: evidenceSuggestion)
//   1건 = "이 결제가 맞나요? [맞아요] [아니에요]" / 여러 건 = "비슷한 결제가 N건 있어요 [고르기]" → EvidencePopup
//   [맞아요] · 고른 결제 = PATCH /api/budgets/fixed/<id>/ { evidenceTransactionId }. [아니에요] = 요청 없음 (다음 달 다시 물어봄)
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from '../popups/context.js'
import FixedPopup from '../popups/FixedPopup.jsx'
import EvidencePopup from '../popups/EvidencePopup.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

// 결제 수단: account(계좌) 또는 card(카드) 중 하나, 둘 다 없으면 모름. 이름은 서버 displayName 그대로 (docs/API_RULES.md)
// payStatus: 'disconnected' 면 연결 해제된 계좌·카드 → 다시 고르게 안내
const FIXED = [
  { id: 1, name: '월세', day: 25, account: '국민 급여통장 · ****5678', cat: '주거·통신', amount: 700_000 },
  { id: 2, name: '통신비', day: 20, account: '국민 급여통장 · ****5678', cat: '주거·통신', amount: 55_000 },
  { id: 3, name: '구독료', day: 5, card: '신한 Deep Dream · ****4321', cat: '문화·여가', amount: 15_000 },
  // 직접 추가한 카드 고정지출 — 비슷한 결제가 2건이라 [고르기]로 묻는다
  { id: 4, name: 'OTT', day: 5, card: '신한 Deep Dream · ****4321', cat: '문화·여가', amount: 13_500, manual: true,
    suggestion: { count: 2, candidates: [
      { id: 't1', merchant: 'NETFLIX.COM', date: '10.05', amount: 13_500 },
      { id: 't2', merchant: 'YOUTUBE PREMIUM', date: '10.06', amount: 13_900 },
    ] } },
]
const total = FIXED.reduce((sum, f) => sum + f.amount, 0)

// 마이 · 홈에서 열었을 때 — 확정된 고정지출 (GET /api/budgets/fixed/ 의 confirmed). 시안 예시값
// due = 이번 · 지난 주기 납부 예정 중 확인이 필요한 회차 (payment_state)
//   needs_review = 출금일 +3일이 지나도 출금을 못 찾음 → "미납 확인 필요" + [고르기] (10/22 PR #26 리뷰, 결정 G)
//   candidates = 늦게 낸 것 같은 출금 (±3일 밖이라 자동 연결 안 됨). 고르면 PATCH { evidenceTransactionId } → 납부 완료
// day: null = 출금일 모름 → 홈 "출금일을 확인할 고정지출 N건" (H-05)
const CONFIRMED = [
  { id: 5, name: '관리비', day: 25, account: '국민 급여통장 · ****5678', cat: '주거·통신', amount: 150_000,
    due: { state: 'needs_review', date: '09.25', candidates: [
      { id: 'w1', merchant: '아파트관리비', date: '09.29', amount: 150_000 },
      { id: 'w2', merchant: '관리사무소', date: '10.02', amount: 148_000 },
    ] } },
  { id: 1, name: '월세', day: 25, account: '국민 급여통장 · ****5678', cat: '주거·통신', amount: 700_000 },
  { id: 2, name: '통신비', day: 20, account: '국민 급여통장 · ****5678', cat: '주거·통신', amount: 55_000 },
  { id: 3, name: '구독료', day: 5, card: '신한 Deep Dream · ****4321', cat: '문화·여가', amount: 15_000 },
  { id: 6, name: '보험료', day: null, cat: '의료·건강', amount: 42_000, manual: true },
]

// 목록 한 줄에 보일 결제 수단
function payLabel(f) {
  const name = f.account ?? f.card
  if (!name) return '출금 계좌 확인 필요'
  return f.payStatus === 'disconnected' ? `${name} (연결 해제됨 · 다시 고르기)` : name
}

// 결제 확인 한 줄: 1건이면 맞아요/아니에요, 여러 건이면 고르기
function Suggestion({ f, onPick }) {
  const { count, candidates } = f.suggestion
  if (count > 1) {
    return (
      <div className="mc-row mc-note" style={{ paddingBottom: 0 }}>
        <span>비슷한 결제가 {count}건 있어요</span>
        <button type="button" className="mc-link" onClick={onPick}>고르기</button>
      </div>
    )
  }
  const t = candidates[0]
  return (
    <div className="mc-note" style={{ paddingBottom: 0 }}>
      <p style={{ margin: 0 }}>이 결제가 {f.name} 맞나요? {t.date} {t.merchant} {won(t.amount)}</p>
      {/* 맞아요 = PATCH evidenceTransactionId: t.id / 아니에요 = 요청 없음. API 연결 후 */}
      <div className="mc-inline"><button type="button" className="mc-secondary">맞아요</button><button type="button" className="mc-secondary">아니에요</button></div>
    </div>
  )
}

// 미납 확인 필요 회차 한 줄 (결정 G) — 누르면 늦게 낸 출금 고르기
function LateDue({ f, onPick }) {
  return (
    <div className="mc-fixed-late" role="note">
      <p><b>{f.due.date} {f.name} 출금을 아직 못 찾았어요.</b><br />늦게 냈으면 그 출금을 골라 주세요. 고르면 납부 완료로 바뀌어요.</p>
      <button type="button" className="mc-secondary" onClick={onPick}>고르기</button>
    </div>
  )
}

// 마이 > 고정지출 · 홈 '꼭 나갈 돈'에서 열기
function ManageView() {
  const { openPopup } = usePopup()
  const sum = CONFIRMED.reduce((acc, f) => acc + f.amount, 0)
  const late = CONFIRMED.filter((f) => f.due?.state === 'needs_review')
  const noDay = CONFIRMED.filter((f) => !f.day)
  // 확인할 것이 있는 항목을 먼저 보여 준다
  const rows = [...late, ...noDay, ...CONFIRMED.filter((f) => !late.includes(f) && !noDay.includes(f))]

  return (
    <Layout title="고정지출" showBack>
      <div className="mc-eyebrow">고정지출</div>
      <h1>매달 나가는 돈</h1>
      <p className="mc-muted">여기 있는 돈은 예산에서 먼저 떼어 둬요. 금액을 바꾸거나 그만두면 다음 달 예산부터 반영돼요.</p>
      {(late.length > 0 || noDay.length > 0) && (
        <p className="mc-note" style={{ paddingTop: 0 }}>
          확인할 것 {late.length + noDay.length}건{late.length > 0 && ` · 미납 확인 ${late.length}건`}{noDay.length > 0 && ` · 출금일 모름 ${noDay.length}건`}
        </p>
      )}

      {rows.map((f) => (
        <div className="mc-card" key={f.id}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{f.name}</strong><br /><span className="mc-muted">{f.day ? `매월 ${f.day}일` : '출금일 모름'} · {payLabel(f)} · {f.cat}</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(f.amount)}</strong>
          </div>
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">{f.manual ? '직접 추가' : '자동으로 찾음'}</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<FixedPopup item={f} manage />)}>{f.day ? '수정' : '출금일 정하기'}</button>
          </div>
          {f.due?.state === 'needs_review' && (
            <LateDue f={f} onPick={() => openPopup(<EvidencePopup late item={{ ...f, suggestion: { count: f.due.candidates.length, candidates: f.due.candidates } }} />)} />
          )}
          {!f.day && <p className="mc-note" style={{ paddingBottom: 0 }}>출금일을 정하면 홈 '꼭 나갈 돈'에 나오고, 출금됐는지 확인해 드려요.</p>}
        </div>
      ))}
      <button type="button" className="mc-secondary" onClick={() => openPopup(<FixedPopup manage />)}>+ 고정지출 직접 추가</button>

      <div className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>매달 나가는 고정지출</span><strong>{won(sum)}</strong></div>
        <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>카드로 내는 고정지출은 카드값 안에 들어 있어서 따로 빼지 않아요.</p>
      </div>
    </Layout>
  )
}

// 첫 설정 흐름 · 고정지출 확인
function OnboardingView() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()

  return (
    <Layout title="고정지출 확인" showBack foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.savings)}>확인하고 다음</button>}>
      <StepBar step={4} />
      <div className="mc-eyebrow">고정지출 확인</div>
      <h1>매달 이 돈이 나가나요?</h1>
      <p className="mc-muted">매달 비슷한 날, 같은 금액으로 3달 연속 나간 항목을 찾았어요. 맞으면 그대로, 다르면 고치거나 '고정지출 아니에요'를 눌러 주세요.</p>
      <p className="mc-note" style={{ paddingTop: 0 }}>대출 상환은 대출 확인에서, 내 계좌로 보내는 저축은 다음 화면에서 따로 챙겨요.</p>

      {FIXED.map((f) => (
        <div className="mc-card" key={f.id}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{f.name}</strong><br /><span className="mc-muted">{f.day ? `매월 ${f.day}일` : '출금일 확인 필요'} · {payLabel(f)} · {f.cat}</span></span>
            <strong>{won(f.amount)}</strong>
          </div>
          <span className="mc-badge" style={{ marginTop: 8 }}>{f.manual ? '직접 추가' : '3달 연속 감지'}</span>
          {f.suggestion && <Suggestion f={f} onPick={() => openPopup(<EvidencePopup item={f} />)} />}
          <div className="mc-inline">
            <button type="button" className="mc-secondary" onClick={() => openPopup(<FixedPopup item={f} />)}>수정</button>
            {/* 고정지출 아니에요 = 이 후보를 무시(ignored). 같은 후보를 다시 묻지 않는다. API 연결 후. 직접 추가한 건 후보가 아니라 없음 */}
            {!f.manual && <button type="button" className="mc-secondary">고정지출 아니에요</button>}
          </div>
        </div>
      ))}
      <button type="button" className="mc-secondary" onClick={() => openPopup(<FixedPopup />)}>+ 고정지출 직접 추가</button>

      <div className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>고정지출 합계</span><strong>{won(total)}</strong></div>
      </div>
      <div className="mc-card">
        <div className="mc-row"><h2>카드 청구서</h2><button type="button" className="mc-link" onClick={() => navigate(PATHS.connect)}>카드 관리</button></div>
        <div className="mc-cardday"><span><Icon name="CreditCard" />신한카드</span><strong>420,000원 · 10.15 출금예정</strong></div>
        <p className="mc-note" style={{ paddingBottom: 0 }}>카드값은 청구서 금액 기준으로 출금예정일에 챙겨요. 카드로 내는 고정지출(구독료 등)은 이 안에 들어 있어서 따로 빼지 않아요.</p>
      </div>
    </Layout>
  )
}

export default function FixedPage() {
  const location = useLocation()
  const from = location.state?.from
  return from === 'my' || from === 'home' ? <ManageView /> : <OnboardingView />
}
