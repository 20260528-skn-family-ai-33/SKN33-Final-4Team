// 기관 고르기 칸 (계좌 연결 · 카드 연결 화면이 같이 쓴다): 쿠숑이 질문 + 칩 + 더 보기 + 고른 기관 이름
import { useState } from 'react'
import ChatAsk from './ChatAsk.jsx'
import { josa } from '../utils/adjust.js'

// 칩 여러 개 + "다른 ○○ 보기" + "2곳 선택 · 신한, 국민" 을 한 덩어리로
// items = utils/institutions.js 의 BANKS · CARDS. 자주 쓰는 곳(popular)만 먼저 보이고 나머지는 펼친다
// soon = 아직 연결할 수 없는 곳. 펼치면 토글 아래에 누를 수 없는 칩으로 보인다 (은행)
export default function PickCard({ title, hint, items, picked, moreLabel, notSupported = [], soon = [], className = '' }) {
  const [open, setOpen] = useState(false)
  // 펼치지 않았어도 이미 고른 기관은 보이게
  const shown = items.filter((x) => open || x.popular || picked.has(x.name))
  const rest = items.length - items.filter((x) => x.popular).length + soon.length

  return (
    <>
    <ChatAsk>{title} <span className="mc-opt">{hint}</span></ChatAsk>
    <div className={`mc-card mc-chat-answer ${className}`}>
      <div className="mc-chipbar">
        {shown.map((x) => (
          <button key={x.name} type="button" aria-pressed={picked.has(x.name)} onClick={() => picked.toggle(x.name)}>
            {x.label}
          </button>
        ))}
      </div>
      {rest > 0 && <button type="button" className="mc-link mc-pick-more" aria-expanded={open} onClick={() => setOpen(!open)}>
        {soon.length ? `${moreLabel} ${rest}곳` : open ? '접기' : `${moreLabel} ${rest}곳 더 보기`} <span className="mc-pick-arrow" aria-hidden="true">▼</span>
      </button>}
      {/* 준비 중인 곳은 토글 아래에서 펼쳐진다 (누를 수 없음) */}
      {soon.length > 0 && (
        <div className="mc-pick-soon" data-open={open} aria-hidden={!open}>
          <div><div className="mc-chipbar mc-chipbar-soon">{soon.map((label) => <button key={label} type="button" disabled>{label}</button>)}</div></div>
        </div>
      )}
      {notSupported.length > 0 && <p className="mc-picked">{notSupported.join('·')}{josa(notSupported.at(-1), '은', '는')} 아직 지원하지 않아요.</p>}
      {/* 고른 순서가 아니라 화면 순서대로 이름을 보여 준다 */}
      <p className="mc-picked"><b>{picked.list.length}곳</b> 선택 · {items.filter((x) => picked.has(x.name)).map((x) => x.label).join(', ')}</p>
    </div>
    </>
  )
}
