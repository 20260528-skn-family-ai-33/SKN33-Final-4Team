// 상단 바: 뒤로 가기 + 화면 제목 + (탭 화면이면) 마이 메뉴 아이콘(가로선 세 개, 11/3)
import { useNavigate } from 'react-router'
import { ChevronLeft, Menu } from 'lucide-react'
import { PATHS } from '../routes.js'

// action = 메뉴 아이콘 왼쪽 버튼 (홈의 알림 종)
export default function Header({ title, showBack = false, showMy = true, action = null, inert = false }) {
  const navigate = useNavigate()  // 코드로 화면 이동할 때 쓰는 함수

  return (
    <header id="mc-header" inert={inert}>
      <div className="mc-head-left">
        {showBack && (
          <button type="button" className="mc-icon" onClick={() => navigate(-1)} aria-label="뒤로">
            <ChevronLeft size={20} aria-hidden="true" />
          </button>
        )}
        <strong>{title}</strong>
      </div>
      {showMy && (
        <div className="mc-head-r">
          {action}
          <button type="button" className="mc-icon" onClick={() => navigate(PATHS.my)} aria-label="마이">
            <Menu size={20} aria-hidden="true" />
          </button>
        </div>
      )}
    </header>
  )
}
