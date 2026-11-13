// 팝업: 기관 로그인 — 계좌 연결 2단계에서 기관을 누르면 열린다
// props: inst = '신한은행' 같은 기관 이름, onSubmit(form) = 입력이 다 되면 (팝업은 닫히고 화면이 연결을 진행)
//        recheck = 기관이 더 요구한 확인(utils/institutions.js RECHECK). 있으면 그 칸을 더 받는다
// 비밀번호는 이 팝업 안에서만 들고 있다가 넘기고 끝. 저장·출력하지 않는다 (서버가 RSA 로 암호화해 CODEF 에 한 번 보내고 버림)
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { EXTRA, FIELD, instInfo } from '../utils/institutions.js'
import CardNoInput from '../components/CardNoInput.jsx'
import { josa } from '../utils/adjust.js'

export default function ConnectLoginPopup({ inst, onSubmit, recheck }) {
  const { closePopup } = usePopup()
  const extra = EXTRA[inst] ?? {}
  const info = instInfo(inst)  // 잠김 횟수
  const extraKeys = [...new Set([...(extra.fields ?? []), ...(recheck?.fields ?? [])])]
  const keys = ['id', 'password', ...extraKeys]
  const [form, setForm] = useState(() => Object.fromEntries(keys.map((k) => [k, ''])))
  const [error, setError] = useState('')
  const set = (k, v) => { setForm((f) => ({ ...f, [k]: v })); setError('') }

  const submit = (e) => {
    e.preventDefault()
    const empty = keys.find((k) => !form[k].trim())
    if (empty) {
      const label = empty === 'id' ? '아이디' : empty === 'password' ? '비밀번호' : FIELD[empty].label
      setError(`${label}${josa(label)} 입력해 주세요.`)
      return
    }
    onSubmit(form)
    closePopup()
  }

  return (
    <Popup label={`${inst} 로그인`}>
      <h2>{inst} 로그인</h2>
      <p className="mc-muted" style={{ margin: '4px 0 12px' }}>{inst} 홈페이지에서 쓰는 로그인 정보를 넣어 주세요.</p>
      <form className="mc-form mc-login-form" onSubmit={submit}>
        {recheck ? <p className="mc-login-note is-warn" role="note">{recheck.message}</p> : extra.note && <p className="mc-login-note">{extra.note}</p>}
        <label className="mc-field">아이디
          <input autoComplete="off" name="instId" value={form.id} onChange={(e) => set('id', e.target.value)} />
        </label>
        <label className="mc-field">비밀번호
          <input autoComplete="off" name="instPw" type="password" value={form.password} onChange={(e) => set('password', e.target.value)} />
        </label>
        {extraKeys.map((k) => {
          const f = FIELD[k]
          return (
            <label className="mc-field" key={k}>{f.label}
              {k === 'cardNo' ? <CardNoInput value={form[k]} onChange={(v) => set(k, v)} /> : (
                <input autoComplete="off" inputMode={f.inputMode} maxLength={f.maxLength} placeholder={f.placeholder} type={f.type ?? 'text'}
                  value={form[k]} onChange={(e) => set(k, f.inputMode === 'numeric' ? e.target.value.replace(/\D/g, '') : e.target.value)} />
              )}
            </label>
          )
        })}
        {info.lock && <p className="mc-login-warn" role="note">비밀번호를 {info.lock}번 틀리면 {inst} 로그인이 잠겨요.</p>}
        {extra.warn && <p className="mc-login-warn" role="note">{extra.warn}</p>}
        {error && <p className="mc-error" role="alert">{error}</p>}
        <button type="submit" className="mc-primary">연결하기</button>
        <p className="mc-note" style={{ paddingBottom: 0 }}>비밀번호는 암호화해서 {inst}에 한 번 보내고 저장하지 않아요. 송금·결제는 하지 않아요.</p>
      </form>
    </Popup>
  )
}
