// 머니쿠션 공통 동작: 아이콘 그리기 + 팝업 열고 닫기
// 팝업은 HTMX가 서버에서 조각(_이름.html)을 받아 #mc-layer 안에 넣는다.
(function () {
  const layer = () => document.getElementById("mc-layer");
  const blocks = () => ["mc-main", "mc-header", "mc-nav"].map((id) => document.getElementById(id)).filter(Boolean);

  function drawIcons() {
    window.lucide?.createIcons({ attrs: { width: 20, height: 20 } });
  }

  function closePopup() {
    const el = layer();
    if (!el || !el.innerHTML.trim()) return;
    el.innerHTML = "";
    blocks().forEach((b) => (b.inert = false));
  }

  // 팝업이 들어오면: 뒤 화면 잠그고, 아이콘 그리고, 첫 버튼에 포커스
  document.addEventListener("htmx:afterSwap", (e) => {
    drawIcons();
    if (e.detail.target.id === "mc-layer") {
      blocks().forEach((b) => (b.inert = true));
      layer().querySelector(".mc-sheet button, .mc-sheet a, .mc-sheet input")?.focus();
    }
  });

  // data-href="주소" 가 달린 버튼은 누르면 그 화면으로 이동 (프로토타입 버튼 모양을 그대로 쓰려고)
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-href]");
    if (el) window.location.href = el.dataset.href;
  });

  // 선택 버튼 (화면에서만 바뀌고 저장은 안 됨)
  // data-choice="그룹" : 같은 그룹 중 하나만 켜기 / data-toggle : 누를 때마다 켜고 끄기
  document.addEventListener("click", (e) => {
    const one = e.target.closest("[data-choice]");
    if (one) {
      const group = one.dataset.choice;
      one.parentElement.querySelectorAll(`[data-choice="${group}"]`).forEach((b) => b.setAttribute("aria-pressed", String(b === one)));
      return;
    }
    const t = e.target.closest("[data-toggle]");
    if (t) {
      const on = t.getAttribute("aria-pressed") !== "true";
      t.setAttribute("aria-pressed", String(on));
      const pick = t.closest(".mc-acctpick");   // 계좌·카드 고르기 카드 모양
      if (pick) {
        pick.classList.toggle("on", on);
        const box = pick.querySelector(".mc-acctpick-box");
        if (box) box.textContent = on ? "✓" : "";
      }
    }
  });

  // 배경이나 [data-close] 버튼을 누르면 닫기
  document.addEventListener("click", (e) => {
    if (e.target.closest(".mc-backdrop, [data-close]")) closePopup();
  });
  // 팝업 안의 저장 버튼: 지금은 저장하지 않고 닫기만 한다 (DB 연결 후 바꾼다)
  document.addEventListener("submit", (e) => {
    if (e.target.closest("#mc-layer")) {
      e.preventDefault();
      closePopup();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePopup();
  });

  document.addEventListener("DOMContentLoaded", drawIcons);
  window.mcClosePopup = closePopup;
})();
