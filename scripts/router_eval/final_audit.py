"""고정 계약으로 최종 검증. 기대값 변경/자동 재시도 없이 공용 장부를 사용한다.
roles: 현행 전체 해석 사례(구 통합 사례 포함), reading: 대상·지표 8개,
checklist: 기존 46개, b: 실제 서비스에서 입력→계획→조회→답 15개.
실제 호출은 사용자가 승인한 상한과 --live를 반드시 지정한다.
"""
import argparse
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import time
from unittest.mock import patch

import verify_checklist as checklist
import verify_service as service
import stage_b
import check_reading
import explain_eval
from chat import engine
from chat.llm.live import LiveReading
from spending_analysis import reading, claims, investigation
from chat.router_core import reading as router_reading


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def contracts():
    return {'router': router_reading.PROMPT, 'spending': reading.PROMPT,
            'hypothesis_instruction': investigation.INSTRUCTION, 'hypothesis_targets': investigation.catalog(),
            'plan': claims.PLAN_PROMPT, 'answer': claims.ANSWER_PROMPT}


def frozen_check(root, revision):
    frozen = root / revision
    saved = json.loads((frozen / 'contracts.json').read_text())
    if saved != contracts(): raise SystemExit('고정 지시문과 현재 지시문이 다릅니다')
    manifest = json.loads((frozen / 'manifest.json').read_text())
    for name, expected in manifest.items():
        if name.startswith(('scripts/router_eval/', 'backend/spending_analysis/', 'backend/chat/llm/')) and Path(name).exists():
            if sha(Path(name).read_bytes()) != expected: raise SystemExit(f'고정 파일 변경: {name}')
    return sha((frozen / 'contracts.json').read_bytes())


class AuditReading(LiveReading):
    def __init__(self, root, max_calls, tag):
        super().__init__(max_calls, str(root / 'ledger.json'))
        self.log = root / f'{tag}-calls.jsonl'
        if self.log.exists(): raise SystemExit('기존 실행 로그를 덮어쓰지 않습니다')
        self.unit = None
        self.session_calls = []

    def __call__(self, system, user, timeout=None):
        roles = {router_reading.PROMPT: 'router', reading.PROMPT: 'spending', claims.PLAN_PROMPT: 'plan',
                 claims.ANSWER_PROMPT: 'answer'}
        row = {'unit': self.unit, 'role': roles.get(system, 'unknown'), 'prompt_sha256': sha(system),
               'request': json.loads(user), 'raw': None, 'error': None}
        started = time.monotonic()
        try:
            row['raw'] = super().__call__(system, user, timeout)
            return row['raw']
        except Exception as error:
            row['error'] = getattr(error, 'kind', type(error).__name__)
            raise
        finally:
            row['ms'] = round((time.monotonic() - started) * 1000)
            row['usage'] = self.usage()
            self.session_calls.append(row)
            with self.log.open('a') as f: f.write(json.dumps(row, ensure_ascii=False) + '\n')


def save(root, tag, rows, live, extra=None):
    value = {'rows': rows, 'usage': live.usage(), 'session_calls': len(live.session_calls), **(extra or {})}
    (root / f'{tag}-results.json').write_text(json.dumps(value, ensure_ascii=False, indent=2))


def roles(root, tag, live, ids=None):
    data = stage_b.load()
    rows = []
    def complete_for(turn):
        return live
    for case in data['cases']:
        if ids and case['id'] not in ids: continue
        live.unit = case['id']
        row = stage_b.run_case(case, complete_for)
        rows.append(row); save(root, tag, rows, live)
        print(row['id'], row['kind'], 'used', live.usage()['used'], flush=True)
    for scenario in data['scenarios']:
        if ids and scenario['id'] not in ids: continue
        live.unit = scenario['id']
        row = stage_b.run_scenario(scenario, complete_for)
        rows.append(row); save(root, tag, rows, live)
        print(row['id'], row['ok'], 'used', live.usage()['used'], flush=True)
    return rows


def read_cases(root, tag, live, ids=None):
    rows = []
    for cid, question, previous, expected in check_reading.CASES:
        if ids and cid not in ids: continue
        live.unit = cid
        try:
            got = json.loads(json.dumps(asdict(reading.read(question, None, live, previous=previous))))
            diff = {k: {'expected': v, 'actual': check_reading.at(got, k)} for k,v in expected.items() if check_reading.at(got,k) != v}
            row = {'id': cid, 'question': question, 'reading': got, 'mismatches': diff, 'pass': not diff}
        except Exception as error:
            row = {'id': cid, 'question': question, 'pass': False, 'error': type(error).__name__}
        rows.append(row);save(root,tag,rows,live);print(cid,row['pass'],flush=True)
    return rows


def snake(obj):
    if isinstance(obj, dict): return {re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower(): snake(v) for k,v in obj.items()}
    if isinstance(obj, list): return [snake(v) for v in obj]
    return obj


