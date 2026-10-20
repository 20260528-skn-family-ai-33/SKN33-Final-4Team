// 팝업: 예산 조정 챗봇 — 추천 예산 "챗봇과 조정하기 ›"
// 지금은 고르는 것까지만. "조정안 받기"·질문 보내기는 챗봇 기능을 만들 때 붙인다
// 고르기 말풍선(AdjustForm)은 통합 챗봇과 같이 쓴다
import Popup from './Popup.jsx'
import Icon from '../components/Icon.jsx'
import AdjustForm from '../components/AdjustForm.jsx'

const MY_PRIO = ['식비', '문화·여가', '뷰티·미용']  // 내 우선순위 (시안 예시값)
const QUICK = [['식비', '식비를'], ['문화·여가', '여가비를'], ['교통·차량', '교통비를']]

export default function AdjustChatPopup() {
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
            <AdjustForm initial={{ cat: '식비', amount: 3, dir: 'up' }} />
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
