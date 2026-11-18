// 시작하기
// 가입 · 로그인 바로 다음 첫 화면이라 뒤로 가기 버튼이 없다 (11/12 피드백: 뒤로 가면 로그인된 채 로그인 화면이 나왔다)
// 11/18 시안 O3 라임 스토리 카드: 넘겨 보는 카드 세 장. 옆으로 밀거나 [다음]으로 넘기고, 마지막 장에서 [시작하기]
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Mascot from '../components/Mascot.jsx'
import { PATHS } from '../routes.js'

// 카드 세 장: 라임 그림 칸(쿠숑이 + 작은 미리보기) · 큰 문장 · 한 줄
const CARDS = [
  {
    mood: 'default', title: <>이번 주에 얼마 써도 되는지<br />바로 알려 줘요</>, text: '월급날까지 남은 생활비를 주 단위로 나눠 드려요.',
    peek: <><small>이번 주 쓸 수 있는 돈</small><b>83,750원</b></>,
  },
  {
    mood: 'planner', title: <>월급이 들어오면<br />세 칸으로 나눠요</>, text: '생활비 · 저축 · 쿠션으로 나눠서 흔들리지 않게 받쳐 줘요.',
    peek: <span className="mc-o3-chips"><i>생활비</i><i>저축</i><i>쿠션</i></span>,
  },
  {
    mood: 'hello', title: <>몇 가지만 물어볼게요<br />5분이면 끝나요</>, text: '내 정보 · 계좌 연결 · 월급 · 지킬 돈 · 추천 예산 순서예요.',
    peek: <><small>다섯 단계</small><b>5분</b></>,
  },
]

export default function OnboardPage() {
  const navigate = useNavigate()
  const [at, setAt] = useState(0)
  const [touchX, setTouchX] = useState(null)
  const last = at === CARDS.length - 1
  const card = CARDS[at]
  const go = (i) => setAt(Math.max(0, Math.min(CARDS.length - 1, i)))
  const start = () => navigate(PATHS.profile)
  // 옆으로 40px 넘게 밀면 넘긴다
  const onTouchEnd = (e) => {
    if (touchX == null) return
    const dx = e.changedTouches[0].clientX - touchX
    if (Math.abs(dx) > 40) go(at + (dx < 0 ? 1 : -1))
    setTouchX(null)
  }

  return (
    <Layout title="시작하기" bare>
      <div className="mc-o3-story" onTouchStart={(e) => setTouchX(e.touches[0].clientX)} onTouchEnd={onTouchEnd}>
        <div className="mc-o3-skip">{!last && <button type="button" onClick={start}>건너뛰기</button>}</div>
        <div className="mc-o3-slide" key={at} aria-live="polite">
          <div className="mc-o3-art">
            <div className="mc-o3-peek">{card.peek}</div>
            <Mascot mood={card.mood} size={180} />
          </div>
          <h1>{card.title}</h1>
          <p>{card.text}</p>
        </div>
        <div className="mc-o3-storyfoot">
          <span className="mc-o3-dots" role="img" aria-label={`${CARDS.length}장 중 ${at + 1}장`}>
            {CARDS.map((_, i) => <i key={i} className={i === at ? 'on' : undefined} />)}
          </span>
          <button type="button" className="mc-o3-next" onClick={() => (last ? start() : go(at + 1))}>{last ? '시작하기' : '다음'}</button>
        </div>
      </div>
    </Layout>
  )
}
