// 팝업: 쿠션 기준 확인 — 쿠션 화면 "쿠션 기준 확인"
// 읽기 전용. 월 적립금 저장(PATCH /api/budgets/cushion/)은 예산 설정 작업과 조율한 뒤 연결한다 (R26 후속)
import Popup from './Popup.jsx'
import { won } from '../utils/money.js'
import { usePopup } from './context.js'

// props: cushion = 쿠션 화면이 장고에게 받아 온 값. null은 0원으로 보이지 않게 안내 문구로 바꾼다
export default function CushionPopup({ cushion }) {
  const { closePopup } = usePopup()

  return (
    <Popup label="쿠션 기준 확인" sub>
      <div className="mc-eyebrow">쿠션 기준 확인</div>
      <h1>내 예비비의 기준</h1>
      <div className="mc-keyvalue"><span>쿠션 목표액</span><strong>{cushion.goal == null ? '급여 정보 확인이 필요해요' : won(cushion.goal)}</strong></div>
      <p className="mc-note">목표액은 월 실수령액의 3배로 정해져요.</p>
      <div className="mc-keyvalue"><span>월 쿠션 적립금</span><strong>{cushion.monthly == null ? '아직 정하지 않았어요' : won(cushion.monthly)}</strong></div>
      <p className="mc-note">월 쿠션 적립금은 이 화면에서 바꿀 수 없어요.</p>
      <div className="mc-keyvalue"><span>예산 조정 시 쿠션 사용</span><strong>{cushion.useRule ?? '아직 설정을 지원하지 않아요'}</strong></div>
      <div className="mc-foot"><button type="button" className="mc-primary" onClick={closePopup}>확인</button></div>
    </Popup>
  )
}
