// 머니쿠션 소개 (쿠션 탭 [?] · 지킬 돈 설정 '월 쿠션 적립금' [!] 창 내용): 정의 → 쓰는 곳 세 칸 → 쌓이는 방식
import Icon from './Icon.jsx'

const USES = [['Stethoscope', '병원비'], ['Heart', '경조사'], ['Wrench', '수리비']]
export default function CushionIntro() {
  return (
    <>
      <p className="mc-cushion-def">갑자기 큰돈이 필요할 때 꺼내 쓰는 <b>비상금</b>이에요.<br />급할 때 저축을 깨지 않게 받쳐 줘요.</p>
      <div className="mc-cushion-uses">
        {USES.map(([icon, text]) => <div key={text}><span className="mc-tile"><Icon name={icon} /></span>{text}</div>)}
      </div>
      <h3 className="mc-cushion-sub">이렇게 쌓여요</h3>
      <ul className="mc-help-list">
        <li>월급날 이틀 뒤에 예산에서 정한 쿠션 적립금을 추정액에 더해요.</li>
        <li>지킬 돈 설정에서 적립금을 바꾸면 같이 바뀌어요.</li>
        <li>실제 계좌의 돈이 옮겨지지는 않아요. 저축 계좌 잔액은 참고로만 보여 줘요.</li>
      </ul>
    </>
  )
}
