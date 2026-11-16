// 팝업: 예산 조정 AI 전송 동의 (11/11). 11/12: 아래 시트 대신 가운데 창 (내용이 길면 창 안에서 스크롤), [취소]는 위 [닫기 ×]와 같아서 뺐다
// 챗봇에 예산 조정을 요청했는데 동의 이력이 없어 서버가 거절(budget_adjust_consent_required)했을 때 열린다.
// 동의를 저장하면 onAgreed — 챗봇 화면이 거절됐던 요청을 그대로 다시 보낸다. 취소하면 아무것도 보내지 않는다.
// 문서는 열면서 불러오지만 체크는 사용자가 직접 한다. 동의만으로 예산은 바뀌지 않는다.
// 동의 이력 확인 · 철회는 마이 > 동의·데이터 관리 (PrivacyPopup). 동의를 저장하면 토스트로 짧게 알린다 (11/12)
import { useCallback, useEffect, useRef, useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { apiSend } from '../utils/api.js'
import { toast } from '../utils/toast.js'

const URL = '/api/budgets/adjustment-ai-consent/'
const NONE = { main: false, health: false }

export default function BudgetAdjustConsentPopup({ onAgreed }) {
  const { closePopup } = usePopup()
  const [state, setState] = useState(null)
  const [checks, setChecks] = useState(NONE)
  const [opened, setOpened] = useState(null)  // 펼쳐 보는 문서: 'main' · 'health' · null
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const working = useRef(false)
  const load = useCallback(() => apiSend(URL, 'GET').then((next) => {
    setState(next)
    setError('')
  }, (err) => {
    setState(null)
    setError(err.status ? err.message : '동의 문서를 불러오지 못했어요. 연결 상태를 확인한 뒤 다시 시도해 주세요.')
  }), [])
  useEffect(() => { load() }, [load])

  const done = () => { closePopup(); onAgreed?.() }
  const both = checks.main && checks.health
  const save = async () => {
    if (working.current || !state?.available || !both) return
    working.current = true
    setBusy(true)
    setError('')
    try {
      await apiSend(URL, 'PUT', {
        agreed: true, healthAgreed: true, documentVersion: state.documentVersion, documentHash: state.documentHash,
        healthDocumentVersion: state.healthDocumentVersion, healthDocumentHash: state.healthDocumentHash,
      })
      toast('동의했어요. 철회는 마이에서 할 수 있어요')
      done()
    } catch (err) {
      setChecks(NONE)
      if (err.code === 'document_outdated') {
        await load()  // 바뀐 문서를 다시 보여 주고 처음부터 확인하게 한다
        setError('동의 내용이 바뀌었어요. 다시 확인해 주세요.')
      } else if (err.code === 'budget_ai_unavailable') {
        setState({ ...state, available: false })
        setError(err.message)
      } else {
        setError(err.status ? err.message : '동의 처리 결과를 확인하지 못했어요. 다시 시도해 주세요.')
      }
    } finally {
      working.current = false
      setBusy(false)
    }
  }

  const docs = state ? [['main', state.document], ['health', state.healthDocument]] : []
  const granted = state?.status === 'granted'
  return (
    <Popup label="예산 조정 AI 전송 동의" dialog>
      <h2 className="mc-adjust-consent-title">예산 조정을 하려면 동의가 필요해요</h2>
      <p className="mc-muted">예산 조정 챗봇은 요청을 이해하고 조정안을 만들 때 외부 AI(OpenAI)를 써요. 보내는 항목은 아래 문서에서 확인할 수 있어요. 동의만으로 예산은 바뀌지 않아요.</p>
      {!state && !error && <p role="status">동의 문서를 불러오는 중이에요.</p>}
      {granted && <p role="status">이미 동의했어요. 이어서 진행할 수 있어요.</p>}
      {state && !granted && state.available === false && <p className="mc-note">현재 이 AI 기능이 꺼져 있어 새 동의는 받지 않아요.</p>}
      {state && !granted && state.available && (
        <div className="mc-consent-ask">
          {state.status === 'needs_renewal' && <p className="mc-note">동의 내용이 바뀌어 다시 확인이 필요해요.</p>}
          {docs.map(([key, doc]) => (
            <div key={key}>
              <div className="mc-terms-row">
                <input type="checkbox" aria-label={doc.label} checked={checks[key]} disabled={busy}
                  onChange={(e) => setChecks({ ...checks, [key]: e.target.checked })} />
                <button type="button" className="mc-terms-open" aria-expanded={opened === key}
                  onClick={() => setOpened(opened === key ? null : key)}>
                  {doc.label}<span>{opened === key ? '접기' : '보기'}</span>
                </button>
              </div>
              {opened === key && (
                <div className="mc-terms-doc mc-consent-doc">
                  {doc.sections.map((s) => (
                    <section key={s.h}>
                      <h3>{s.h}</h3>
                      {s.items.map((t) => <p key={t}>{t}</p>)}
                    </section>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {error && <p className="mc-error" role="alert">{error}</p>}
      {!state && error && <button type="button" className="mc-secondary" onClick={load}>동의 문서 다시 불러오기</button>}
      {granted && <button type="button" className="mc-primary" onClick={done}>계속하기</button>}
      {state && !granted && state.available && (
        <button type="button" className="mc-primary" disabled={busy || !both} onClick={save}>{busy ? '저장하는 중…' : '동의하고 계속'}</button>
      )}
    </Popup>
  )
}
