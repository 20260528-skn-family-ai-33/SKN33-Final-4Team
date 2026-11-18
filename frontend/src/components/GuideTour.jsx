// 쿠숑이 가이드 투어 (11/16): 첫 예산 승인 뒤 홈에 처음 들어오면 마스코트 쿠숑이가 하단 탭을 하나씩 소개한다.
// 화면을 어둡게 덮고, 설명하는 탭만 밝게 비춘 뒤(구멍 난 테두리) 쿠숑이 말풍선 카드를 그 위에 띄운다.
// 열려 있는 동안 뒤 화면은 Layout 이 잠근다(inert). Esc · [건너뛰기] = 닫기, 마지막 [시작하기] = 닫기
// 본 기록은 utils/guideTour.js (서버 칸이 생기면 서버, 그 전에는 이 브라우저)
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import Mascot from './Mascot.jsx'
import { PATHS } from '../routes.js'

// mood = components/Mascot.jsx 의 표정 코드. 문장은 각 탭 화면이 실제로 하는 일만, 한두 줄로 (11/18 시안 T1 · T2)
// title = 탭 이름(작은 줄), head = 큰 한 줄, text = 보조 한 줄. 첫 장 · 마지막 장은 탭 없이 가운데
export const GUIDE_STEPS = [
  { mood: 'hello', title: '안녕하세요! 쿠숑이에요', text: '아래 다섯 탭을 하나씩 짚어 드릴게요.' },
  { tab: 'home', mood: 'planner', title: '홈', head: '이번 주 쓸 수 있는 돈', text: '남은 생활비와 곧 나갈 돈을 한눈에 봐요' },
  { tab: 'txs', mood: 'basic', title: '거래', head: '거래가 자동으로 모여요', text: "애매한 거래는 '확인 필요'로 알려 드려요" },
  { tab: 'budget', mood: 'planner', title: '예산', head: '카테고리마다 남은 돈', text: '넘을 것 같으면 쿠숑이가 조정을 도와요' },
  { tab: 'fund', mood: 'saver', title: '쿠션', head: '든든한 비상금, 머니쿠션', text: '목표까지 얼마나 모였는지 보여 드려요' },
  { tab: 'assistant', mood: 'fun', title: '챗봇', head: '궁금한 건 저한테 물어보세요', text: '소비 분석 · 예산 조정 · 청년 정책까지' },
  { mood: 'fun', title: '준비 끝!', text: '이 안내는 마이메뉴에서 언제든 다시 볼 수 있어요.' },
]

// 비출 탭 (components/TabBar.jsx 의 링크)
const TARGET = {
  home: '#mc-nav .mc-home',
  txs: `#mc-nav a[href="${PATHS.txs}"]`,
  budget: `#mc-nav a[href="${PATHS.budget}"]`,
  fund: `#mc-nav a[href="${PATHS.fund}"]`,
  assistant: `#mc-nav a[href="${PATHS.assistant}"]`,
}
const PAD = 10

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
      // 탭 칸 전체가 아니라 아이콘 + 글자만 감싼다 (칸 폭이 넓어 빛이 화면 끝에 잘리지 않게)
      const parts = [...el.querySelectorAll('svg, span')].map((x) => x.getBoundingClientRect()).filter((x) => x.width)
      const r = parts.length ? {
        left: Math.min(...parts.map((x) => x.left)), top: Math.min(...parts.map((x) => x.top)),
        width: Math.max(...parts.map((x) => x.right)) - Math.min(...parts.map((x) => x.left)),
        height: Math.max(...parts.map((x) => x.bottom)) - Math.min(...parts.map((x) => x.top)),
      } : el.getBoundingClientRect()
      // 탭 크기와 관계없이 동그란 빛으로 (네모난 흰 칸이 튀지 않게). 카드 꼬리는 탭 가운데를 가리킨다
      const size = Math.max(r.width, r.height) + PAD * 2
      const cx = r.left - d.left + r.width / 2
      const cy = r.top - d.top + r.height / 2
      setSpot({ left: cx - size / 2, top: cy - size / 2, size, cardBottom: d.bottom - (cy - size / 2) + 14, caret: cx - 16 })
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

  const first = step === 0
  const centered = first || last  // 첫 장 · 마지막 장 = 어두운 화면 가운데 쿠숑이 (시안 T2)
  // 막대 = 탭 소개 장만 (첫 장 · 마지막 장 제외). '1 / 5' 와 같은 수만큼 채운다
  const tabSteps = GUIDE_STEPS.length - 2
  const dots = (
    <span className="mc-tour-dots" role="img" aria-label={`${tabSteps}단계 중 ${step}단계`}>
      {GUIDE_STEPS.slice(1, -1).map((s, i) => <i key={s.title} className={i < step ? 'on' : undefined} />)}
    </span>
  )

  return (
    <div className="mc-tour" ref={box} role="dialog" aria-modal="true" aria-labelledby="mc-tour-title" aria-describedby="mc-tour-text">
      {spot && !centered
        ? <div className="mc-tour-spot" aria-hidden="true" style={{ left: spot.left, top: spot.top, width: spot.size, height: spot.size }} />
        : <div className="mc-tour-dim" aria-hidden="true" />}
      {centered ? (
        <div className="mc-tour-hello">
          <Mascot mood={current.mood} size={150} />
          <strong id="mc-tour-title">{current.title}</strong>
          <p id="mc-tour-text">{current.text}</p>
          <button type="button" ref={next} className="mc-tour-next" onClick={() => (last ? onClose() : setStep(1))}>
            {last ? '시작하기' : '둘러보기'}
          </button>
          {first && <button type="button" className="mc-tour-skip" onClick={onClose}>건너뛰기</button>}
        </div>
      ) : (
        /* 탭 소개 = 아래 시트 + 탭 동그란 빛 (시안 T1). 탭을 못 찾으면 화면 가운데 */
        <div className={`mc-tour-card${spot ? '' : ' is-center'}`} style={spot ? { bottom: spot.cardBottom } : undefined}>
          <div className="mc-tour-who">
            <Mascot mood={current.mood} size={96} />
            <div className="mc-tour-say">
              <small><span>{step} / {GUIDE_STEPS.length - 2}</span> · <span id="mc-tour-title">{current.title}</span></small>
              <div id="mc-tour-text"><strong>{current.head}</strong><p>{current.text}</p></div>
            </div>
            <button type="button" className="mc-tour-skip mc-tour-x" onClick={onClose}>건너뛰기</button>
          </div>
          {dots}
          <div className="mc-tour-btns">
            <button type="button" className="mc-tour-skip mc-tour-prev" onClick={() => setStep(step - 1)}>이전</button>
            <button type="button" ref={next} className="mc-tour-next" onClick={() => setStep(step + 1)}>다음</button>
          </div>
        </div>
      )}
    </div>
  )
}
