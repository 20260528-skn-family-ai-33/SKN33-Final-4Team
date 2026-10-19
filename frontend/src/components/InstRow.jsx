// 기관·계좌 한 줄 (아이콘 + 이름 + 작은 글씨 + 오른쪽 끝)
import Icon from './Icon.jsx'

export default function InstRow({ icon = 'Landmark', name, sub, subClass, right }) {
  return (
    <div className="mc-inst">
      <span className="mc-tile"><Icon name={icon} /></span>
      <span className="mc-grow"><strong>{name}</strong><small className={subClass}>{sub}</small></span>
      {right}
    </div>
  )
}
