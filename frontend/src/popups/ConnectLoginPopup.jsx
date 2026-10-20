// 팝업: 기관 로그인 — 계좌 연결 2단계에서 기관을 누르면 열린다
// props: inst = '신한은행' 같은 기관 이름, onSubmit(form) = 입력이 다 되면 (팝업은 닫히고 화면이 연결을 진행)
// 비밀번호는 이 팝업 안에서만 들고 있다가 넘기고 끝. 저장·출력하지 않는다 (진짜 연동 때는 서버에서 RSA 암호화)
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { EXTRA, FIELD, instInfo } from '../utils/institutions.js'
import { josa } from '../utils/adjust.js'

const METHODS = [['id', '아이디 로그인'], ['cert', '공동인증서']]

export default function ConnectLoginPopup({ inst, onSubmit }) {
  const { closePopup } = usePopup()
  const extra = EXTRA[inst] ?? {}
  const info = instInfo(inst)  // 아이디 로그인 지원 여부 · 잠김 횟수
  const keys = ['id', 'password', ...(extra.fields ?? [])]
  const [method, setMethod] = useState(info.idLogin ? 'id' : 'cert')
  const [form, setForm] = useState(() => Object.fromEntries(keys.map((k) => [k, ''])))
  const [error, setError] = useState('')
  const set = (k, v) => { setForm((f) => ({ ...f, [k]: v })); setError('') }

  const submit = (e) => {
    e.preventDefault()
    const empty = keys.find((k) => !form[k].trim())
    if (empty) {
      const label = empty === 'id' ? '아이디' : empty === 'password' ? '비밀번호' : FIELD[empty].label
      setError(`${label}${josa(label)} ${FIELD[empty]?.options ? '골라' : '입력해'} 주세요.`)
      return
    }
    onSubmit(form)
    closePopup()
  }

  return (
    <Popup label={`${inst} 로그인`}>
      <h2>{inst} 로그인</h2>
      <p className="mc-muted" style={{ margin: '4px 0 12px' }}>{inst} 홈페이지에서 쓰는 로그인 정보를 넣어 주세요.</p>
      <div className="mc-tabsw" role="tablist" aria-label="로그인 방법">
        {METHODS.map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={method === k} onClick={() => setMethod(k)}>{label}</button>
        ))}
      </div>

      {method === 'cert' && (
        <p className="mc-note mc-login-cert">
          {info.idLogin
            ? '공동인증서 로그인은 지원 방식을 정한 뒤 열어요. 지금은 아이디 로그인을 써 주세요.'
            : `${inst}${josa(inst, '은', '는')} 공동인증서로만 연결돼요. 공동인증서 로그인은 지원 방식을 정한 뒤 열어요.`}
        </p>
      )}
      {method === 'id' && !info.idLogin && (
        <p className="mc-note mc-login-cert">{inst}{josa(inst, '은', '는')} 아이디 로그인을 지원하지 않아요. 공동인증서로만 연결돼요.</p>
      )}
      {method === 'id' && info.idLogin && (
        <form className="mc-form mc-login-form" onSubmit={submit}>
          {extra.note && <p className="mc-login-note">{extra.note}</p>}
          <label className="mc-field">아이디
            <input autoComplete="off" name="instId" value={form.id} onChange={(e) => set('id', e.target.value)} />
          </label>
          <label className="mc-field">비밀번호
            <input autoComplete="off" name="instPw" type="password" value={form.password} onChange={(e) => set('password', e.target.value)} />
          </label>
          {(extra.fields ?? []).map((k) => {
            const f = FIELD[k]
            return (
              <label className="mc-field" key={k}>{f.label}
                {f.options ? (
                  <select value={form[k]} onChange={(e) => set(k, e.target.value)}>
                    <option value="">선택</option>
                    {f.options.map((o) => <option key={o}>{o}</option>)}
                  </select>
                ) : (
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
      )}
    </Popup>
  )
}
