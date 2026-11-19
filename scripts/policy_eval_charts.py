"""청년 정책 에이전트 평가 그래프: policy_agent_eval 의 summary json → 발표 · 평가서용 PNG 4장.

matplotlib 은 프로젝트 의존성이 아니다. 따로 만든 가상환경에서 돌린다:
    python -m venv chartenv && chartenv/Scripts/python -m pip install matplotlib
    chartenv/Scripts/python scripts/policy_eval_charts.py --out docs/agents/images \
        --modes 검색만=s-search.summary.json "고정 흐름=s-rules.summary.json" "LLM 에이전트=s-agent.summary.json" \
        --versions "PR 97 병합 11-12=v-ca824113.summary.json" … "현재 11-17=s-agent.summary.json"
"""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

# 기본 차트 팔레트 (dataviz 참조 팔레트, 밝은 화면). 색은 항목을 따라간다: 검색만 · 고정 흐름 · 에이전트 순서 고정
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
CRITERIA = [("fit", "추천 적합성", 55), ("eligibility", "자격·지역", 20), ("facts", "사실 정확성", 15),
            ("action", "요청 이해·행동", 10)]  # 가중치 큰 순서 (policies/judge.py 와 같게)
FIELD_ORDER = ["생활비", "주거", "저축", "창업", "직업훈련", "마음건강", "취업", "없음"]

plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "font.size": 11,
                     "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                     "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE})


def load(pairs):
    out = []
    for pair in pairs:
        name, path = pair.split("=", 1)
        out.append((name.replace("\\n", "\n"), json.loads(Path(path).read_text(encoding="utf-8"))))  # 이름 속 \n 은 줄바꿈
    return out


def style(ax, ylabel, top):
    ax.set_ylim(0, top)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)


def bars(ax, xs, values, color, width=0.6, offset=0.0, label=True, fmt="{:.1f}", pad=1.0):
    drawn = ax.bar([x + offset for x in xs], values, width=width, color=color, edgecolor=SURFACE, linewidth=2)
    if label:
        for bar, value in zip(drawn, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + pad, fmt.format(value), ha="center", va="bottom",
                    color=INK, fontsize=10)
    return drawn


def pct(value):
    return 0 if value is None else value * 100


def modes_chart(modes, out):
    """방식별: 왼쪽 LLM 채점 총점, 오른쪽 첫 화면 정답률 (한 축에 두 척도를 섞지 않는다)"""
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4.2))
    names = [n for n, _ in modes]
    xs = list(range(len(modes)))
    for i, (_, s) in enumerate(modes):
        bars(left, [i], [s["all"].get("score", 0)], SERIES[i])
        bars(right, [i], [pct(s["all"]["pass"])], SERIES[i])
    for ax, title, label in ((left, "LLM 채점 총점 (0~100)", "점수"), (right, "첫 화면 정답률 (%)", "%")):
        style(ax, label, 105)
        ax.set_xticks(xs, names)
        ax.set_title(title, color=INK, loc="left", fontsize=12)
    fig.suptitle("단계별 방식 비교 · 같은 시험지", color=INK, x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out / "policy_eval_modes.png", dpi=200)


def versions_chart(versions, out):
    """버전별 총점 · 정답률 추이 (PR 97 → 현재). 두 척도는 위아래 작은 그래프로 나눈다"""
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    names = [n for n, _ in versions]
    xs = list(range(len(versions)))
    for ax, values, title in ((top, [s["all"].get("score", 0) for _, s in versions], "LLM 채점 총점 (0~100)"),
                              (bottom, [pct(s["all"]["pass"]) for _, s in versions], "첫 화면 정답률 (%)")):
        ax.plot(xs, values, color=SERIES[0], linewidth=2, marker="o", markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2)
        for x, value in zip(xs, values):
            ax.text(x, value + 3, f"{value:.1f}", ha="center", va="bottom", color=INK, fontsize=10)
        style(ax, "", 105)
        ax.set_title(title, color=INK, loc="left", fontsize=12)
    bottom.set_xticks(xs, names)
    fig.suptitle("고치기 전과 후 · PR 97 → 현재", color=INK, x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out / "policy_eval_versions.png", dpi=200)


def fields_chart(modes, out):
    """분야별 시험지 점수 (방식별 묶은 막대)"""
    fields = [f for f in FIELD_ORDER if f in modes[-1][1]["fields"]]
    fig, ax = plt.subplots(figsize=(12, 4.6))
    width = 0.8 / len(modes)
    xs = list(range(len(fields)))
    for i, (name, s) in enumerate(modes):
        values = [s["fields"][f].get("score", 0) for f in fields]
        bars(ax, xs, values, SERIES[i], width=width, offset=(i - (len(modes) - 1) / 2) * width, label=i == len(modes) - 1,
             fmt="{:.0f}")
    style(ax, "LLM 채점 총점", 105)
    ax.set_xticks(xs, [f"{f}\n({modes[-1][1]['fields'][f]['questions']}개)" for f in fields])
    ax.legend(handles=[Patch(color=SERIES[i], label=name) for i, (name, _) in enumerate(modes)], frameon=False,
              ncol=len(modes), loc="upper left", bbox_to_anchor=(0, 1.13))
    fig.suptitle("분야별 시험지 점수", color=INK, x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out / "policy_eval_fields.png", dpi=200)


def criteria_chart(modes, out):
    """기준별 평균 (1~5점)"""
    fig, ax = plt.subplots(figsize=(11, 4.4))
    width = 0.8 / len(modes)
    xs = list(range(len(CRITERIA)))
    for i, (name, s) in enumerate(modes):
        values = [s["all"]["criteria"][key] for key, _, _ in CRITERIA]
        bars(ax, xs, values, SERIES[i], width=width, offset=(i - (len(modes) - 1) / 2) * width, label=i == len(modes) - 1,
             fmt="{:.2f}", pad=0.05)
    style(ax, "평균 점수 (1~5)", 5.6)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_xticks(xs, [f"{label}\n가중치 {w}" for _, label, w in CRITERIA])
    ax.legend(handles=[Patch(color=SERIES[i], label=name) for i, (name, _) in enumerate(modes)], frameon=False,
              ncol=len(modes), loc="upper left", bbox_to_anchor=(0, 1.13))
    fig.suptitle("기준별 점수", color=INK, x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out / "policy_eval_criteria.png", dpi=200)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", required=True, help="이름=summary.json (검색만 · 고정 흐름 · 에이전트 순서)")
    parser.add_argument("--versions", nargs="+", required=True, help="이름=summary.json (오래된 순서)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    modes, versions = load(args.modes), load(args.versions)
    modes_chart(modes, args.out)
    versions_chart(versions, args.out)
    fields_chart(modes, args.out)
    criteria_chart(modes, args.out)


if __name__ == "__main__":
    main()
