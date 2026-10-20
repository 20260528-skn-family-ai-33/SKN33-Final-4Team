// 팝업: 약관 상세 — 회원가입 약관 항목을 누르면 열린다
// props: kind = 'terms' | 'privacy' (termsText.js 의 키), onAgree = "동의하고 닫기"를 눌렀을 때 할 일
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { TERMS } from './termsText.js'

export default function TermsPopup({ kind, onAgree }) {
  const { closePopup } = usePopup()
  const doc = TERMS[kind]

  const agree = () => {
    onAgree?.()  // 회원가입 화면의 체크박스를 켠다
    closePopup()
  }

  return (
    <Popup label={doc.title}>
      <h2>{doc.title}</h2>
      <p className="mc-muted">필수 · {doc.version} · {doc.date} 시행</p>
      <div className="mc-terms-doc">
        {doc.sections.map((s) => (
          <section key={s.h}>
            <h3>{s.h}</h3>
            {s.list
              ? <ul>{s.items.map((t) => <li key={t}>{t}</li>)}</ul>
              : s.items.map((t) => <p key={t}>{t}</p>)}
          </section>
        ))}
      </div>
      <p className="mc-note">시안 문구예요. 출시 전에 확정해요.</p>
      <div className="mc-terms-foot">
        <button type="button" className="mc-primary" onClick={agree}>동의하고 닫기</button>
      </div>
    </Popup>
  )
}
