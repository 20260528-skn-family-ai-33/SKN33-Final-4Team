// 팝업 "방송실" — 어느 화면에서든 usePopup() 으로 팝업을 열고 닫을 수 있게 해 주는 통로
import { createContext, useContext } from 'react'

export const PopupContext = createContext(null)

// 사용법: const { openPopup, closePopup } = usePopup()
//         openPopup(<CalcPopup />)  → 팝업 열기
//         closePopup()             → 팝업 닫기
export const usePopup = () => useContext(PopupContext)
