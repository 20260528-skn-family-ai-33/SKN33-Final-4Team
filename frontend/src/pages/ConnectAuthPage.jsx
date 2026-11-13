// 계좌 연결 2: 기관 로그인 · 연결 상태
// 앞 화면에서 고른 기관마다 로그인 → (필요하면 문자 인증) → 연결 완료 / 실패 · 다시 시도
// POST /api/finance/connections/ (docs/api/finance-connect.md '금융기관 인증 · 새 연결', 11/03 S2)
//   - 기관 하나씩 보낸다. 은행은 연결 직후 계좌 · 거래를, 카드사는 보유카드를 서버가 불러온다
//   - 11/09: 카드 청구서 · 승인내역은 서버가 뒤에서 불러온다(syncStatus 'loading'). 기다리지 않고 다음 단계로 가도 되고,
//     지킬 돈 화면이 끝날 때까지 '불러오는 중'을 보여 준다 (업종 조회로 1분 넘게 걸려 온보딩이 멈춰 보였다)
//   - 연결(인증) 성공과 불러오기 성공은 다르다: syncStatus 'failed' 면 연결은 됐지만 불러오기 실패로 보여 준다
//   - 문자 인증: 같은 기관 값 + smsAuth { authToken, code } 를 한 번 더 보낸다. 로그인 값은 팝업이 닫힐 때까지 메모리에만
import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import InstRow from '../components/InstRow.jsx'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import ConnectLoginPopup from '../popups/ConnectLoginPopup.jsx'
import SmsAuthPopup from '../popups/SmsAuthPopup.jsx'
import { apiSend } from '../utils/api.js'
import { BANKS, CARDS, CARD_RECHECK, SERVER_FIELDS } from '../utils/institutions.js'

// 상태별 작은 글씨
const SUB = {
  todo: ['로그인이 필요해요', ''],
  busy: ['연결하는 중…', ''],
  sms: ['문자 인증이 필요해요', 'warn-t'],
  ok: ['연결 완료 · 내역을 불러왔어요', 'ok'],
  loading: ['연결 완료 · 카드 내역은 뒤에서 불러오고 있어요. 다음 단계로 넘어가도 돼요.', 'ok'],
  okNoSync: ['연결은 됐지만 내역을 불러오지 못했어요. 연결 결과 화면에서 다시 불러오기를 눌러 주세요.', 'warn-t'],
}

// 로그인 팝업 입력 → 서버가 받는 기관 한 줄
function entry(inst, form) {
  const row = { businessType: inst.kind, organizationCode: inst.code, loginId: form.id.trim(), password: form.password }
  for (const [k, v] of Object.entries(form)) if (SERVER_FIELDS[k] && v) row[SERVER_FIELDS[k]] = v
  return row
}

export default function ConnectAuthPage() {
  const { state } = useLocation()
  // 앞 화면(동의 · 기관 고르기)을 거치지 않고 들어오면 처음으로
  if (!state?.banks) return <Navigate to={PATHS.connect} replace />
  return <AuthList picked={state} />
}

