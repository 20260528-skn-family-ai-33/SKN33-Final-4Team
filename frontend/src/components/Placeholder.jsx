// 아직 옮기지 않은 화면에 잠깐 보여주는 카드
export default function Placeholder({ name }) {
  return (
    <div className="mc-card">
      <h2>{name}</h2>
      <p className="mc-muted" style={{ margin: 0 }}>이 화면은 다음 단계에서 옮길 거예요.</p>
    </div>
  )
}
