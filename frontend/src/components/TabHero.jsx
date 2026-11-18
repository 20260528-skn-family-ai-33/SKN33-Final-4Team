// 탭 화면 맨 위 색 띠 (11/18 시안 B): 탭마다 띠 색이 달라서 화면만 봐도 어느 탭인지 안다 (색은 styles/tabs.css 의 data-tab)
// 문장 제목 + 오른쪽 쿠숑이. 바로 뒤에 오는 .mc-tabsheet(흰 시트)가 띠 위로 겹쳐 올라온다
// props: eyebrow = 제목 위 작은 줄, title = 큰 문장, mood = 쿠숑이 포즈(없으면 안 보임), children = 제목 아래 칩 · 버튼
import Mascot from './Mascot.jsx'

export default function TabHero({ eyebrow, title, mood, children }) {
  return (
    <section className={`mc-tabhero${mood ? ' has-mascot' : ''}`}>
      {eyebrow && <div className="mc-tabhero-eyebrow">{eyebrow}</div>}
      <h1 className="mc-tabhero-title">{title}</h1>
      {children}
      {mood && <Mascot mood={mood} size={116} />}
    </section>
  )
}
