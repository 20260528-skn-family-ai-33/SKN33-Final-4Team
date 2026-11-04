// 팝업: 알림 — 홈 오른쪽 위 종 아이콘 (11/3: 메인 카드 위에 늘어놓던 할 일 · 월급 안내를 여기로 모음)
// props: items = [{ key, text, count?, icon?, go? }] — go 가 있으면 누르는 줄(누르면 닫고 그 화면으로), 없으면 같은 모양의 안내 줄
// 목록 모양은 홈 '꼭 나갈 돈'과 같다: 왼쪽 네모(건수 또는 아이콘) · 문장 · ›, 줄 사이 구분선
import Popup from './Popup.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from './context.js'

export default function NoticePopup({ items }) {
  const { closePopup } = usePopup()
  const open = (go) => { closePopup(); go() }

  return (
    <Popup label="알림">
      <h2>알림</h2>
      {items.length === 0 && <p className="mc-muted mc-notice-list">새 알림이 없어요.</p>}
      {items.length > 0 && (
        <div className="mc-notice-list">
          {items.map((n) => {
            const Row = n.go ? 'button' : 'div'  // 안내(월급 상태)는 갈 화면이 없어서 누를 수 없고 화살표가 없다
            return (
              <Row {...(n.go ? { type: 'button', onClick: () => open(n.go) } : {})} className="mc-listrow" key={n.key}>
                <span className="mc-tile">{n.count != null ? <b>{n.count}</b> : <Icon name={n.icon ?? 'Bell'} />}</span>
                <span className="mc-grow">{n.text}</span>
                {n.go && <span className="mc-muted" aria-hidden="true">›</span>}
              </Row>
            )
          })}
        </div>
      )}
    </Popup>
  )
}