def b_cases(root, tag, live, ids=None):
    from django.db import transaction
    from spending_fixture.test_fixture import user, TODAY
    design = service.seed.load('design.json')
    service.seed.apply(design)
    service.set_login.set_passwords(service.PASSWORD, design)
    cases = json.loads(explain_eval.CASES.read_text())['cases']
    rows=[]
    with service.SERVICE, service.override_settings(CHAT_EXPLAIN_PLAN=True, ALLOWED_HOSTS=['testserver']), patch.object(engine,'reading',return_value=live):
        for case in cases:
            if ids and case['id'] not in ids: continue
            live.unit = case['id']
            startcalls = len(live.session_calls)
            with transaction.atomic():
                explain_eval._extra(case, user(case['persona']))
                client=service.Client(enforce_csrf_checks=True)
                assert service.login(client,design['personas'][case['persona']]['synthetic_email']).status_code == 200
                cat=reading.CATEGORY_NAMES.get(case['category'], '')
                first_question=f'지난달보다 {cat + " " if cat else ""}더 썼어?'
                turns=[]; cid=None; started=time.monotonic()
                for question in [first_question, case['question']]:
                    response=client.post('/api/chat/messages/',json.dumps({'conversationId':cid,'text':question}),content_type='application/json',HTTP_X_CSRFTOKEN=client.cookies['mc_fixture_csrftoken'].value)
                    data=response.json();cid=data.get('conversationId',cid)
                    content=data.get('messages',[{},{}])[1].get('content',{})
                    turns.append({'question':question,'status':response.status_code,'route':data.get('route'),'content':content,
                                  'lines':content.get('lead',[])+content.get('lines',[])+content.get('notices',[]),'trace':content.get('devTrace',{})})
                trace=snake(turns[-1]['trace'])
                scored=explain_eval.score(case,trace,[])
                checks={k:v for k,v in scored.items() if k != 'why' and v is not None}
                checks['service_success']=all(t['status']==200 and not (t['route'] or {}).get('fallback') for t in turns)
                checks['answer_present']=bool(turns[-1]['content'].get('result') and turns[-1]['lines'])
                row={'id':case['id'],'title':case['id']+' · '+case['question'],'persona':case['persona'],
                     'expected':case,'checks':checks,'pass':all(checks.values()),'why':scored['why'], 'turns':turns,
                     'ms':round((time.monotonic()-started)*1000),'call_roles':[c['role'] for c in live.session_calls[startcalls:]],
                     'human':case['human']}
                rows.append(row)
                transaction.set_rollback(True)
            save(root,tag,rows,live)
            print(row['id'],row['pass'],row['why'],flush=True)
    return rows


def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['roles','reading','checklist','b'])
    p.add_argument('--live',action='store_true',required=True)
    p.add_argument('--max-calls',type=int,required=True)
    p.add_argument('--root',required=True)
    p.add_argument('--tag',required=True)
    p.add_argument('--revision',default='frozen')
    p.add_argument('--ids',nargs='+')
    args=p.parse_args()
    root=Path(args.root).resolve()
    if root==service.ROOT or service.ROOT in root.parents: raise SystemExit('출력은 저장소 밖에 저장합니다')
    contract_sha=frozen_check(root,args.revision)
    if (root/f'{args.tag}-results.json').exists(): raise SystemExit('기존 결과를 덮어쓰지 않습니다')
    live=AuditReading(root,args.max_calls,args.tag)
    meta={'mode':args.mode,'revision':args.revision,'contract_sha256':contract_sha,'model':live.model,
          'max_calls':args.max_calls,'ids':args.ids,'automatic_retries':0}
    (root/f'{args.tag}-manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    if args.mode=='roles': rows=roles(root,args.tag,live,args.ids)
    elif args.mode=='reading': rows=read_cases(root,args.tag,live,args.ids)
    else:
        old=service.connection.creation.create_test_db(verbosity=0,autoclobber=True,keepdb=False)
        try:
            if args.mode=='b': rows=b_cases(root,args.tag,live,args.ids)
            else:
                cases=[c for c in checklist.CASES if not args.ids or c['id'] in args.ids]
                rows=checklist.run(live,cases)
        finally: service.connection.creation.destroy_test_db(old,verbosity=0)
        page=service.render(rows).replace('가짜 LLM + 실제 서비스 조회·계산·검사·대화 상태를 사용했습니다. 실제 LLM의 자연어 이해 품질 검증은 별도입니다.','실제 LLM + 실제 서비스 API. 실행 전 지시문·사례 해시 고정. 원본 답변이며 실패/대체 답변도 그대로 표시합니다.')
        (root/f'{args.tag}.html').write_text(page)
    save(root,args.tag,rows,live,meta)
    print(json.dumps({'units':len(rows),'usage':live.usage()},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
