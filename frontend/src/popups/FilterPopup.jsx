// 팝업: 거래 유형 (장고 finance/_filter.html) — 거래 화면 맨 아래 "필터 · 거래 유형"
import Popup from './Popup.jsx'
import HelpPopup from './HelpPopup.jsx'
import { usePopup } from './context.js'

export default function FilterPopup() {
  const { openPopup, closePopup } = usePopup()

  return (
    <Popup>
      <h2>거래 유형</h2>
      <button type="button" className="mc-primary" onClick={closePopup}>소비만 보기</button>
      <button type="button" className="mc-secondary" onClick={() => openPopup(<HelpPopup />)}>이체·저축 제외 기준 보기</button>
    </Popup>
  )
}
