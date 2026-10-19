// 팝업: 매달 같은 금액으로 고정할까요? — 예산 화면의 자물쇠
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'

// props: categories = 예산 화면이 장고에게 받아 온 카테고리 목록
export default function CatLockPopup({ categories }) {
  const { closePopup } = usePopup()
  // 켜진 카테고리 이름 목록. 누를 때마다 넣었다 뺐다 (화면에서만 바뀌고 저장은 안 됨)
  const [picked, setPicked] = useState([])
  const toggle = (name) =>
    setPicked((now) => (now.includes(name) ? now.filter((n) => n !== name) : [...now, name]))

  return (
    <Popup>
      <h2>매달 같은 금액으로 고정할까요?</h2>
      <p className="mc-muted" style={{ margin: '4px 0 12px' }}>고른 카테고리는 다음 예산부터 이 금액 그대로 들어가요. 챗봇과 조정할 때도 바뀌지 않아요.</p>
      <div className="mc-lock-list">
        {categories.map((c) => (
          <button type="button" className="mc-lock-opt" key={c.name} aria-pressed={picked.includes(c.name)} onClick={() => toggle(c.name)}>
            <span className="mc-lock-box"></span><span className="mc-grow">{c.name}</span><strong>{won(c.budget)}</strong>
          </button>
        ))}
      </div>
      <p className="mc-note" style={{ padding: '10px 0 4px' }}>교통비·통신비처럼 매달 비슷하게 나가는 항목에 좋아요.</p>
      <button type="button" className="mc-primary" onClick={closePopup}>고정 안 함</button>
    </Popup>
  )
}
