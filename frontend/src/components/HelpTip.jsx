// 제목 옆 [?] — 누르면 화면 가운데 작은 창으로 짧은 설명 (11/9: 화면 맨 아래 안내문을 옮김)
// props: label = 버튼 · 창 이름(화면 읽기용), title = 창 제목, children = <li> 줄들
import Icon from './Icon.jsx'
import Popup from '../popups/Popup.jsx'
import { usePopup } from '../popups/context.js'

export default function HelpTip({ label, title, children }) {
  const { openPopup } = usePopup()
  return (
    <button type="button" className="mc-help-btn" aria-label={`${label} 보기`} onClick={() => openPopup(
      <Popup label={label} dialog>
        <h2>{title}</h2>
        <ul className="mc-help-list">{children}</ul>
      </Popup>,
    )}><Icon name="CircleQuestionMark" size={15} /></button>
  )
}