function AuthList({ picked }) {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const codeOf = Object.fromEntries([...BANKS, ...CARDS].map((x) => [x.name, x.code]))
  // [{ name, kind: 'bank' | 'card', code, status: todo·busy·sms·recheck·ok·loading·okNoSync·fail, error?, recheck? }]
  const [insts, setInsts] = useState(() => [
    ...picked.banks.map((name) => ({ name, kind: 'bank', code: codeOf[name], status: 'todo' })),
    ...picked.cards.map((name) => ({ name, kind: 'card', code: codeOf[name], status: 'todo' })),
  ])
  const [pageError, setPageError] = useState('')

  const update = (name, patch) => setInsts((list) => list.map((i) => (i.name === name ? { ...i, ...patch } : i)))

  // 서버 결과 한 기관 → 화면 상태. row = 보낸 값(문자 인증 때 다시 보내려고 메모리에만 둔다)
  const apply = (inst, row, result) => {
    if (result.status === 'connected') {
      update(inst.name, { status: { failed: 'okNoSync', loading: 'loading' }[result.syncStatus] ?? 'ok', error: null })
    } else if (result.status === 'sms_required') {
      update(inst.name, { status: 'sms', error: result.error?.message ?? null })
      askSms(inst, row, result.smsAuth, result.error?.message)
    } else {
      fail(inst, result.error ?? {})
    }
  }
  // 카드 값을 더 요구하면 그 칸을 붙여 로그인 팝업을 다시 연다. 아니면 실패 문구
  const fail = (inst, error) => {
    const keys = Object.keys(error.fields ?? {}).join(' ')
    if (/cardNo|cardPassword/.test(keys)) {
      update(inst.name, { status: 'recheck', recheck: CARD_RECHECK, error: null })
      login(inst, CARD_RECHECK)
    } else {
      update(inst.name, { status: 'fail', error: error.message ?? '연결하지 못했어요.' })
    }
  }

  const send = async (inst, row) => {
    update(inst.name, { status: 'busy', error: null })
    setPageError('')
    try {
      const res = await apiSend('/api/finance/connections/', 'POST', { institutions: [row] })
      apply(inst, row, res.results[0])
    } catch (err) {
      // 기관 하나만 보냈으므로 4xx · 502 의 문구가 곧 그 기관의 안내 (fields 키는 institutions[0].cardNo 꼴)
      fail(inst, { message: err.message, fields: err.fields })
    }
  }

  // 문자 인증 팝업 → 인증번호 확인
  const askSms = (inst, row, smsAuth, notice) => openPopup(
    <SmsAuthPopup inst={inst.name} phoneHint={smsAuth?.phoneHint} notice={notice}
      onCancel={() => update(inst.name, { status: 'fail', error: '문자 인증을 취소했어요.' })}
      onSubmit={(code) => send(inst, { ...row, smsAuth: { authToken: smsAuth.authToken, code } })} />,
  )

  // 로그인 팝업 → 연결 시도
  const login = (inst, recheck) => openPopup(
    <ConnectLoginPopup inst={inst.name} recheck={recheck} onSubmit={(form) => send(inst, entry(inst, form))} />,
  )

  const connected = (i) => ['ok', 'loading', 'okNoSync'].includes(i.status)
  const okBanks = insts.filter((i) => i.kind === 'bank' && connected(i)).length
  const okCards = insts.filter((i) => i.kind === 'card' && connected(i)).length
  const next = () => {
    if (okBanks === 0) { setPageError('은행을 1곳 이상 연결해야 계좌를 불러올 수 있어요.'); return }
    navigate(PATHS.connectAccounts, { state: { cards: okCards > 0 } })
  }

  const right = (i) => {
    if (connected(i)) return <Icon name="CircleCheck" />
    if (i.status === 'busy') return <span className="mc-conn-busy"><Icon name="Loader" /></span>
    const label = { todo: '로그인', sms: '다시 로그인', recheck: i.recheck?.label, fail: '다시 시도' }[i.status]
    const go = i.status === 'recheck' ? () => login(i, i.recheck) : () => login(i)
    return <button type="button" className={`mc-conn-btn${i.status === 'fail' ? ' is-retry' : ''}`} onClick={go}>{label}</button>
  }
  const sub = (i) => {
    if (i.status === 'fail') return [`연결 실패 · ${i.error}`, 'mc-conn-fail']
    if (i.status === 'sms' && i.error) return [i.error, 'mc-conn-fail']
    if (i.status === 'recheck') return [i.recheck?.sub, 'warn-t']
    return SUB[i.status]
  }

  const group = (kind, title) => {
    const list = insts.filter((i) => i.kind === kind)
    if (list.length === 0) return null
    return (
      <div className="mc-card">
        <h2>{title}</h2>
        {list.map((i) => {
          const [text, cls] = sub(i)
          return <InstRow key={i.name} icon={kind === 'card' ? 'CreditCard' : 'Landmark'} name={i.name} sub={text} subClass={cls} right={right(i)} />
        })}
      </div>
    )
  }

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" onClick={next}>보유 계좌 보기 · 은행 {okBanks}곳</button>}
    >
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>기관마다 로그인해 주세요</h1>
      <p className="mc-muted">로그인을 마친 기관부터 내 계좌·카드를 불러와요.</p>
      {group('bank', '은행')}
      {group('card', '카드사')}
      <p className="mc-note">연결에 실패한 기관이 있어도 성공한 기관의 계좌는 그대로 쓸 수 있어요. 카드사는 나중에 연결해도 돼요.</p>
      {pageError && <div className="mc-error" role="alert">{pageError}</div>}
    </Layout>
  )
}
