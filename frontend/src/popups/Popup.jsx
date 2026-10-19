// 팝업 틀: 어두운 배경 + 아래에서 올라오는 시트 + 닫기 ×
// props: label = 화면 읽기 프로그램용 이름, sub = 큰 시트(mc-subsheet) 여부, children = 팝업 내용
import { useEffect, useRef } from 'react'
import { usePopup } from './context.js'

export default function Popup({ label = '상세 확인', sub = false, children }) {
  const { closePopup } = usePopup()
  const sheet = useRef(null)

  // 열리자마자 첫 버튼에 포커스 (키보드·화면 읽기 프로그램 사용자용)
  useEffect(() => {
    sheet.current?.querySelector('button, a, input')?.focus()
  }, [])

  return (
    <>
      <button type="button" className="mc-backdrop" aria-label="닫기" onClick={closePopup}></button>
      <section ref={sheet} className={sub ? 'mc-sheet mc-subsheet' : 'mc-sheet'} role="dialog" aria-modal="true" aria-label={label}>
        <div className="mc-handle"></div>
        <div className="mc-row"><span></span><button type="button" className="mc-link" onClick={closePopup}>닫기 ×</button></div>
        {children}
      </section>
    </>
  )
}
