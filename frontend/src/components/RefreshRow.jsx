// "지금 새로고침" 한 줄 — 연결 상세(계좌·카드) 팝업에서 쓴다
// CODEF 를 너무 자주 부르면 기관이 IP 를 막을 수 있어서, 한 번 새로고침하면 10분 동안은 다시 못 하게 안내한다
// 지금은 흉내만 (실제 조회는 백엔드 연결 후). 마지막 새로고침 시각은 이 화면에 있는 동안만 기억한다
import { useState } from 'react'
import Icon from './Icon.jsx'

const COOLDOWN_MIN = 10
let lastAt = {}  // { 연결 이름: 마지막 새로고침 시각(ms) } — 팝업을 닫았다 열어도 유지

export default function RefreshRow({ target }) {
  const [state, setState] = useState(() => (lastAt[target] ? 'done' : 'idle'))  // idle · busy · done · wait
  const [waitMin, setWaitMin] = useState(COOLDOWN_MIN)  // 다시 할 수 있을 때까지 남은 분 (누를 때 계산)

  const click = () => {
    if (state === 'busy') return
    const passed = Date.now() - (lastAt[target] ?? 0)
    if (lastAt[target] && passed < COOLDOWN_MIN * 60_000) {
      setWaitMin(Math.max(1, Math.ceil((COOLDOWN_MIN * 60_000 - passed) / 60_000)))
      setState('wait')
      return
    }
    setState('busy')
    setTimeout(() => { lastAt = { ...lastAt, [target]: Date.now() }; setState('done') }, 1000)
  }

  const sub = {
    idle: null,
    busy: null,
    done: `방금 새로고침했어요 · ${COOLDOWN_MIN}분 뒤 다시 할 수 있어요`,
    wait: `${waitMin}분 뒤에 다시 할 수 있어요. 너무 자주 조회하면 은행이 접속을 막을 수 있어요.`,
  }[state]

  return (
    <button type="button" className="mc-listrow mc-refresh" aria-busy={state === 'busy'} onClick={click}>
      <span className={state === 'busy' ? 'mc-conn-busy' : undefined}><Icon name="RefreshCw" /></span>
      <span className="mc-grow">
        <span>{state === 'busy' ? '새로고침 중…' : '지금 새로고침'}</span>
        {sub && <small className={state === 'wait' ? 'mc-conn-fail' : 'mc-muted'}>{sub}</small>}
      </span>
    </button>
  )
}
