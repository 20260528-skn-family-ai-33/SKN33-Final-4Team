"""final_audit 실행 원본으로 사람이 읽는 보고서를 만든다. 재채점·LLM 호출 없음."""
import argparse
import difflib
import hashlib
import html
import json
from pathlib import Path
import shutil

E = html.escape

def read(root, name): return json.loads((root / name).read_text())
def flat(rows): return [t for r in rows for t in r.get('turns', [r])]
def pretty(value): return E(json.dumps(value, ensure_ascii=False, indent=2))
def badge(ok): return '<span class="ok">통과</span>' if ok else '<span class="bad">기준 미충족</span>'
def doc(title, body):
    return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+E(title)+'''</title><style>
body{font:16px/1.7 system-ui,sans-serif;max-width:1080px;margin:35px auto;padding:0 20px;background:#f5f7fa;color:#172b3a}h1{font-size:28px}h2{font-size:21px}h3{font-size:18px}section{background:white;border:1px solid #d9e1ea;border-radius:12px;padding:22px;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid #dde4ec;padding:9px;text-align:left;vertical-align:top}.ok{color:#146441}.bad{color:#9b3225}a{color:#1e56a0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}blockquote{margin:14px 0;padding:16px 20px;background:#eef3fa;border-left:4px solid #5285bb}details{margin-top:14px}summary{cursor:pointer}.muted{color:#526373}.note{padding:15px;border:1px solid #d6dfe9;background:#edf2f8}code{overflow-wrap:anywhere}</style>'''+body+'</html>'

