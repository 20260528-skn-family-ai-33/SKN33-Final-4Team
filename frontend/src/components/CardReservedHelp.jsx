// '지난 카드값' 옆 [?] (11/12) — 고정지출 화면에서 확인한 카드 청구서 금액과 왜 다르게 보이는지
// 사용자마다 이유가 달라서 숫자 대신 '~때문일 수 있어요'로 흔한 이유만 알려 준다 (서버 계산: budgets/protect.py issuer_deduction)
import HelpTip from './HelpTip.jsx'

export default function CardReservedHelp() {
  return (
    <HelpTip label="지난 카드값 안내" title="왜 청구서 금액과 달라 보이나요?">
      <li>지난 카드값은 가입 전 카드값이라 따로 떼어 둬요.</li>
      <li>청구서 속 이번 달 결제는 이미 소비라 빼요.</li>
      <li>지난 월급으로 먼저 낸 카드값도 빼요.</li>
      <li>가입 전 할부가 남아 있으면 이번 회차만큼 더해요.</li>
      <li>그래서 청구서 금액과 다를 수 있어요.</li>
    </HelpTip>
  )
}
