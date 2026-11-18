// 쿠숑이 질문 한 줄 (11/18 시안 O2): 작은 쿠숑이 얼굴 + 말풍선. 온보딩 화면(내 정보 · 계좌 연결)이 같이 쓴다
import Mascot from './Mascot.jsx'

export default function ChatAsk({ mood = 'planner', children }) {
  return (
    <div className="mc-chat-ask">
      <Mascot mood={mood} size={40} />
      <div className="mc-o2-bubble">{children}</div>
    </div>
  )
}
