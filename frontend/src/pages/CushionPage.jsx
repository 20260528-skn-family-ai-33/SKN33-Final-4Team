// 머니 쿠션
import Layout from '../components/Layout.jsx'
import { comma, won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import CushionPopup from '../popups/CushionPopup.jsx'
import HelpTip from '../components/HelpTip.jsx'
import Icon from '../components/Icon.jsx'

export default function CushionPage() {
  const { openPopup } = usePopup()
  const { data, error, reload } = useApi('/api/budgets/cushion/')

  if (!data) return <Layout title="머니 쿠션" activeTab="fund"><ApiStatus error={error} /></Layout>
  const c = data.cushion

  return (
    <Layout title="머니 쿠션" activeTab="fund">
      <div className="mc-eyebrow">갑작스러운 지출에도 흔들리지 않게</div>
      <h1>내 돈의 안전장치</h1>

      <div className="mc-card">
        <span className="mc-muted mc-cushion-title">현재 확보한 쿠션
          {/* 11/9: 맨 아래 안내문을 [?] 창으로 */}
          {/* 11/13 (R27): 예산 승인 · 지킬 돈 저장 때 쿠션 배정액이 쌓인다 */}
          {/* 11/13: 머니쿠션이 무엇인지 먼저 소개(정의 · 쓰는 곳)하고, 쌓이는 방식은 그 아래 */}
          <HelpTip label="머니쿠션 안내" title="머니쿠션이란?" body={<CushionIntro />} />
        </span>
        <div className="mc-money">{comma(c.now)}<span style={{ fontSize: 18 }}>원</span></div>
        <div className="mc-bar"><span style={{ width: `${c.bar ?? 0}%` }}></span></div>
        {/* null(미설정)은 0원·0%로 보이지 않게 안내 문구로 바꾼다 (cushion.md 누락·경계 처리) */}
        <div className="mc-row">
          <span className="mc-muted">목표 {c.goalCustom ? '' : `${c.goalLabel} · `}{c.goal == null ? '급여를 입력하면 계산돼요' : won(c.goal)}</span>
          {c.pct != null && <strong>{c.pct}%</strong>}
        </div>
        <div className="mc-keyvalue"><span>월 쿠션 적립금</span><strong>{c.monthly == null ? '아직 정하지 않았어요' : won(c.monthly)}</strong></div>
        {/* 11/13: '재적립 계획' 카드를 따로 두지 않고 월 쿠션 적립금 아래로 (예상 기간 = 이 적립금 기준) */}
        <div className="mc-keyvalue"><span>목표까지 남은 금액</span><strong>{c.remain == null ? '급여를 입력하면 계산돼요' : won(c.remain)}</strong></div>
        <div className="mc-keyvalue"><span>예상 기간</span><strong>{c.eta}</strong></div>
        {/* 11/13: '쿠션 기준 확인'(읽기 전용) 대신 목표를 월급의 3 · 6 · 12배 중에서 바꾼다 */}
        <button type="button" className="mc-secondary" onClick={() => openPopup(<CushionPopup cushion={c} onSaved={reload} />)}>목표 바꾸기</button>
      </div>
    </Layout>
  )
}

// [?] 창 내용: 머니쿠션 정의 → 쓰는 곳 세 칸 → 쌓이는 방식
const USES = [['Stethoscope', '병원비'], ['Heart', '경조사'], ['Wrench', '수리비']]
function CushionIntro() {
  return (
    <>
      <p className="mc-cushion-def">갑자기 큰돈이 필요할 때 꺼내 쓰는 <b>비상금</b>이에요.<br />급할 때 저축을 깨지 않게 받쳐 줘요.</p>
      <div className="mc-cushion-uses">
        {USES.map(([icon, text]) => <div key={text}><span className="mc-tile"><Icon name={icon} /></span>{text}</div>)}
      </div>
      <h3 className="mc-cushion-sub">이렇게 쌓여요</h3>
      <ul className="mc-help-list">
        <li>예산을 승인하면 쿠션 적립금만큼 쌓여요.</li>
        <li>지킬 돈 설정에서 적립금을 바꾸면 같이 바뀌어요.</li>
        <li>실제 계좌의 돈이 옮겨지지는 않아요.</li>
      </ul>
    </>
  )
}
