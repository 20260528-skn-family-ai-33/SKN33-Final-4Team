// 팝업: 쿠션 설정 — 쿠션 화면 "쿠션 설정 변경"
import Popup from './Popup.jsx'
import { usePopup } from './context.js'

// props: cushion = 쿠션 화면이 장고에게 받아 온 값
export default function CushionPopup({ cushion }) {
  const { closePopup } = usePopup()
  const onSubmit = (e) => {
    e.preventDefault()
    closePopup()  // 저장은 DB 연결 후
  }

  return (
    <Popup label="쿠션 설정" sub>
      <div className="mc-eyebrow">쿠션 설정</div>
      <h1>내 예비비의 기준</h1>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit}>
        {/* defaultValue = 처음 채워 둘 값. 그 뒤로는 사용자가 자유롭게 고칠 수 있다 */}
        <label className="mc-field">쿠션 목표액<input inputMode="numeric" min="0" name="cushionTarget" step="1" type="number" defaultValue={cushion.goal} /></label>
        {/* 2026-10-21: '보호 하한' 대신 매달 적립할 금액을 정한다 (지킬 돈 설정의 월 쿠션 적립금과 같은 값) */}
        <label className="mc-field">월 쿠션 적립금<input inputMode="numeric" min="0" name="cushionMonthly" step="10000" type="number" defaultValue={cushion.monthly} /></label>
        <label className="mc-field">예산 조정 시 쿠션 사용<select><option>승인 후 사용 허용</option><option>사용하지 않음</option></select></label>
        <div className="mc-error" role="alert"></div>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit">설정 저장</button></div>
      </form>
    </Popup>
  )
}
