// 머니 쿠션
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
        <div className="mc-keyvalue"><span>월 쿠션 적립금</span><strong>{won(c.monthly)}</strong></div>
        <div className="mc-keyvalue"><span>예산 조정 시 사용</span><strong>{c.useRule}</strong></div>
        <button type="button" className="mc-secondary" onClick={() => openPopup(<CushionPopup cushion={c} />)}>쿠션 설정 변경</button>
      </div>

      <div className="mc-card">
        <h2>재적립 계획</h2>
        <div className="mc-keyvalue"><span>목표까지 남은 금액</span><strong>{won(c.remain)}</strong></div>
        <div className="mc-keyvalue"><span>예상 기간</span><strong>{c.eta}</strong></div>
        <p className="mc-note" style={{ paddingBottom: 0 }}>지킬 돈 설정에서 정한 월 쿠션 적립금 기준이에요. 쿠션 설정 변경에서 바꿀 수 있어요.</p>
      </div>
      <p className="mc-note">앱에서 계획한 배정액이에요. 은행 계좌의 돈이 이동하지는 않아요.</p>
    </Layout>
  )
}
