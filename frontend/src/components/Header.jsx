// 상단 바: 뒤로 가기 + 화면 제목 + (탭 화면이면) 마이 아이콘
import { useNavigate } from 'react-router'
import { ChevronLeft, UserRound } from 'lucide-react'
import { PATHS } from '../routes.js'

export default function Header({ title, showBack = false, showMy = true, inert = false }) {
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
          <button type="button" className="mc-icon" onClick={() => navigate(PATHS.my)} aria-label="마이">
            <UserRound size={20} aria-hidden="true" />
          </button>
        </div>
      )}
    </header>
  )
}
