// 계좌 연결 2: 기관 로그인 · 연결 상태
// 앞 화면에서 고른 기관마다 로그인 → (필요하면 문자 인증) → 연결 완료 / 실패 · 다시 시도
// 지금은 utils/mockConnect.js 로 흐름만 보여 준다. 진짜 연동 때는 CODEF 계정 등록(/v1/account/create · add) API 로 바꾼다
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import InstRow from '../components/InstRow.jsx'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import ConnectLoginPopup from '../popups/ConnectLoginPopup.jsx'
import SmsAuthPopup from '../popups/SmsAuthPopup.jsx'
import { mockConnect, mockSms } from '../utils/mockConnect.js'
import { RECHECK } from '../utils/institutions.js'

// 앞 화면 없이 바로 들어왔을 때 보여 줄 기본값
const DEFAULT = { banks: ['신한은행', '국민은행'], cards: ['신한카드'] }

// 상태별 작은 글씨
const SUB = {
  todo: ['로그인이 필요해요', ''],
  busy: ['연결하는 중…', ''],
  sms: ['문자 인증이 필요해요', 'warn-t'],
  ok: ['연결 완료', 'ok'],
}

export default function ConnectAuthPage() {
  const navigate = useNavigate()
  const { state } = useLocation()
  const { openPopup } = usePopup()
  const picked = state?.banks ? state : DEFAULT
  // [{ name, kind: 'bank' | 'card', status: todo·busy·sms·recheck·ok·fail, error?, phoneHint?, recheck? }]
  const [insts, setInsts] = useState(() => [
    ...picked.banks.map((name) => ({ name, kind: 'bank', status: 'todo' })),
    ...picked.cards.map((name) => ({ name, kind: 'card', status: 'todo' })),
  ])
  const [pageError, setPageError] = useState('')

  const update = (name, patch) => setInsts((list) => list.map((i) => (i.name === name ? { ...i, ...patch } : i)))

  // 문자 인증 팝업 → 인증번호 확인
  const askSms = (name, phoneHint) => openPopup(
    <SmsAuthPopup inst={name} phoneHint={phoneHint}
      onCancel={() => update(name, { status: 'fail', error: '문자 인증을 취소했어요.' })}
      onSubmit={async (code) => {
        update(name, { status: 'busy' })
        const r = await mockSms(code)
        update(name, r.status === 'ok' ? { status: 'ok', error: null } : { status: 'sms', error: r.message, phoneHint })
      }} />,
  )

  // 로그인 팝업 → 연결 시도
  const login = (name, recheck) => openPopup(
    <ConnectLoginPopup inst={name} recheck={recheck} onSubmit={async (form) => {
      update(name, { status: 'busy', error: null })
      setPageError('')
      const r = await mockConnect(name, form)
      if (r.status === 'sms') {
        update(name, { status: 'sms', phoneHint: r.phoneHint })
        askSms(name, r.phoneHint)
      } else if (r.status === 'recheck') {
        // 기관이 더 확인을 요구 → 필요한 칸을 붙여서 로그인 팝업을 다시 연다
        update(name, { status: 'recheck', recheck: RECHECK[r.code] })
        login(name, RECHECK[r.code])
      } else {
        update(name, r.status === 'ok' ? { status: 'ok' } : { status: 'fail', error: r.message })
      }
    }} />,
  )

  const okBanks = insts.filter((i) => i.kind === 'bank' && i.status === 'ok').length
  const next = () => {
    if (okBanks === 0) { setPageError('은행을 1곳 이상 연결해야 계좌를 불러올 수 있어요.'); return }
    navigate(PATHS.connectAccounts)
  }

  const right = (i) => {
    if (i.status === 'ok') return <Icon name="CircleCheck" />
    if (i.status === 'busy') return <span className="mc-conn-busy"><Icon name="Loader" /></span>
    const label = { todo: '로그인', sms: '인증번호 입력', recheck: i.recheck?.label, fail: '다시 시도' }[i.status]
    const go = i.status === 'sms' ? () => askSms(i.name, i.phoneHint) : i.status === 'recheck' ? () => login(i.name, i.recheck) : () => login(i.name)
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
      foot={<button type="button" className="mc-primary" onClick={next}>보유 계좌 불러오기 · 은행 {okBanks}곳</button>}
    >
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>기관마다 로그인해 주세요</h1>
      <p className="mc-muted">로그인을 마친 기관부터 내 계좌·카드를 불러와요.</p>
      {group('bank', '은행')}
      {group('card', '카드사')}
      <p className="mc-note">연결에 실패한 기관이 있어도 성공한 기관의 계좌는 그대로 쓸 수 있어요. 카드사는 나중에 연결해도 돼요.</p>
      <p className="mc-note mc-conn-demo">시안 안내: 실제 정보는 넣지 마세요. 비밀번호에 <b>wrong</b>을 넣으면 실패 화면, 롯데카드·씨티는 문자 인증, 국민카드는 카드 소지 확인 화면을 볼 수 있어요.</p>
      {pageError && <div className="mc-error" role="alert">{pageError}</div>}
    </Layout>
  )
}
