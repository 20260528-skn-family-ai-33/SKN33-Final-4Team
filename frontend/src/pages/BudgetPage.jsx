// 카테고리 예산
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import LockIcon from '../components/LockIcon.jsx'
import { won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import IrregularPopup from '../popups/IrregularPopup.jsx'
import CatLockPopup from '../popups/CatLockPopup.jsx'

export default function BudgetPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const { data, error } = useApi('/api/budgets/')

  if (!data) return <Layout title="카테고리 예산" activeTab="budget"><ApiStatus error={error} /></Layout>
  const { period: PERIOD, monthly: MONTHLY, monthSplit: MONTH_SPLIT, categories: CATEGORIES, unclassified: UNCLASSIFIED } = data

  return (
    <Layout title="카테고리 예산" activeTab="budget">
      <div className="mc-eyebrow">{PERIOD.planName} · {PERIOD.label} · 예산 {PERIOD.version}</div>
      <h1>카테고리 예산</h1>

      <div className="mc-card">
        <div className="mc-row"><h2>한 달 나눠보기</h2><span className="mc-muted">월 {won(MONTHLY)}</span></div>
        {MONTH_SPLIT.map((s) =>
          s.popup ? (
            <button type="button" className="mc-keyvalue mc-kv-btn" key={s.name} onClick={() => openPopup(<IrregularPopup />)}>
              <span>{s.name} ›</span><strong>{won(s.amount)}</strong>
            </button>
          ) : (
            <div className="mc-keyvalue" key={s.name}><span>{s.name}</span><strong>{won(s.amount)}</strong></div>
          )
        )}
      </div>

      <div className="mc-card">
        <div className="mc-row">
          <h2>생활비 카테고리</h2>
          <span className="mc-muted">{won(data.livingUsed)} / {won(data.livingBudget)}</span>
        </div>
        {CATEGORIES.map((c) => (
          <div className="mc-catuse" key={c.name}>
            <div className="mc-row">
              <span className="mc-catname"><span className="mc-cat-ic"><Icon name={c.icon} /></span>{c.name}</span>
              <button type="button" className="mc-lockt" aria-pressed={c.locked} aria-label={`${c.name} 자물쇠`} onClick={() => openPopup(<CatLockPopup categories={CATEGORIES} />)}>
                <LockIcon />
              </button>
            </div>
            <div className="mc-bar"><span style={{ width: `${c.pct}%` }}></span></div>
            <div className="mc-row mc-catuse-n">
              <span className="mc-muted">{won(c.used)} / {won(c.budget)} · {c.pct}%</span>
              <strong>남은 {won(c.left)}</strong>
            </div>
          </div>
        ))}
        {UNCLASSIFIED.count > 0 && (
          <button type="button" className="mc-catuse mc-catuse-pend" onClick={() => navigate(`${PATHS.txs}?category=확인 필요`)}>
            <span>확인 필요 {UNCLASSIFIED.count}건</span><strong>{won(UNCLASSIFIED.amount)} 고르러 가기 ›</strong>
          </button>
        )}
      </div>

      <button type="button" className="mc-primary" onClick={() => navigate(PATHS.assistant)}>
        <Icon name="MessageCircle" /> 챗봇으로 예산 조정
      </button>
      <p className="mc-note">자물쇠를 건 카테고리는 조정할 때 그대로 둬요. 예산 변경은 챗봇이 만든 조정안을 확인하고 승인해야 반영돼요.</p>
    </Layout>
  )
}
