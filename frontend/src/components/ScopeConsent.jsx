// 금융 조회 동의 한 범위 (bank · card · loan) — 계좌 · 카드 · 대출 연결 화면마다 맨 아래에 그 범위 동의만 (11/18)
// 체크박스 + 이름(누르면 약관 상세 팝업, 팝업 안 '동의하고 닫기' = 체크). 문서는 서버(GET /api/consent-documents/)
// 저장은 saveScopeConsent: 체크한 문서만 화면에 보여 준 버전 그대로 POST /api/finance/consents/ (같은 버전 동의가 있으면 서버가 새로 만들지 않음)
import TermsPopup from '../popups/TermsPopup.jsx'
import { useConsentDocs } from '../popups/consentDocs.js'
import { usePopup } from '../popups/context.js'
import { apiSend, newRequestKey } from '../utils/api.js'

export const useScopeConsent = (scope, initial) =>
  useConsentDocs((d) => d.purpose === 'finance_inquiry' && d.productScope === scope, initial)

export function saveScopeConsent(consent) {
  return apiSend('/api/finance/consents/', 'POST', {
    consents: consent.docs.filter(consent.isAgreed).map((d) => ({ purpose: d.purpose, productScope: d.productScope, documentVersion: d.version })),
  }, { requestKey: newRequestKey() })
}

export default function ScopeConsent({ consent, onChange, note }) {
  const { openPopup } = usePopup()
  const set = (d, on) => { consent.setOne(d, on); onChange?.() }
  return (
    <div className="mc-card mc-chat-answer mc-consent-card">
      {consent.error ? (
        <div role="alert">
          <p className="mc-error">동의 문서를 불러오지 못했어요. 문서를 확인해야 연결할 수 있어요.</p>
          <button type="button" className="mc-link" onClick={consent.reload}>다시 불러오기</button>
        </div>
      ) : !consent.docs ? (
        <p className="mc-muted">동의 문서를 불러오는 중…</p>
      ) : consent.docs.map((d) => (
        <div className="mc-terms-row" key={`${d.productScope}:${d.version}`}>
          <input aria-label={d.document.label} checked={consent.isAgreed(d)} id={`mc-consent-${d.productScope}`} type="checkbox"
            onChange={(e) => set(d, e.target.checked)} />
          <button type="button" className="mc-terms-open" aria-haspopup="dialog"
            onClick={() => openPopup(<TermsPopup doc={d.document} onAgree={() => set(d, true)} />)}>
            {d.document.label}<span>보기</span>
          </button>
        </div>
      ))}
      {note && <p className="mc-picked mc-consent-note">{note}</p>}
    </div>
  )
}
