"""
화면만 있는 페이지용 도우미 (DB 연결 전)
- GET: 템플릿을 그대로 보여준다.
- POST(폼의 저장 버튼): 아무것도 저장하지 않고 다음 화면으로 넘어간다.
DB·기능을 붙일 때 각 화면을 진짜 view 함수로 바꾼다.
"""
from django.shortcuts import redirect, render


def screen(template, next_url=None):
    def view(request):
        if request.method == "POST" and next_url:
            return redirect(next_url)
        return render(request, template)
    return view


def popup(template):
    """팝업 조각(_이름.html)만 돌려준다. 버튼에 hx-get 으로 불러서 #mc-layer 에 띄운다."""
    def view(request, **kwargs):
        return render(request, template, kwargs)
    return view
