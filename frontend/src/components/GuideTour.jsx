// 쿠숑이 가이드 투어 (11/16): 첫 예산 승인 뒤 홈에 처음 들어오면 마스코트 쿠숑이가 하단 탭을 하나씩 소개한다.
// 화면을 어둡게 덮고, 설명하는 탭만 밝게 비춘 뒤(구멍 난 테두리) 쿠숑이 말풍선 카드를 그 위에 띄운다.
// 열려 있는 동안 뒤 화면은 Layout 이 잠근다(inert). Esc · [건너뛰기] = 닫기, 마지막 [시작하기] = 닫기
// 본 기록은 utils/guideTour.js (서버 칸이 생기면 서버, 그 전에는 이 브라우저)
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import Mascot from './Mascot.jsx'
import { PATHS } from '../routes.js'

// mood = components/Mascot.jsx 의 표정 코드. 문장은 각 탭 화면이 실제로 하는 일만 (화면 머리 주석 기준)
export const GUIDE_STEPS = [
  { mood: 'hello', title: '안녕하세요! 쿠숑이에요',
    text: '앞으로 여러분의 돈 관리를 제가 함께 도와드릴게요. 아래 탭마다 무엇을 할 수 있는지 짧게 안내해 드릴게요.' },
  { tab: 'home', mood: 'planner', title: '홈',
    text: '이번 급여 주기에 하루 쓸 수 있는 돈과 계좌 잔액, 꼭 나갈 돈을 한눈에 볼 수 있어요.' },
  { tab: 'txs', mood: 'basic', title: '거래',
    text: "연결한 계좌·카드 거래가 자동으로 모여요. 어디에 쓴 돈인지 애매한 거래는 '확인 필요'로 알려 드려요." },
  { tab: 'budget', mood: 'planner', title: '예산',
    text: '카테고리별 예산과 남은 돈을 확인해요. 꼭 지키고 싶은 예산은 자물쇠로 잠가 둘 수 있어요.' },
  { tab: 'fund', mood: 'saver', title: '쿠션',
    text: '갑자기 돈이 필요할 때를 대비한 머니 쿠션을 모으는 곳이에요. 목표까지 얼마나 모였는지 보여 드려요.' },
  { tab: 'assistant', mood: 'fun', title: '챗봇',
    text: '궁금한 건 저한테 물어보세요! 소비 분석, 예산 조정, 청년 정책까지 같이 살펴볼게요.' },
  { mood: 'fun', title: '준비 끝!',
    text: "이제 시작해 볼까요? 이 안내는 마이메뉴의 '쿠숑이 가이드 다시 보기'에서 언제든 다시 볼 수 있어요." },
]

// 비출 탭 (components/TabBar.jsx 의 링크)
const TARGET = {
  home: '#mc-nav .mc-home',
  txs: `#mc-nav a[href="${PATHS.txs}"]`,
  budget: `#mc-nav a[href="${PATHS.budget}"]`,
  fund: `#mc-nav a[href="${PATHS.fund}"]`,
  assistant: `#mc-nav a[href="${PATHS.assistant}"]`,
}
const PAD = 3  // 아이콘 · 글자 묶음에서 빛 테두리까지 여백
const LIFT = 2  // 원을 살짝 위로 (아래 탭 줄 바닥 여백 때문에 묶음 정가운데보다 위가 눈에 가운데로 보인다)

export default function GuideTour({ onClose }) {
  const [step, setStep] = useState(0)
  const [spot, setSpot] = useState(null)
  const box = useRef(null)
  const next = useRef(null)
  const current = GUIDE_STEPS[step]
  const last = step === GUIDE_STEPS.length - 1

  // 비출 탭 위치 (기기 틀 .mc-device 기준). 탭을 못 찾으면 가운데 카드로
  useLayoutEffect(() => {
    const place = () => {
      const device = box.current?.closest('.mc-device')
      const el = current.tab && device?.querySelector(TARGET[current.tab])
      if (!device || !el) return setSpot(null)
      const d = device.getBoundingClientRect()
      // 11/17: 아이콘 + 글자 묶음을 원 정가운데에, 묶음을 딱 감싸는 크기로. 가운데 홈은 동그란 버튼 둘레
      const parts = el.classList.contains('mc-home') ? [el.getBoundingClientRect()]
        : [...el.querySelectorAll('svg, span')].map((x) => x.getBoundingClientRect()).filter((x) => x.width)
      if (!parts.length) parts.push(el.getBoundingClientRect())
      const left = Math.min(...parts.map((r) => r.left)), right = Math.max(...parts.map((r) => r.right))
      const top = Math.min(...parts.map((r) => r.top)), bottom = Math.max(...parts.map((r) => r.bottom))
      const cx = (left + right) / 2
      const cy = (top + bottom) / 2
      // 홈 버튼은 원이라 폭의 절반, 탭 묶음은 네모라 모서리까지(대각선의 절반)
      const reach = el.classList.contains('mc-home') ? (right - left) / 2 : Math.hypot(right - left, bottom - top) / 2
      const size = Math.round((reach + PAD) * 2)
      // 아래는 기기 틀(.mc-device) 기준 좌표. 카드 아래 끝은 틀 높이 기준 (PC 에서 틀이 화면 위에서 떨어져 있어도 맞게)
      // 가운데 홈 버튼만 눈으로 맞춘 미세 보정 (왼쪽 0.5px · 아래 0.5px)
      const home = el.classList.contains('mc-home')
      const x = cx - d.left - (home ? 0.5 : 0)
      const y = cy - d.top - LIFT + (home ? 0.5 : 0)
      setSpot({ left: x - size / 2, top: y - size / 2, size, cardBottom: d.height - (y - size / 2) + 14, caret: x - 16 })
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [current.tab])

  useEffect(() => { next.current?.focus() }, [step])
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="mc-tour" ref={box} role="dialog" aria-modal="true" aria-labelledby="mc-tour-title" aria-describedby="mc-tour-text">
      {spot
        ? <div className="mc-tour-spot" aria-hidden="true" style={{ left: spot.left, top: spot.top, width: spot.size, height: spot.size }} />
        : <div className="mc-tour-dim" aria-hidden="true" />}
      <div className={`mc-tour-card${spot ? '' : ' is-center'}`} style={spot ? { bottom: spot.cardBottom, '--mc-tour-caret': `${spot.caret}px` } : undefined}>
        <div className="mc-tour-who">
          <Mascot mood={current.mood} size={84} />
          <div className="mc-tour-say">
            <strong id="mc-tour-title">{current.title}</strong>
            <p id="mc-tour-text">{current.text}</p>
          </div>
        </div>
        <div className="mc-tour-foot">
          <span className="mc-tour-dots" role="img" aria-label={`${GUIDE_STEPS.length}단계 중 ${step + 1}단계`}>
            {GUIDE_STEPS.map((s, i) => <i key={s.title} className={i === step ? 'on' : undefined} />)}
          </span>
          <span className="mc-tour-btns">
            {!last && <button type="button" className="mc-tour-skip" onClick={onClose}>건너뛰기</button>}
            {step > 0 && !last && <button type="button" className="mc-tour-skip" onClick={() => setStep(step - 1)}>이전</button>}
            <button type="button" ref={next} className="mc-tour-next" onClick={() => (last ? onClose() : setStep(step + 1))}>
              {last ? '시작하기' : step === 0 ? '좋아요' : '다음'}
            </button>
          </span>
        </div>
      </div>
    </div>
  )
}
