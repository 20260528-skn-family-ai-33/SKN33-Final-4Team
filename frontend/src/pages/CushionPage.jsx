// 머니 쿠션 (11/18 시안 B): 남색 띠에 쌓인 쿠션을 문장으로 + 쿠숑이 → 흰 시트에 쿠션 하나의 진행 → 저축 계좌 잔액
// 머니쿠션은 회원마다 하나다. 저축 계좌는 쿠션 계산에 쓰지 않는 참고 정보 (GET /api/budgets/cushion/ 의 savings, docs/api/cushion.md)
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import { comma, won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import CushionPopup from '../popups/CushionPopup.jsx'
import HelpTip from '../components/HelpTip.jsx'
import Icon from '../components/Icon.jsx'
import TabHero from '../components/TabHero.jsx'
import CushionIntro from '../components/CushionIntro.jsx'
import { PATHS } from '../routes.js'


export default function CushionPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const { data, error, reload } = useApi('/api/budgets/cushion/')

  if (!data) return <Layout title="쿠션" activeTab="fund"><ApiStatus error={error} /></Layout>
  const c = data.cushion
  const savings = data.savings
  const notices = data.notices ?? {}  // 11/18: 쿠션 넣는 날 · 통장 잔액이 쿠션보다 적음 (홈 종 알림과 같은 기준)

  return (
    <Layout title="쿠션" activeTab="fund">
      <TabHero
        title={<>쿠션이<br /><em>{comma(c.now)}원</em><br />쌓였어요</>} mood="cushion" />

      <section className="mc-tabsheet mc-cushion" aria-label="내 머니쿠션">
        <div className="mc-row">
          <h2 className="mc-cushion-title">내 머니쿠션
            {/* 머니쿠션이 무엇인지 먼저 소개(정의 · 쓰는 곳)하고, 쌓이는 방식은 그 아래 */}
            <HelpTip label="머니쿠션 안내" title="머니쿠션이란?" body={<CushionIntro />} />
          </h2>
          {/* null(미설정)은 0원·0%로 보이지 않게 안내 문구로 바꾼다 (cushion.md 누락·경계 처리) */}
          <span className="mc-muted">목표 <b>{c.goal == null ? '급여 입력 후 계산' : won(c.goal)}</b></span>
        </div>
        <div className="mc-pillow" role="progressbar" aria-label="머니쿠션 목표 달성률" aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={c.bar ?? 0} aria-valuetext={c.pct == null ? '목표 미정' : `${c.pct}%`}>
          <span style={{ width: `${c.bar ?? 0}%` }}></span>
          <em>{c.pct == null ? '목표를 정하면 채워져요' : `${c.pct}% 채웠어요`}</em>
        </div>
        <div className="mc-tabkv">
          <div><small>매달 쌓는 돈</small><b>{c.monthly == null ? '아직 안 정함' : won(c.monthly)}</b></div>
          <div><small>다 쌓을 때까지</small><b>{c.etaMonths == null ? '—' : c.etaMonths === 0 ? '목표 달성' : `약 ${c.etaMonths}개월`}</b></div>
        </div>
        {c.etaMonths == null && <p className="mc-muted">{c.eta}</p>}
        {/* 11/18 시안 K1: 쿠션 알림은 진행 막대 바로 아래. 해당할 때만 보인다 */}
        {notices.depositDue && (
          <div className="mc-cushion-note is-deposit" role="status">
            <span className="mc-tile"><Icon name="PiggyBank" /></span>
            <div><b>쿠션 모으기 잊지 마세요!</b>오늘은 쿠션 넣는 날이에요{c.monthly ? ` · 이번 달 ${won(c.monthly)}` : ''}</div>
          </div>
        )}
        {notices.balanceLow && (
          <div className="mc-cushion-note is-low" role="status">
            <span className="mc-tile"><Icon name="CircleAlert" /></span>
            <div><b>통장 잔액이 쿠션보다 적어요</b>쿠션을 따로 잘 모으고 있는지 확인해 주세요.</div>
          </div>
        )}
        <button type="button" className="mc-secondary" onClick={() => openPopup(<CushionPopup cushion={c} onSaved={reload} />)}>목표 바꾸기</button>
      </section>

      {/* 저축 계좌 잔액 (11/18): 은행 연결 때 함께 저장된 예적금 잔액. 최신 값은 홈 [새로고침]으로 다시 불러온다 */}
      <div className="mc-tabsec">
        <h2>저축 계좌</h2>
        {savings?.total != null && <b>{won(savings.total)}</b>}
      </div>
      {!Array.isArray(savings?.items) ? <ApiStatus error={new Error('저축 계좌 목록 누락')} onRetry={reload} /> : savings.items.length === 0 ? (
        <div className="mc-card mc-savings-empty">
          <span className="mc-tile"><Icon name="PiggyBank" /></span>
          <div className="mc-grow">
            <strong>연결된 저축 계좌가 없어요</strong>
            <p className="mc-muted">은행을 연결하면 적금 · 예금 잔액도 여기서 볼 수 있어요.</p>
          </div>
          <button type="button" className="mc-link" onClick={() => navigate(PATHS.connections)}>연결 관리 ›</button>
        </div>
      ) : (
        <ul className="mc-card mc-savings">
          {savings.items.map((a) => (
            <li key={a.accountId}>
              <span className="mc-tile"><Icon name="PiggyBank" /></span>
              <span className="mc-grow">
                <strong>{a.displayName}</strong>
                {a.status === 'relogin' && <small>다시 연결 필요</small>}
              </span>
              <b>{a.balance == null ? '잔액 확인 안 됨' : won(a.balance)}</b>
            </li>
          ))}
          {/* 11/18: 조회 시각 줄은 뺐다. 못 불러온 계좌가 있을 때만 한 줄 */}
          {savings.unknownCount > 0 && <li className="mc-savings-asof">{savings.unknownCount}개는 잔액을 못 불러왔어요</li>}
        </ul>
      )}
    </Layout>
  )
}
