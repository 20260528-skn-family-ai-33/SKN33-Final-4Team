// 예산 산정 방향
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { PATHS } from '../routes.js'

const DIRS = [
  ['생활 안정형', '생활은 안정되게, 저축은 최소한으로만'],
  ['저축 집중형', '생활을 빠듯하게, 저축에 집중'],
]

export default function DirectionPage() {
  const navigate = useNavigate()
  const [dir, setDir] = useState(null)

  return (
    <Layout title="예산 산정 방향" showBack foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.protect)}>다음</button>}>
      <StepBar step={5} />
      <div className="mc-eyebrow">예산 산정 방향</div>
      <h1>어떤 방향으로 나눌까요?</h1>
      <p className="mc-muted">고른 방향에 맞춰 지킬 돈과 카테고리 예산을 추천해요.</p>
      {DIRS.map(([name, desc]) => (
        <button key={name} type="button" className="mc-choice" aria-pressed={dir === name} onClick={() => setDir(name)}>
          <strong>{name}</strong><span className="mc-muted">{desc}</span>
        </button>
      ))}
      <p className="mc-note">언제든 마이에서 바꿀 수 있어요.</p>
    </Layout>
  )
}
