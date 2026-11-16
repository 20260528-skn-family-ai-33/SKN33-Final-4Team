// 가운데 창: 월급 확인을 취소 · 정정해서 '이번 주기만 받은 금액으로' 기준이 풀렸을 때 다시 짤지 묻기 (11/13)
// 서버는 이미 등록 실수령액 기준으로 돌아갔다(POST salary/decisions 응답 basisReset). 승인한 예산은 그대로라서
// [다시 짜기] = 지킬 돈 화면을 등록 월급 기준 추천값으로 연다 → 저장 → 새 추천 → 승인 · [그대로 두기] = 지금 예산 유지
// props: onDone = 그대로 둘 때 홈 다시 불러오기
import { useNavigate } from 'react-router'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { toast } from '../utils/toast.js'
import { PATHS } from '../routes.js'

export default function SalaryBasisResetPopup({ onDone }) {
  const navigate = useNavigate()
  const { closePopup } = usePopup()
  const choices = [
    ['redo', '다시 짜기', '등록한 월급으로 지킬 돈과 예산을 다시 정해요'],
    ['keep', '그대로 두기', '지금 예산을 이번 주기 끝까지 써요'],
  ]
  const pick = (k) => {
    closePopup()
    if (k === 'redo') navigate(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })
    else { toast('이번 예산은 그대로 둘게요'); onDone?.() }
  }
  return (
    <Popup label="예산 다시 짜기 확인" dialog>
      <h2>월급 확인을 취소했어요</h2>
      <p className="mc-muted" style={{ margin: '6px 0 0' }}>이번 예산은 그때 받은 월급으로 짜여 있어요.<br />등록한 월급으로 다시 짤까요?</p>
      <div className="mc-apply-choices">
        {choices.map(([k, label, sub]) => (
          <button key={k} type="button" className="mc-choice" onClick={() => pick(k)}>
            <strong>{label}</strong><span className="mc-muted">{sub}</span>
          </button>
        ))}
      </div>
    </Popup>
  )
}
