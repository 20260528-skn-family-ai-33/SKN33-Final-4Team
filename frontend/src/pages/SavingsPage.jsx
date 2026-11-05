// 저축 확인 · 정기 저축 관리 — 한 화면을 두 곳에서 쓴다 (docs/api/savings.md)
//   첫 설정 흐름: 고정지출 확인 다음, 대출 확인 전. 찾은 후보마다 "저축인가요?"를 묻는다
//   마이 > 정기 저축 (location.state.from === 'my'): 확정된 저축 목록 + [수정] (10/22 PR #29 리뷰)
// 목록 = GET /api/budgets/savings/. 내 계좌끼리 매달 같은 금액을 보낸 이체를 찾은 후보가 있을 때만 "저축인가요?"를 묻는다 (TXN-005 · TXN-008)
//   저축이에요 → 후보 확정(confirm). 확정된 것의 월 합계 = 저축 최소 금액 (서버가 계산, S-02)
//   아니에요   → 후보 무시(ignore). 내 계좌끼리 옮긴 돈으로만 본다 (소비 아님 · 저축 아님)
// 저축은 사용자 확인 없이 자동으로 확정하지 않는다 (TXN-005). 자동 감지는 아직 없어 후보가 없으면 직접 추가 · 계속하기만 보여 준다
// [+ 저축 직접 추가] = 자동으로 못 찾는 저축(다른 은행 적금 등). 바로 확정 (S-03)
// 찾은 후보의 [수정]은 이름 · 금액 · 이체일만 (계좌는 못 바꿈, S-11) → SavingPopup
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import SavingPopup from '../popups/SavingPopup.jsx'
import { won } from '../utils/money.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import { PATHS } from '../routes.js'

const ANSWERS = [['yes', '저축이에요'], ['no', '아니에요']]

// 저장 뒤 같은 목록을 다시 받는다 — useApi 는 주소가 바뀔 때만 다시 받아서 서버가 무시하는 ?v= 를 바꾼다
function useSavingsList() {
  const [version, setVersion] = useState(0)
  const { data, error } = useApi(`/api/budgets/savings/?v=${version}`)
  return { data, error, reload: () => setVersion((v) => v + 1) }
}

// "매월 25일 · 국민 급여통장 · ****5678 → 신한 자유적금 · ****9012"
function savingLine(s) {
  const day = s.transferDay ? `매월 ${s.transferDay}일` : '이체일 모름'
  const from = s.fromAccount?.displayName
  const to = s.toAccount?.displayName
  const route = from || to ? `${from ?? '보내는 계좌 모름'} → ${to ?? '다른 은행'}` : '계좌 모름'
  return `${day} · ${route}`
}

// 마이 > 정기 저축
function ManageView() {
  const { openPopup } = usePopup()
  const { data, error, reload } = useSavingsList()
  if (!data) return <Layout title="정기 저축" showBack><ApiStatus error={error} /></Layout>
  const confirmed = data.items.filter((s) => s.status === 'confirmed')

  return (
    <Layout title="정기 저축" showBack>
      <div className="mc-eyebrow">정기 저축</div>
      <h1>매달 하는 저축</h1>
      <p className="mc-muted">여기 있는 저축은 예산에서 먼저 떼어 둬요. 금액을 바꾸거나 그만두면 다음 달 예산부터 반영돼요.</p>

      {confirmed.length === 0 && (
        <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>등록된 정기 저축이 없어요. 매달 하는 저축이 있으면 추가해 주세요.</p></div>
      )}
      {confirmed.map((s) => (
        <div className="mc-card" key={s.savingId}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{s.name}</strong><span className="mc-muted" style={{ display: 'block', marginTop: 4 }}>{savingLine(s)}</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(s.amount)}</strong>
          </div>
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">{s.sourceType === 'manual' ? '직접 추가' : '자동으로 찾음'}</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<SavingPopup item={s} manage onSaved={reload} />)}>수정</button>
          </div>
        </div>
      ))}
      <button type="button" className="mc-secondary" onClick={() => openPopup(<SavingPopup manage onSaved={reload} />)}>+ 저축 직접 추가</button>

      <div className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>매달 꼭 저축할 돈</span><strong>{won(data.summary.minimumMonthly)}</strong></div>
        <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>지킬 돈 설정에서 저축을 이 금액보다 적게 정할 수 없어요.</p>
      </div>
    </Layout>
  )
}

