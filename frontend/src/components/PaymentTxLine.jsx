// 납부 거래 확인 팝업의 출금 한 줄 (적요 · 날짜 · 계좌 · 금액)
import { won } from '../utils/money.js'

export default function PaymentTxLine({ t }) {
  return <>
    <span className="mc-grow">{t.description || '적요 없음'}<br /><small className="mc-muted">{t.date} · {t.account?.displayName ?? '계좌 확인 필요'}</small></span>
    <strong>{won(t.amount)}</strong>
  </>
}
