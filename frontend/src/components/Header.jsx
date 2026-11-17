// 상단 바: 뒤로 가기 + 화면 제목 + (탭 화면이면) 마이 메뉴 아이콘(가로선 세 개, 11/3)
import { useLocation, useNavigate } from 'react-router'
import { ChevronLeft, Menu } from 'lucide-react'
import { PATHS } from '../routes.js'

// action = 메뉴 아이콘 왼쪽 버튼 (홈의 알림 종)
export default function Header({ title, showBack = false, backTo, showMy = true, action = null, inert = false }) {
  const navigate = useNavigate()  // 코드로 화면 이동할 때 쓰는 함수
  const first = useLocation().key === 'default'  // 앱을 이 주소로 바로 열어 앞 화면 기록이 없다

  return (
    <header id="mc-header" inert={inert}>
      <div className="mc-head-left">
        {showBack && (
          // 앞 화면 기록이 있으면 그 화면으로(navigate(-1)). 주소로 바로 열어 기록이 없으면 backTo 로 — 앱 밖으로 나가지 않게
          <button type="button" className="mc-icon" onClick={() => (backTo && first ? navigate(backTo, { replace: true }) : navigate(-1))} aria-label="뒤로">
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
