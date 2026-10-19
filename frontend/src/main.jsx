import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
// 디자인: 장고 backend/static/css 에서 그대로 복사해 온 파일
import './styles/app.css'
import './styles/shell.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {/* BrowserRouter: 주소창의 주소를 보고 어떤 화면을 보여줄지 정하는 장치 */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
