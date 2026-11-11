// 팝업: 머니쿠션 도움말 — 마이 > 도움말 · 거래 필터 '이체 · 저축 제외 기준 보기' (11/9: 두 줄 → 앱 소개 · 쓰는 순서 · 쉬운 용어)
// 금융 개념 설명보다 '사회초년생이 월급을 나눠 쓰도록 돕는 가이드'라는 점을 먼저. 돈을 보내거나 옮기지 않는다는 점을 분명히
import Popup from './Popup.jsx'

const STEPS = [
  ['월급 확인', '이번 달 월급이 들어왔는지 확인해요.'],
  ['꼭 나갈 돈 확인', '월세 · 통신비 · 카드값 · 대출처럼 다음 월급 전에 빠져나갈 돈을 먼저 챙겨요.'],
  ['지킬 돈 정하기', '저축과 쿠션(비상금)처럼 쓰지 않고 남겨 둘 돈을 정해요.'],
  ['남은 돈으로 생활하기', '나머지를 식비 · 교통 같은 생활비로 나누고, 홈에서 하루에 쓸 수 있는 돈을 보며 써요.'],
  ['바꾸고 싶으면 챗봇에게', '예산을 바꾸고 싶을 때 챗봇에게 말하면 조정안을 만들어 줘요. 확인하고 승인해야 바뀌어요.'],
]

const WORDS = [
  ['꼭 나갈 돈', '다음 월급날 전까지 통장에서 빠져나갈 돈이에요.'],
  ['지킬 돈', '저축 · 쿠션처럼 생활비로 쓰지 않고 지켜 둘 돈이에요.'],
  ['쿠션', '갑자기 병원비 · 수리비가 생겨도 흔들리지 않게 모아 두는 비상금 계획이에요.'],
  ['여윳돈', '어느 예산에도 아직 나누지 않은 돈이에요. 생활비를 넘게 쓰면 여기서 먼저 채워요.'],
  ['하루에 쓸 수 있는 돈', '남은 생활비를 다음 월급날까지 남은 날로 나눈 금액이에요.'],
]

export default function HelpPopup() {
  return (
    <Popup label="머니쿠션 도움말" sub>
      <div className="mc-eyebrow">도움말</div>
      <h1>머니쿠션은 이런 앱이에요</h1>
      <p className="mc-muted">처음 월급을 받는 사회초년생이 <strong>월급을 어떻게 나눠 쓸지</strong> 같이 정하고, 다음 월급날까지 잘 버티도록 돕는 가이드예요.</p>
      <div className="mc-card">
        <ul className="mc-help-list" style={{ marginTop: 0 }}>
          <li>돈을 대신 보내거나 계좌 사이로 옮기지 않아요. 송금 · 결제 기능이 없어요.</li>
          <li>연결한 계좌 · 카드는 내역을 <strong>읽기만</strong> 해요.</li>
          <li>앱에 보이는 예산 · 쿠션 금액은 계획이에요. 통장 속 돈은 그대로예요.</li>
        </ul>
      </div>

      <h3 className="mc-loan-h">이렇게 써 보세요</h3>
      <ol className="mc-help-steps">
        {STEPS.map(([title, text]) => <li key={title}><strong>{title}</strong><span>{text}</span></li>)}
      </ol>

      <h3 className="mc-loan-h">자주 보는 말</h3>
      <div className="mc-card">
        {WORDS.map(([word, text]) => <div className="mc-help-word" key={word}><strong>{word}</strong><span>{text}</span></div>)}
      </div>

      <h3 className="mc-loan-h">알아 두면 좋아요</h3>
      <ul className="mc-help-list">
        <li>저축 · 투자 · 내 계좌끼리 옮긴 돈은 소비로 세지 않아요.</li>
        <li>카드값은 카드로 쓴 거래와 겹쳐 두 번 빼지 않아요.</li>
      </ul>
    </Popup>
  )
}
