// 고정지출 확인
// 목록은 시안 예시값 — 고정지출 감지 API 가 생기면 바꾼다
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from '../popups/context.js'
import FixedPopup from '../popups/FixedPopup.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

// 결제 수단: account(계좌) 또는 card(카드) 중 하나, 둘 다 없으면 모름. 이름은 서버 displayName 그대로 (docs/API_RULES.md)
// payStatus: 'disconnected' 면 연결 해제된 계좌·카드 → 다시 고르게 안내
const FIXED = [
  { id: 1, name: '월세', day: 25, account: '국민 급여통장 · ****5678', cat: '주거·통신', amount: 700_000 },
  { id: 2, name: '통신비', day: 20, account: '국민 급여통장 · ****5678', cat: '주거·통신', amount: 55_000 },
  { id: 3, name: '구독료', day: 5, card: '신한 Deep Dream · ****4321', cat: '문화·여가', amount: 15_000 },
]
const total = FIXED.reduce((sum, f) => sum + f.amount, 0)

// 목록 한 줄에 보일 결제 수단
function payLabel(f) {
  const name = f.account ?? f.card
  if (!name) return '출금 계좌 확인 필요'
  return f.payStatus === 'disconnected' ? `${name} (연결 해제됨 · 다시 고르기)` : name
}

export default function FixedPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()

  return (
    <Layout title="고정지출 확인" showBack foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.savings)}>확인하고 다음</button>}>
      <StepBar step={4} />
      <div className="mc-eyebrow">고정지출 확인</div>
      <h1>매달 이 돈이 나가나요?</h1>
      <p className="mc-muted">최근 2~3개월 거래에서 비슷한 결제가 3번 이상 나온 항목을 찾았어요. 다르면 고치거나 지워 주세요.</p>
      <p className="mc-note" style={{ paddingTop: 0 }}>대출 상환은 대출 확인에서, 내 계좌로 보내는 저축은 다음 화면에서 따로 챙겨요.</p>

      {FIXED.map((f) => (
        <div className="mc-card" key={f.id}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{f.name}</strong><br /><span className="mc-muted">{f.day ? `매월 ${f.day}일` : '출금일 확인 필요'} · {payLabel(f)} · {f.cat}</span></span>
            <strong>{won(f.amount)}</strong>
          </div>
          <span className="mc-badge" style={{ marginTop: 8 }}>최근 3개월 3회 감지</span>
          <div className="mc-inline">
            <button type="button" className="mc-secondary" onClick={() => openPopup(<FixedPopup item={f} />)}>수정</button>
            {/* 삭제는 API 연결 후 */}
            <button type="button" className="mc-secondary">삭제</button>
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
        <p className="mc-note" style={{ paddingBottom: 0 }}>카드값은 청구서 금액 기준으로 출금예정일에 챙겨요. 승인 사용액과 청구액은 달라서 고정지출 합계에는 넣지 않아요.</p>
      </div>
    </Layout>
  )
}
