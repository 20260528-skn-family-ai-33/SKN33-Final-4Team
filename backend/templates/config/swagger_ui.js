"use strict";

const swaggerSettings = {{ settings|safe }};

const requestInterceptor = (request) => {
  const target = new URL(request.url, window.location.href);
  if (target.origin !== window.location.origin) {
    throw new Error("이 문서에서는 현재 서버와 같은 출처의 API만 호출할 수 있어요.");
  }
  // 저장 · 수정 · 삭제는 Django CSRF 규칙대로 csrftoken 쿠키 값을 X-CSRFToken 헤더에 자동으로 넣는다.
  // (실행 버튼은 '현재 서버 확인' 화면에만 있고, 그 화면에는 구현된 API만 저장 요청이 열려 있다.)
  if (request.method && request.method.toUpperCase() !== "GET") {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) {
      request.headers["X-CSRFToken"] = decodeURIComponent(match[1]);
    }
  }
  // 로그인 쿠키를 직접 입력하거나 브라우저 저장소에 보관하지 않는다.
  request.credentials = "same-origin";
  return request;
};

const ui = SwaggerUIBundle({
  ...swaggerSettings,
  url: "{{ schema_url|escapejs }}",
  dom_id: "#swagger-ui",
  presets: [SwaggerUIBundle.presets.apis],
  layout: "BaseLayout",
  requestInterceptor,
});
