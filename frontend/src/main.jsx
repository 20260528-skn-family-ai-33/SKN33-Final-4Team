import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
// 디자인: MVP 프로토타입에서 옮겨 온 CSS (app.css = 원본, shell.css = 모바일 웹 보정)
import './styles/app.css'
import './styles/shell.css'
import App from './App.jsx'
import PopupProvider from './popups/PopupProvider.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {/* BrowserRouter: 주소창의 주소를 보고 어떤 화면을 보여줄지 정하는 장치 */}
    <BrowserRouter>
      {/* PopupProvider: 어느 화면에서든 팝업을 열 수 있게 해 주는 방송실 */}
      <PopupProvider>
        <App />
      </PopupProvider>
    </BrowserRouter>
  </StrictMode>,
)