def field_failures(r):
    out=[]
    for role in ('router', 'spending'):
        for k,v in r.get(role,{}).get('fields',{}).items():
            if not v['ok']:out.append({'role':role,'field':k,'expected':v['want'],'actual':v['got']})
    for kind in ('decision','state'):
        for k,v in r.get(kind,{}).items():
            if not v['ok']:out.append({'role':kind,'field':k,'expected':v['want'],'actual':v['got']})
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output-dir',required=True);args=p.parse_args()
    root,out=Path(args.root).resolve(),Path(args.output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    tags=['roles-initial','roles-revision1','checklist-initial','checklist-revision1','reading-initial','b-initial']
    data={tag:read(root,tag+'-results.json') for tag in tags}
    for tag in tags:
        shutil.copy2(root/(tag+'-results.json'),out/(tag+'-results.json'))
        if (root/(tag+'.html')).exists(): shutil.copy2(root/(tag+'.html'),out/(tag+'.html'))
    shutil.copytree(root/'verify-service',out/'verify-service',dirs_exist_ok=True)
    for name in ['backend-full.log','backend-affected-revision1.log','eval-tests.log','eval-tests-revision1.log','frontend-lint.log','frontend-build.log','frontend-fixture-build.log','ledger.json','skipped-tests.json','case-coverage.json','correction-plan.json']:
        shutil.copy2(root/name,out/name)
    for rev in ('frozen','revision1'):
        target=out/rev;target.mkdir(exist_ok=True)
        for name in ['manifest.json','contracts.json','checklist.json','reading_cases.json','service_cases.json','effective_role_cases.json']:
            shutil.copy2(root/rev/name,target/name)
    roles0=flat(data['roles-initial']['rows']);roles1=flat(data['roles-revision1']['rows'])
    latest={r['id']:{**r,'revision':'R0'} for r in roles0};latest.update({r['id']:{**r,'revision':'R1'} for r in roles1})
    unresolved=[r for r in latest.values() if r['kind']!='ok' and not r.get('policy_pending')]
    b=data['b-initial']['rows'];reading=data['reading-initial']['rows']
    artifacts={'role_unresolved':[{**r,'differences':field_failures(r)} for r in unresolved],
               'reading_failures':[r for r in reading if not r['pass']], 'b_failures':[r for r in b if not r['pass']]}
    (out/'remaining-failures.json').write_text(json.dumps(artifacts,ensure_ascii=False,indent=2))
    human_notes={
        'e01':'자동 기준은 scope 축의 직접 인용을 요구합니다. 실제 답에는 visit 절 안에 전체 증가액이 포함되어 있습니다. 기준 실패와 숫자 오류를 구분해서 읽어 주세요.',
        'e08':'평균 금액을 묻는 기대와 달리 시간대 증가를 답했습니다. 입력 가설이 빠지고 계획에서도 질문의 평균 지표를 충족하지 못했습니다.',
        'e10':'확인 불가 안내 자체는 적절하지만, 고정 기대 지표 amount 대신 count를 선택해 의미 기준은 실패했습니다.',
        'e12':'자동 기준은 scope 축의 직접 인용을 요구합니다. 실제 답에는 visit 절의 전체 증가액이 들어 있습니다. 기대값은 수정하지 않았습니다.',
        'e13':'고정 자동 기준은 통과했습니다. 다만 실제 약속 횟수를 확인한 답은 아니며 저녁 지출 금액을 비교한 답입니다. 질문 의도에 충분한지는 사람이 판단해야 합니다.',
    }
    body='<h1>B 켠 실제 답변 · 15개 원문</h1><p><a href="report.html">종합 결과</a> · <a href="b-initial-results.json">서비스 응답 JSON</a></p>'
    body+='<p class="note">수정 전 고정 지시문 R0에서 실제 서비스 API를 통해 선행 비교 → 후속 질문 이해 → 계획 → 추가 조회 → 답변을 실행했습니다. 아래 본문은 서버가 반환한 lead·lines·notices 순서를 그대로 표시합니다. B는 수정하지 않았으며 R1에서 다시 실행하지 않았습니다. 예시는 더미 기준일 2026-10-29입니다.</p>'
    names={'A':'윤서','B':'민재','C':'지우'}
    for r in b:
        t=r['turns'][-1];trace=t['trace']
        body+='<section id="'+E(r['id'])+'"><h2>'+E(r['id']+' · '+t['question'])+'</h2><p>'+E(names[r['persona']])+' · '+badge(r['pass'])+'</p>'
        body+='<p>선행 질문: '+E(r['turns'][0]['question'])+'</p>'
        if r['expected'].get('extra'):body+='<p>이 사례에만 밤 거래 '+str(len(r['expected']['extra']))+'건을 추가한 뒤 되돌렸습니다.</p>'
        body+='<blockquote>'+'<br>'.join(E(line) for line in t['lines'])+'</blockquote>'
        body+='<p>호출 역할: '+E(' → '.join(r['call_roles']))+' · 선행 조회를 포함한 두 턴 총 '+str(round(r['ms']/1000,1))+'초</p>'
        body+='<p>실행된 추가 조회: <code>'+E(json.dumps(trace.get('queries',[]),ensure_ascii=False))+'</code></p>'
        if r['why']:body+='<p>고정 기준 미충족: '+E(', '.join(r['why']))+'</p>'
        if r['id'] in human_notes:body+='<p class="note">'+E(human_notes[r['id']])+'</p>'
        body+='<details><summary>질문 해석·계획·근거·검사 원본</summary><pre>'+pretty(trace)+'</pre></details></section>'
    (out/'b-answers.html').write_text(doc('B 실제 답변 원문',body))
    body='<h1>남겨 둔 실패 목록</h1><p><a href="report.html">종합 결과</a> · <a href="remaining-failures.json">전체 차이 JSON</a></p>'
    body+='<p>한 차례 수정 뒤 추가 수정하지 않았습니다. R1은 수정 후 실제 재검증에서도 실패한 항목, R0는 최초 실패 후 재검증하지 않은 항목입니다. R0의 실패가 현재도 재현된다고 단정하지 않습니다.</p>'
    for rev,title in [('R1','수정 후 확인된 해석 실패'),('R0','최초 실행 뒤 미재검증으로 남긴 해석 실패')]:
        body+='<section><h2>'+title+'</h2><table><tr><th>ID · 질문</th><th>최종 결정</th><th>차이</th></tr>'
        for r in unresolved:
            if r['revision']!=rev:continue
            body+='<tr><td>'+E(r['id']+' · '+r['text'])+'</td><td>'+('일치' if r.get('decision_ok') else '불일치')+'</td><td><pre>'+pretty(field_failures(r))+'</pre></td></tr>'
        body+='</table></section>'
    body+='<section><h2>대상·지표 해석</h2>'
    for r in reading:
        if not r['pass']:body+='<p>'+E(r['id']+' · '+r['question'])+'</p><pre>'+pretty(r.get('mismatches',r.get('error')))+'</pre>'
    body+='</section><section><h2>B 고정 기준 실패</h2>'
    for r in b:
        if not r['pass']:body+='<p><a href="b-answers.html#'+E(r['id'])+'">'+E(r['title'])+'</a>: '+E(', '.join(r['why']))+'</p>'
    body+='</section>'
    (out/'failures.html').write_text(doc('남은 실패 목록',body))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    m0=read(root/'frozen','manifest.json');m1=read(root/'revision1','manifest.json')
    patch=''
    for name in read(root,'correction-plan.json')['changed']:
        patch+=''.join(difflib.unified_diff((root/'frozen'/name).read_text().splitlines(True),(root/'revision1'/name).read_text().splitlines(True),fromfile='R0/'+name,tofile='R1/'+name))
    (out/'correction.patch').write_text(patch)
    bfiles=['claims.py','explain.py','investigation.py','tools.py','clauses.py']
    b_unchanged=all(sha(Path('backend/spending_analysis')/f)==m0['backend/spending_analysis/'+f] for f in bfiles)
    cases_unchanged=all(sha(Path(name))==expected for name,expected in m0.items() if name.startswith('scripts/router_eval/') and (Path(name).name.startswith('cases') or Path(name).name in ('check_reading.py','verify_checklist.py','verify_service.py','run.py','stage_b.py','explain_eval.py')))
    calls=[]
    for pth in root.glob('*-calls.jsonl'):calls += [json.loads(line) for line in pth.read_text().splitlines()]
    summary={'initial':{'checklist_pass':44,'checklist_total':46,'roles_strict_pass':116,'roles_scored':154,'roles_total':158,'roles_decision_pass':137,'reading_pass':6,'reading_total':8,'b_pass':11,'b_total':15},
             'recheck':{'checklist_pass':sum(r['pass'] for r in data['checklist-revision1']['rows']),'checklist_total':len(data['checklist-revision1']['rows']),'roles_strict_pass':sum(r['kind']=='ok' for r in roles1),'roles_total':len(roles1),'roles_decision_pass':sum(r.get('decision_ok',False) for r in roles1)},
             'ledger':read(root,'ledger.json'),'calls_logged':len(calls),'call_errors':[{'unit':r['unit'],'role':r['role'],'error':r['error']} for r in calls if r['error']],
             'case_expectations_unchanged':cases_unchanged,'b_implementation_unchanged':b_unchanged,
             'contracts':{'R0':m0['contracts.json'],'R1':m1['contracts.json']},'remaining_role_failures':len(unresolved),'correction_rounds':1,'commits':0}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    body='<h1>최종 코드 전체 검증 보고서</h1><p>2026-11-03 · 더미 데이터 기준일 2026-10-29 · 실제 LLM + 서비스 API</p>'
    body+='<p class="note"><strong>검증 완료. 한 차례 회귀 수정 후 남은 실패는 그대로 보존했습니다.</strong> 실제 LLM '+str(summary['ledger']['used'])+'/500회, 자동 재시도 0회, 커밋 0개. B는 고치지 않았습니다.</p>'
    body+='<p><a href="b-answers.html"><strong>B의 실제 답변 15개 원문 보기</strong></a> · <a href="failures.html">남은 실패 목록</a> · <a href="correction.patch">이번 수정만 보기</a> · <a href="summary.json">요약 JSON</a></p>'
    body+='''<section><h2>실제 LLM 결과</h2><table><tr><th>검사</th><th>고정 지시문 R0 전체 실행</th><th>한 차례 수정 후 R1 영향 사례</th></tr>
<tr><td>질문 목록</td><td><a href="checklist-initial.html">44/46 통과</a></td><td><a href="checklist-revision1.html">8/8 통과</a> · 실패했던 기간 선택·타인 소비 분류 포함</td></tr>
<tr><td>기존 역할별 해석 전체</td><td>158개 입력 중 정책 미정 4개 별도. 엄격한 추출 116/154, 최종 결정 137/154</td><td>17개 입력 재검증: 엄격한 추출 13/17, 최종 결정 16/17</td></tr>
<tr><td>대상·지표 해석 8개</td><td>6/8</td><td>설명·B 관련이므로 수정/재검증 안 함</td></tr>
<tr><td>B 연결 15개</td><td><a href="b-answers.html">11/15</a></td><td>B 수정/재실행 안 함</td></tr></table>
<p>R1로 전체 목록을 다시 실행한 정확도가 아닙니다. 과거 통과 기록은 reports/router_eval의 run30 및 이전 질문 목록 검증과 대조했습니다.
현행 역할별 사례는 단일 102개+연속 대화 20개(56턴)이며, 구 통합 사례와 frozen 과거판의 입력은 모두 현행 목록에 포함됩니다.</p></section>'''
    body+='''<section><h2>자동 테스트·빌드</h2><table><tr><th>검사</th><th>결과</th></tr>
<tr><td>백엔드 전체</td><td><a href="backend-full.log">1,069건: 1,066 통과, 2건 건너뜀, 기존 예상 실패 1건</a></td></tr>
<tr><td>수정 영향 백엔드</td><td><a href="backend-affected-revision1.log">144/144 통과</a></td></tr>
<tr><td>verify_service.py</td><td><a href="verify-service/report.html">11/11 통과</a> (고정 해석·실제 API, 외부 LLM 없음)</td></tr>
<tr><td>평가 도구 테스트</td><td>최초 38/39 → <a href="eval-tests-revision1.log">39/39 통과</a>. 지시문에 들어간 평가 문장 제거, 검사 기준은 그대로</td></tr>
<tr><td>프론트 lint</td><td><a href="frontend-lint.log">오류 없음, 기존 MoneyInput.jsx 경고 1건</a></td></tr>
<tr><td>프론트 build</td><td><a href="frontend-build.log">기본 빌드 통과</a> · <a href="frontend-fixture-build.log">더미 서비스 빌드 통과</a></td></tr></table>
<p>건너뛴 2건: 실제 분류 모델 파일 없음, 외부 CODEF 샌드박스 명시 실행 조건 미충족.
예상 실패 1건: 기존 budget_data의 validated/approved 예산 선택 문제. 이번 범위에서 수정하지 않았습니다.</p></section>'''
    body+='<section><h2>변경과 남은 판단</h2><p>변경은 라우터·소비 입력 해석 지시문 두 파일로 한정했습니다. 타인 소비의 담당 분류, 명시하지 않은 조건의 자동 채움, 모호한 기간·추이·분류·되묻기 해석을 보완하고 평가 문장 유출을 제거했습니다. 추가 답변 템플릿은 만들지 않았습니다.</p>'
    body+='<p>수정 후에도 n08, s4#2, s4#4, s4#5의 엄격한 해석 검사가 실패했습니다. 이 중 실제 최종 결정도 다른 사례는 s4#5(현재 예산 기간 동의 후 다시 질문)입니다. 그 밖의 최초 실패 24개는 추가 재검증하지 않았습니다.</p>'
    body+='<p>B에서는 밥값 상승 질문을 평균 금액 대신 시간대 증가로 답한 e08이 명확한 의도 불일치입니다. e01/e12는 전체 증가액이 다른 근거 문장 안에 포함되지만 고정 기준의 scope 직접 인용을 충족하지 못했습니다. e10은 확인 불가 답은 안전하나 지표가 amount 대신 count입니다. 기대값은 바꾸지 않았습니다.</p></section>'
    body+='<section><h2>고정 해시·원본</h2><p>지시문+가설 계약 묶음 SHA-256:</p><pre>R0 '+summary['contracts']['R0']+'\nR1 '+summary['contracts']['R1']+'</pre>'
    body+='<p>사례·기대값 변경 없음: '+str(cases_unchanged)+' · B 구현 변경 없음: '+str(b_unchanged)+'</p>'
    body+='<p><a href="frozen/manifest.json">실행 전 전체 파일 해시</a> · <a href="frozen/contracts.json">R0 지시문</a> · <a href="revision1/contracts.json">R1 지시문</a> · <a href="frozen/effective_role_cases.json">고정 해석 사례·기대값</a> · <a href="ledger.json">호출 장부</a></p>'
    body+='<p>실행 원본과 호출별 응답·시간·입력은 로컬 <code>'+E(str(root))+'</code>에 보관했습니다. 실패한 답변을 성공 예시로 바꿔 쓰거나 자동 재시도하지 않았습니다.</p></section>'
    (out/'report.html').write_text(doc('최종 검증 결과',body))
    print(json.dumps(summary,ensure_ascii=False,indent=2));print(out/'report.html')

if __name__=='__main__':main()
