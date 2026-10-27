// 첫 설정 흐름의 진행 막대 (6단계 중 몇 단계인지) — 예산 산정 방향 화면을 없애 7 → 6 (2026-10-27)
export default function StepBar({ step, total = 6 }) {
  return (
    <div className="mc-step" aria-label={`${total}단계 중 ${step}단계`}>
      {Array.from({ length: total }, (_, i) => <span key={i} className={i < step ? 'on' : ''}></span>)}
    </div>
  )
}
