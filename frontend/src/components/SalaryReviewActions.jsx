import { useNavigate } from 'react-router'
import { PATHS } from '../routes.js'

// 월급 변경 뒤 예산 조정 대화로 돌아가는 버튼 (11/16 UI 정리: 누르면 무엇이 되는지 버튼 글자 · 한 줄 설명으로 드러낸다)
// stage (월급 증가분 배분 화면만): pending = 아직 미리보기 전, preview = 계산한 배분안이 있음, saved = 확정함. 그 밖의 화면은 비움
const RETURN = {
  pending: ['저축·쿠션은 그대로 두고 요청만 이어가기',
    '남은 월급 증가분은 여윳돈으로 두고, 대화에서 원래 요청한 생활비 조정만 이어가요. 저축·월 머니쿠션까지 나누려면 위에서 비율을 정해 미리보기를 먼저 만들어 주세요.'],
  preview: ['이 배분안으로 대화 이어가기', '계산한 배분안을 예산 조정 대화에서 확인하고 확정할 수 있어요.'],
  saved: ['예산 조정 대화로 돌아가기', null],
}

export default function SalaryReviewActions({ context, busy = false, note = false, stage }) {
  const navigate = useNavigate()
  if (!context.salaryReview && !context.budgetReturn) return null
  const [label, help] = RETURN[stage] ?? ['이전 예산 조정 이어하기', null]
  return <div className="mc-form mc-sr-actions">
    {note && context.salaryReview && <p className="mc-note" role="status">월급은 저장됐어요. 예산 검토는 나중에 다시 할 수 있어요.</p>}
    {context.budgetReturn
      ? <>
        <button type="button" className="mc-secondary" disabled={busy} aria-describedby={help ? 'mc-sr-return-help' : undefined}
          onClick={() => navigate(context.budgetReturn.returnTo)}>{label}</button>
        {help && <p id="mc-sr-return-help" className="mc-note">{help}</p>}
      </>
      : <button type="button" className="mc-link" disabled={busy} onClick={() => navigate(PATHS.my)}>예산 검토 나중에 하기</button>}
  </div>
}
