// 팝업: 돈관리 메뉴 (장고 budgets/_menu.html) — 하단 탭 가운데 "돈관리 ∧"
import { useNavigate } from 'react-router'
import { Repeat, SlidersHorizontal } from 'lucide-react'
import Popup from './Popup.jsx'
import FixedPopup from './FixedPopup.jsx'
import { usePopup } from './context.js'
import { PATHS } from '../routes.js'

export default function MenuPopup() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()

  return (
    <Popup label="돈관리">
      <h2>돈관리</h2>
      <div className="mc-menu-grid">
        <button type="button" onClick={() => navigate(PATHS.assistant)}>
          <SlidersHorizontal size={20} aria-hidden="true" />예산 조정
        </button>
        {/* 팝업 안에서 다른 팝업 열기 = 갈아끼우기 */}
        <button type="button" onClick={() => openPopup(<FixedPopup />)}>
          <Repeat size={20} aria-hidden="true" />고정지출 추가
        </button>
      </div>
      <button type="button" className="mc-primary" onClick={() => navigate(PATHS.assistant)}>챗봇에게 내 소비 물어보기</button>
    </Popup>
  )
}
