// 팝업: 알림 — 홈 오른쪽 위 종 아이콘 (11/3: 메인 카드 위에 늘어놓던 할 일 · 월급 안내를 여기로 모음)
// props: items = [{ key, title?, text, count?, mark?, icon?, go?, group?, act?, tone? }]
//   title = 문장 위 굵은 이름 (예: '월급 확인', 11/13) · mark = 아이콘 대신 굵은 글자 하나 (예: '!', 11/13)
//   go 가 있으면 누르는 줄(누르면 닫고 그 화면으로), 없으면 같은 모양의 안내 줄
// 11/18 시안 N3: 묶음으로 나눈다 — group 'act' = 지금 해 볼 일(문장 아래 act 글자, 예: '쿠숑이와 조정하기 ›') · 'check' = 확인해 주세요 · 없으면 안내
//   tone = 네모 색: amber(큰 지출 · 월급 차이) · navy(쿠션 적립) · lime(명절 · 연말) · red(쿠션 잔액 의심). 없으면 기본 연파랑
// 목록 모양은 홈 '꼭 나갈 돈'과 같다: 왼쪽 네모(건수 또는 아이콘) · 문장 · ›, 줄 사이 구분선
import Popup from './Popup.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from './context.js'

const GROUPS = [['act', '지금 해 볼 일'], ['check', '확인해 주세요'], ['info', '안내']]

export default function NoticePopup({ items }) {
  const { closePopup } = usePopup()
  const open = (go) => { closePopup(); go() }
  const groupOf = (n) => n.group ?? (n.go ? 'check' : 'info')
  const groups = GROUPS.map(([key, label]) => [key, label, items.filter((n) => groupOf(n) === key)]).filter(([, , list]) => list.length)

  return (
    <Popup label="알림">
      <h2>알림</h2>
      {items.length === 0 && <p className="mc-muted mc-notice-list">새 알림이 없어요.</p>}
      {groups.map(([key, label, list]) => (
        <section className="mc-notice-group" key={key} aria-label={label}>
          {/* 묶음이 하나뿐이면 머리글은 생략 */}
          {groups.length > 1 && <h3 className="mc-notice-grp">{label}</h3>}
          <div className="mc-notice-list">
            {list.map((n) => {
              const Row = n.go ? 'button' : 'div'  // 안내(월급 상태)는 갈 화면이 없어서 누를 수 없고 화살표가 없다
              return (
                <Row {...(n.go ? { type: 'button', onClick: () => open(n.go) } : {})} className="mc-listrow" key={n.key}>
                  <span className={`mc-tile${n.tone ? ` mc-tone-${n.tone}` : ''}`}>{n.count != null ? <b>{n.count}</b> : n.mark ? <b className="mc-notice-mark">{n.mark}</b> : <Icon name={n.icon ?? 'Bell'} />}</span>
                  <span className="mc-grow">
                    {n.title && <strong className="mc-notice-title">{n.title}</strong>}
                    <span className="mc-notice-text">{n.text}</span>
                    {n.act && <span className="mc-notice-act">{n.act} ›</span>}
                  </span>
                  {n.go && !n.act && <span className="mc-muted" aria-hidden="true">›</span>}
                </Row>
              )
            })}
          </div>
        </section>
      ))}
    </Popup>
  )
}
