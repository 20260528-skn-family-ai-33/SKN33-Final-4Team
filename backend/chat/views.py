from django.shortcuts import render


def assistant(request):
    """통합 챗봇 (하단 탭 '챗봇'). 지금은 첫 화면(선택지 3개)만"""
    return render(request, "chat/assistant.html")
