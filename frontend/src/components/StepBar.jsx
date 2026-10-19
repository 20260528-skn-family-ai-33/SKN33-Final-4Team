// 첫 설정 흐름의 진행 막대 (7단계 중 몇 단계인지)
export default function StepBar({ step, total = 7 }) {
  return (
    <div className="mc-step" aria-label={`${total}단계 중 ${step}단계`}>
      {Array.from({ length: total }, (_, i) => <span key={i} className={i < step ? 'on' : ''}></span>)}
    </div>
  )
}
