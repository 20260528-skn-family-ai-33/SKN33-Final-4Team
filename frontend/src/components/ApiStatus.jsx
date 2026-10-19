// 데이터를 기다리는 동안 / 실패했을 때 보여 주는 카드
export default function ApiStatus({ error }) {
  if (!error) {
    return <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>불러오는 중…</p></div>
  }
  return (
    <div className="mc-card">
      <h2>데이터를 못 불러왔어요</h2>
      <p className="mc-muted">장고 서버(python manage.py runserver)가 켜져 있는지 확인해 주세요.</p>
      <button type="button" className="mc-secondary" onClick={() => window.location.reload()}>다시 시도</button>
    </div>
  )
}
