// 머니 쿠션 (장고: budgets/templates/budgets/fund.html)
import Layout from '../components/Layout.jsx'
import { comma, won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import CushionPopup from '../popups/CushionPopup.jsx'

export default function CushionPage() {
  const { openPopup } = usePopup()
  const { data, error } = useApi('/api/budgets/cushion/')

  if (!data) return <Layout title="머니 쿠션" activeTab="fund"><ApiStatus error={error} /></Layout>
  const c = data.cushion

  return (
    <Layout title="머니 쿠션" activeTab="fund">
      <div className="mc-eyebrow">갑작스러운 지출에도 흔들리지 않게</div>
      <h1>내 돈의 안전장치</h1>

      <div className="mc-card">
        <span className="mc-muted">현재 확보한 쿠션</span>
        <div className="mc-money">{comma(c.now)}<span style={{ fontSize: 18 }}>원</span></div>
        <div className="mc-bar"><span style={{ width: `${c.bar}%` }}></span></div>
        <div className="mc-row"><span className="mc-muted">목표 {c.goalLabel} · {won(c.goal)}</span><strong>{c.pct}%</strong></div>
        <div className="mc-keyvalue"><span>보호 하한</span><strong>{won(c.floor)}</strong></div>
        <div className="mc-keyvalue"><span>예산 조정 시 사용</span><strong>{c.useRule}</strong></div>
        <button type="button" className="mc-secondary" onClick={() => openPopup(<CushionPopup cushion={c} />)}>쿠션 설정 변경</button>
      </div>

      <div className="mc-card">
        <h2>재적립 계획</h2>
        <div className="mc-keyvalue"><span>목표까지 남은 금액</span><strong>{won(c.remain)}</strong></div>
        <div className="mc-keyvalue"><span>급여마다 적립</span><strong>{won(c.perPay)}</strong></div>
        <div className="mc-keyvalue"><span>예상 기간</span><strong>{c.eta}</strong></div>
        <p className="mc-note" style={{ paddingBottom: 0 }}>남는 돈의 절반을 쿠션에 적립한다고 가정한 시안 예시예요. 승인 전에는 계획에 반영하지 않아요.</p>
      </div>
      <p className="mc-note">앱에서 계획한 배정액이에요. 은행 계좌의 돈이 이동하지는 않아요.</p>
    </Layout>
  )
}
