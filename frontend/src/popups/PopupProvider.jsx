// 지금 열려 있는 팝업을 기억하는 곳 (장고에서는 #mc-layer 안의 HTML 이 이 역할)
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router'
import { PopupContext } from './context.js'

export default function PopupProvider({ children }) {
  const location = useLocation()
  // 스위치: null 이면 꺼짐, 팝업이 들어 있으면 켜짐. at = 팝업을 연 화면
  const [state, setState] = useState(null)

  // 다른 화면으로 넘어가면 팝업은 저절로 사라진다 (연 화면에서만 보여 줌)
  const popup = state && state.at === location.key ? state.el : null

  const openPopup = (el) => setState({ el, at: location.key })
  const closePopup = () => setState(null)

  // Esc 키를 누르면 닫기 (팝업이 열려 있을 때만 귀 기울이기)
  useEffect(() => {
    if (!popup) return
    const onKey = (e) => e.key === 'Escape' && setState(null)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)  // 닫히면 그만 듣기
  }, [popup])

  return (
    <PopupContext.Provider value={{ popup, openPopup, closePopup }}>
      {children}
    </PopupContext.Provider>
  )
}
