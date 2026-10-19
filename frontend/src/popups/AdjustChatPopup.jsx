// 팝업: 예산 조정 챗봇 — 추천 예산 "챗봇과 조정하기 ›"
// 지금은 고르는 것까지만. "조정안 받기"·질문 보내기는 챗봇 기능을 만들 때 붙인다
import { useState } from 'react'
import Popup from './Popup.jsx'
import Icon from '../components/Icon.jsx'

const CATS = ['식비', '주거/통신', '교통/차량', '생활용품', '건강/미용', '여가/관계']
const MY_PRIO = ['식비', '여가/관계', '건강/미용']  // 내 우선순위 (시안 예시값)
const AMOUNTS = ['1만원', '3만원', '5만원', '10만원']
const DIRS = ['늘리고 싶어', '줄이고 싶어']
const QUICK = [['식비', '식비를'], ['여가/관계', '여가비를'], ['교통/차량', '교통비를']]

export default function AdjustChatPopup() {
  const [amount, setAmount] = useState('3만원')
  const [dir, setDir] = useState('늘리고 싶어')

  return (
    <Popup>
      <div className="mc-chat">
        <div className="mc-chat-top">
          <span className="mc-chat-av"><Icon name="Sparkles" /></span>
          <div><h2>예산 조정 챗봇</h2><span className="mc-muted" style={{ fontSize: 11 }}>바꾼 내용은 적용하기 전까지 반영되지 않아요</span></div>
        </div>
        <div aria-live="polite" className="mc-chat-log">
          <div className="mc-msg bot">
            <div className="mc-bubble">어떤 카테고리를 조정할까요?<br />바꾼 만큼 나머지는 <b>내 우선순위</b>에 맞춰 다시 짜 드릴게요.</div>
            <div className="mc-prio-strip">
              <span className="mc-prio-strip-h">내 우선순위</span>
              {MY_PRIO.map((c, i) => <span className="mc-prio-pill" key={c}><em className="mc-rk">{i + 1}</em>{c}</span>)}
            </div>
          </div>
          <div className="mc-msg bot">
            <div className="mc-cf">
              <div className="mc-cf-line">
                <select aria-label="카테고리" defaultValue="식비">{CATS.map((c) => <option key={c}>{c}</option>)}</select>
                <span>를</span>
              </div>
              <div className="mc-cf-amts">
                {AMOUNTS.map((a) => <button key={a} type="button" aria-pressed={amount === a} onClick={() => setAmount(a)}>{a}</button>)}
                <label className="mc-cf-custom"><input aria-label="직접 입력 (만원)" inputMode="numeric" placeholder="직접" type="text" /><span>만원</span></label>
              </div>
              <div className="mc-cf-line">
                <span>만큼</span>
                <div className="mc-cf-dir">
                  {DIRS.map((d) => <button key={d} type="button" aria-pressed={dir === d} onClick={() => setDir(d)}>{d}</button>)}
                </div>
              </div>
              <button type="button" className="mc-primary mc-cf-go">조정안 받기</button>
            </div>
          </div>
        </div>
        <div className="mc-chat-q">
          {QUICK.map(([c, word]) => <button key={c} type="button">{word} 조정하고 싶어</button>)}
        </div>
        <div className="mc-chat-in">
          <input autoComplete="off" id="mc-chat-in" placeholder="예: 식비를 조정하고 싶어" type="text" />
          <button type="button" className="mc-chat-send" aria-label="보내기"><Icon name="Send" /></button>
        </div>
      </div>
    </Popup>
  )
}
