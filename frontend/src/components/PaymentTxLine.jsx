// 납부 거래 확인 팝업의 출금 한 줄 (적요 · 날짜 · 계좌 · 금액)
import { won } from '../utils/money.js'

export default function PaymentTxLine({ t }) {
  return <>
    {/* 적요 · 계좌는 한 줄 말줄임, 금액은 줄바꿈 없이 오른쪽 (11/9) */}
    <span className="mc-grow mc-ptx"><span>{t.description || '적요 없음'}</span><small className="mc-muted">{t.date} · {t.account?.displayName ?? '계좌 확인 필요'}</small></span>
    <strong className="mc-ptx-amt">{won(t.amount)}</strong>
  </>
}