// 첫 설정 흐름 · 저축 확인
function OnboardingView() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const { data, error: loadError, reload } = useSavingsList()
  const [answers, setAnswers] = useState({})   // { savingId: 'yes' | 'no' }
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (!data) return <Layout title="저축 확인" showBack><StepBar step={4} /><ApiStatus error={loadError} /></Layout>
  const candidates = data.items.filter((s) => s.status === 'suggested')
  const confirmed = data.items.filter((s) => s.status === 'confirmed')
  // 고르는 동안은 화면에서 미리 더해 보여 주고, 저장된 값은 서버 합계 (savings.md 1. 화면)
  const savingsMin = data.summary.minimumMonthly + candidates.filter((c) => answers[c.savingId] === 'yes').reduce((sum, c) => sum + c.amount, 0)
  const pick = (id, value) => { setAnswers({ ...answers, [id]: value }); setError('') }

  // S-08: 저축이에요 → confirm, 아니에요 → ignore (각자 요청 번호). 하나라도 실패하면 머문다
  const onNext = async () => {
    if (busy) return
    if (candidates.some((c) => !answers[c.savingId])) { setError('찾은 이체마다 저축인지 골라 주세요.'); return }
    setBusy(true)
    setError('')
    try {
      for (const [answer, path] of [['yes', 'confirm'], ['no', 'ignore']]) {
        const ids = candidates.filter((c) => answers[c.savingId] === answer).map((c) => c.savingId)
        if (ids.length) await apiSend(`/api/budgets/savings/${path}/`, 'POST', { savingIds: ids }, { requestKey: newRequestKey() })
      }
      navigate(PATHS.loanCheck)  // 대출 확인 후 지킬 돈 화면에서 서버의 savingsMin 을 다시 읽는다
    } catch (err) {
      setError(err.fields?.savingIds?.[0] ?? err.message)
      reload()  // 먼저 성공한 쪽은 저장돼 있으니 목록을 새로 받는다
      setBusy(false)
    }
  }

  const foot = <button type="button" className="mc-primary" disabled={busy} onClick={onNext}>{busy ? '저장 중…' : candidates.length ? '확인하고 다음' : '계속하기'}</button>
  return (
    <Layout title="저축 확인" showBack foot={foot}>
      <StepBar step={4} />
      <div className="mc-eyebrow">저축 확인</div>
      <h1>{candidates.length ? '이 돈, 저축인가요?' : '매달 하는 저축이 있나요?'}</h1>
      {candidates.length > 0
        ? <p className="mc-muted">최근 3개월 동안 내 다른 계좌로 매달 같은 금액을 보낸 기록을 찾았어요. 저축이면 그 금액만큼은 꼭 저축하도록 예산을 짜요.</p>
        : <p className="mc-muted">매달 반복되는 내 계좌끼리 이체를 찾지 못했어요. 따로 하는 저축이 있으면 직접 추가하고, 없으면 계속하기를 눌러 주세요. 저축 금액은 지킬 돈 설정에서 정할 수 있어요.</p>}

      {candidates.map((c) => (
        <div className="mc-card" key={c.savingId}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{c.toAccount?.displayName ?? c.name}</strong><span className="mc-muted" style={{ display: 'block', marginTop: 4 }}>{savingLine(c)}</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(c.amount)}</strong>
          </div>
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">최근 3개월 {c.evidenceCount}회 감지</span>
            {/* 이름 · 금액 · 이체일만 고칠 수 있다 (S-11) */}
            <button type="button" className="mc-link" onClick={() => openPopup(<SavingPopup item={c} onSaved={reload} />)}>수정</button>
          </div>
          <div className="mc-seg mc-seg-sm" role="group" aria-label={`${c.toAccount?.displayName ?? c.name} 이체가 저축인가요?`}>
            {ANSWERS.map(([value, label]) => (
              <button key={value} type="button" aria-pressed={answers[c.savingId] === value} onClick={() => pick(c.savingId, value)}>{label}</button>
            ))}
          </div>
        </div>
      ))}
      {confirmed.map((s) => (
        <div className="mc-card" key={s.savingId}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{s.name}</strong><span className="mc-muted" style={{ display: 'block', marginTop: 4 }}>{savingLine(s)}</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(s.amount)}</strong>
          </div>
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">{s.sourceType === 'manual' ? '직접 추가' : '확인함'}</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<SavingPopup item={s} onSaved={reload} />)}>수정</button>
          </div>
        </div>
      ))}
      <button type="button" className="mc-secondary" onClick={() => openPopup(<SavingPopup onSaved={reload} />)}>+ 저축 직접 추가</button>

      <div aria-live="polite" className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>매달 꼭 저축할 돈</span><strong>{won(savingsMin)}</strong></div>
        <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>지킬 돈 설정에서 저축을 이 금액보다 적게 정할 수 없어요.</p>
      </div>
      {error && <p className="mc-error" role="alert">{error}</p>}
      {candidates.length > 0 && <p className="mc-note">'아니에요'를 고른 이체는 내 계좌끼리 옮긴 돈으로만 봐요. 소비로 세지 않아요. 계좌가 달라서 아니라면 [아니에요]를 누르고 직접 추가해 주세요.</p>}
    </Layout>
  )
}

export default function SavingsPage() {
  const location = useLocation()
  return location.state?.from === 'my' ? <ManageView /> : <OnboardingView />
}
