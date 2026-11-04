"""환경에 관계없이 읽히도록 그래프에는 영문, 본문에는 한글을 사용한다."""
import numpy as np
import matplotlib.pyplot as plt
from .constants import AMOUNTS, LABELS

COLORS = {'A': '#2563eb', 'B': '#0f766e', 'C': '#c2410c'}


def style():
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.titleweight': 'bold', 'figure.facecolor': '#ffffff'})


def plot_preprocessing(data):
    style()
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5), layout='constrained')
    years = sorted(data.year.unique())
    n = [len(data[data.year == y]) for y in years]
    ax[0].bar([str(y) for y in years], n, color=['#64748b' if y <= 2024 else '#d97706' for y in years])
    ax[0].set(title='Rows by year: train candidates / evaluation', ylabel='Household-quarter observations')
    for i, v in enumerate(n):
        ax[0].text(i, v + 15, str(v), ha='center', fontsize=9)
    counts = [data.train_A.sum(), data.train_B.sum(), data.train_C.sum(), data.evaluation.sum()]
    ax[1].bar(['A train', 'B train', 'C train', 'Evaluation'], counts,
              color=[COLORS['A'], COLORS['B'], COLORS['C'], '#d97706'])
    ax[1].set(title='Same evaluation rows for all models', ylabel='Rows')
    for i, v in enumerate(counts):
        ax[1].text(i, v + 40, str(v), ha='center')
    return fig


def _comparison(data, pred, model_key, synthetic):
    style()
    fig, ax = plt.subplots(2, 2, figsize=(13, 9), layout='constrained')
    actual = data[list(AMOUNTS)].to_numpy(float)
    total = actual.sum(axis=1)
    predicted = pred['amounts'].sum(axis=1)
    weights = data.survey_weight.to_numpy(float)
    color = COLORS[model_key]
    label = 'Generated reference' if synthetic else 'Observed target'
    maxval = max(total.max(), predicted.max()) / 10000
    ax[0, 0].scatter(total / 10000, predicted / 10000, s=9, alpha=.3, color=color)
    ax[0, 0].plot([0, maxval], [0, maxval], '--', color='#475569', lw=1)
    ax[0, 0].set(xlabel=label + ' total (10,000 KRW)', ylabel='Predicted sum (10,000 KRW)',
                 title='Total comparison' + (' — total supplied as input' if model_key != 'C' else ' — total not supplied'))
    mae = np.average(abs(pred['amounts'] - actual), weights=weights, axis=0)
    order = np.argsort(mae)
    ax[0, 1].barh(np.array(LABELS)[order], mae[order] / 10000, color=color)
    ax[0, 1].set(xlabel='Category MAE (10,000 KRW)', title='Where allocation differs')
    truthmean = np.average(actual, weights=weights, axis=0) / 10000
    predmean = np.average(pred['amounts'], weights=weights, axis=0) / 10000
    idx = np.arange(11)
    ax[1, 0].bar(idx - .18, truthmean, .36, label=label, color='#94a3b8')
    ax[1, 0].bar(idx + .18, predmean, .36, label='Prediction', color=color)
    ax[1, 0].set(xticks=idx, xticklabels=LABELS, ylabel='Mean amount (10,000 KRW)', title='Category means')
    ax[1, 0].tick_params(axis='x', labelrotation=55)
    ax[1, 0].legend()
    rate = abs(pred['amounts'] - actual).sum(axis=1) / total * 100
    ax[1, 1].hist(rate, bins=30, weights=weights / weights.sum(), color=color, alpha=.8)
    ax[1, 1].set(xlabel='Sum of category absolute errors / reference total (%)',
                 ylabel='Fraction of weight', title='Allocation error distribution')
    fig.suptitle('Model ' + model_key + (' | SYNTHETIC SCENARIO COMPARISON — NOT REAL ACCURACY'
                                      if synthetic else ' | OBSERVED DATA RE-EVALUATION'), fontsize=13, fontweight='bold')
    return fig


def plot_evaluation(data, pred, model_key):
    return _comparison(data, pred, model_key, False)


def plot_synthetic(data, pred, model_key):
    return _comparison(data, pred, model_key, True)


def plot_model_comparison(metrics):
    style()
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5), layout='constrained')
    for ax, field, title, scale in zip(axes,
        ['ratio_mae_pp', 'category_macro_mae_won', 'total_mae_won'],
        ['Share MAE (percentage points)', 'Category MAE (10,000 KRW)', 'Total MAE (10,000 KRW)'],
        [1, 10000, 10000]):
        values = [metrics[k]['weighted'][field] / scale for k in 'ABC']
        ax.bar(list('ABC'), values, color=[COLORS[k] for k in 'ABC'])
        for i, value in enumerate(values):
            ax.text(i, value, f'{value:.2f}', ha='center', va='bottom')
        ax.set(title=title, ylim=(0, max(values) * 1.25))
    axes[2].text(.02, .9, 'A/B: actual total is given\nZero is a calculation identity', transform=axes[2].transAxes,
                 fontsize=9, va='top')
    fig.suptitle('Observed evaluation: 2,635 identical rows', fontsize=14, fontweight='bold')
    return fig
