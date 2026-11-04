# -*- coding: utf-8 -*-
"""가계동향 원자료를 확정 10개 생활비 카테고리로 정제한다. 학습은 수행하지 않는다."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

WORKSPACE = Path(__file__).resolve().parents[2] / 'local_workspace'


def run(raw, output, mapping):
    raw, output, mapping = Path(raw).resolve(), Path(output).resolve(), Path(mapping).resolve()
    if output.exists():
        raise FileExistsError(f'기존 결과를 덮어쓰지 않습니다: {output}')
    if not mapping.is_file() or any(not (raw / f'{year}_household_survey.csv').is_file() for year in range(2019, 2027)):
        raise FileNotFoundError('2019~2026 원자료 8개와 카테고리 매핑표가 필요합니다.')
    if output.is_relative_to(WORKSPACE / 'output'):
        raise ValueError('output은 문서 전용입니다. 정제 결과는 data에 저장하세요.')
    output.mkdir(parents=True)
    RAW, OUT = str(raw), str(output)
    P='가계지출_소비지출_'
    # 1) 카테고리 연결 기준표 v1 (Claude 대응표 초안 기준, 팀 결정에 따라 갱신)
    MAP_FILE=str(mapping)   # 팀 결정표(D1~D16) 반영본. 기준 변경 시 이 파일만 수정
    _m=pd.read_csv(MAP_FILE,encoding='utf-8-sig')
    _m=_m[(_m['부호']!=0)&~_m['카테고리'].str.contains('참고')]
    MAP=list(zip(_m['카테고리'],_m['원본항목(가계지출_소비지출_ 생략)'],_m['부호'].astype(int)))
    _f=pd.read_csv(MAP_FILE,encoding='utf-8-sig').fillna('')
    FIXED_COLS=list(dict.fromkeys(_f.loc[(_f['고정지출구분']=='고정(필수)')&~_f['카테고리'].str.startswith('기타'),'원본항목(가계지출_소비지출_ 생략)']))   # 고정지출 표시는 연결기준표에서 관리
    CATS=['식비','주거·통신','교통·차량','생활용품','패션·쇼핑','의료·건강','뷰티·미용','교육','문화·여가','여행·숙박']
    LIVING=list(CATS)   # 10개 카테고리. 2026-10-22: 생활비(예산)에서는 고정비를 뺌 → 주거·통신은 고정비를 뺀 나머지(주택 수리·가사서비스)만 생활비
    EXTRA=[c for c in dict.fromkeys(m[0] for m in MAP) if c not in CATS]   # 자동차구입(별도)·기타 (콘텐츠 구독은 10/21부터 문화·여가)
    log={}
    def load(y):
        f=os.path.join(RAW,f'{y}_household_survey.csv')
        d=pd.read_csv(f,encoding='cp949',low_memory=False)
        d.columns=[c.replace('주스기타음료','쥬스기타음료') for c in d.columns]   # 2026 열 이름 통일
        d.insert(0,'원본행번호',np.arange(2,len(d)+2)); d.insert(0,'원본파일',os.path.basename(f)); d['연도']=y
        return d,f
    def process(years,tag):
        parts=[];files=[]
        for y in years:
            d,f=load(y); parts.append(d); files.append(f)
        df=pd.concat(parts,ignore_index=True); L={'01_원본행수':len(df)}
        ym=df['조사연월'].astype(str); df['분기']=ym.str[4].astype(int)   # 조사연월 예: 202514 → 2025년 1분기(추정, 공식 확인 필요)
        # 2) 결측·단위
        for c in ['전세보증금','월세사글세','자동차보유대수']:
            L[f'02_빈칸→0_{c}']=int(df[c].isna().sum()); df[c]=df[c].fillna(0)
        for c in ['월세평가금액','전세보증금','월세사글세']: df[c+'_원']=df[c]*1000   # 천원 → 원
        # 3) 대상: 1인 가구 & 가구주 20~34세
        df=df[(df['가구원수']==1)&df['가구주_연령'].between(20,34)].copy(); L['03_1인_20~34세']=len(df)
        # 4) 파생변수
        df['처분가능소득']=df['소득']-df['가계지출_비소비지출금액']
        for cat in dict.fromkeys(m[0] for m in MAP):
            df[cat]=sum(s*df[P+col] for k,col,s in MAP if k==cat)
        df['고정비']=sum(df[P+c] for c in FIXED_COLS)   # 연결기준표 '고정지출구분=고정(필수)' 중 기타 밖 항목(월세·수도·관리비·연료·통신요금). 모두 주거·통신 소속. 2026-10-22: 생활비에서 빼고 입력값으로 씀
        df['참고_콘텐츠구독']=df[P+'정보통신_방송시청각콘텐츠이용금액']   # 문화·여가에 포함된 OTT·음악 구독 금액(추적용)
        _cc=CATS+EXTRA
        _small=(df[_cc]<0)&(df[_cc]>=-5)   # 원자료 반올림으로 하위항목 합이 상위항목보다 1~5원 큰 경우
        L['검수_반올림음수→0(셀)']=int(_small.sum().sum()); df[_cc]=df[_cc].mask(_small,0)
        # 2026-10-22: 생활비(예산) = 10개 카테고리 − 고정비. 고정비는 전부 주거·통신 소속이라 주거·통신에서만 뺀다
        df['생활비_주거·통신']=df['주거·통신']-df['고정비']
        _sm=(df['생활비_주거·통신']<0)&(df['생활비_주거·통신']>=-5)
        L['검수_주거통신고정비차감_반올림음수→0']=int(_sm.sum()); df.loc[_sm,'생활비_주거·통신']=0
        L['검수_주거통신고정비차감_음수(-5원미만)']=int((df['생활비_주거·통신']<0).sum())
        LIV={c:(df['생활비_주거·통신'] if c=='주거·통신' else df[c]) for c in LIVING}
        df['생활비']=sum(LIV.values())
        chk=(df[CATS+EXTRA].sum(axis=1)-df['가계지출_소비지출금액']).abs()
        L['검수_합계차이_최대(원)']=float(chk.max())
        neg=int((df[CATS+EXTRA]<0).sum().sum()); L['검수_음수금액']=neg
        # 5) 제외(별도 파일 보존)
        reason=pd.Series('',index=df.index)
        reason[df['가구구분코드']!=1]+='근로자가구아님;'
        reason[df['소득']<=0]+='소득0이하;'
        reason[df['처분가능소득']<=0]+='처분가능소득0이하;'
        reason[df['가계지출_소비지출금액']<=0]+='소비지출0;'
        reason[df['생활비']<=0]+='생활비0;'
        ex=df[reason!=''].assign(제외사유=reason[reason!='']); df=df[reason==''].copy()
        L['04_제외행']=len(ex); L['04_제외사유']=ex['제외사유'].value_counts().to_dict(); L['05_정제행']=len(df)
        # 6) 비율(학습 목표)·입력 변수
        for c in LIVING: df[f'비율_{c}']=(df['생활비_주거·통신'] if c=='주거·통신' else df[c])/df['생활비']
        for c in CATS: df[f'소비비율_{c}']=df[c]/df['가계지출_소비지출금액']
        L['검수_생활비비율합_최대오차']=float((df[[f'비율_{c}' for c in LIVING]].sum(axis=1)-1).abs().max())
        df['로그처분가능소득']=np.log(df['처분가능소득'])
        df['성별']=df['가구주_성별코드'].map({1:'남',2:'여'}); df['나이']=df['가구주_연령']
        df['근로자가구']=(df['가구구분코드']==1).map({True:'Y',False:'N'})
        df['도시']=df['도시읍면부구분코드'].map({1:'Y',0:'N'})
        df['자동차보유']=(df['자동차보유대수']>0).map({True:'Y',False:'N'})
        # 7) 검토 표시(삭제하지 않음)
        flag=pd.Series('',index=df.index)
        flag[df['생활비']<df['생활비'].quantile(0.005)]+='생활비_하위0.5%;'
        flag[(df[[f'비율_{c}' for c in LIVING]]>=0.8).any(axis=1)]+='단일카테고리_80%이상;'
        flag[df['자동차구입(별도)']>0]+='자동차구입_있음;'
        df['검토표시']=flag; L['06_검토표시행']=int((flag!='').sum()); L['06_검토표시_사유']=flag[flag!=''].str.split(';').explode().replace('',np.nan).dropna().value_counts().to_dict()
        L['07_근로자가구']=int((df['근로자가구']=='Y').sum()); L['07_연도별']=df.groupby('연도').size().to_dict()
        cols=['원본파일','원본행번호','연도','분기','조사연월','가중값','나이','성별','근로자가구','도시','자동차보유','자동차보유대수','입주형태코드',
              '월세사글세_원','전세보증금_원','월세평가금액_원','소득','가계지출_비소비지출금액','처분가능소득','로그처분가능소득','가계지출_소비지출금액']+CATS+EXTRA+['고정비','생활비_주거·통신','참고_콘텐츠구독','생활비']+[f'비율_{c}' for c in LIVING]+[f'소비비율_{c}' for c in CATS]+['검토표시']
        df=df[cols].rename(columns={'가계지출_비소비지출금액':'비소비지출','가계지출_소비지출금액':'소비지출'})
        excols=['원본파일','원본행번호','연도','조사연월','가구구분코드','가구원수','가구주_연령','소득','가계지출_비소비지출금액','가계지출_소비지출금액','처분가능소득','생활비','제외사유']
        return df,ex[excols],L,files
    def sha(p):
        with open(p,'rb') as stream:
            return hashlib.file_digest(stream,'sha256').hexdigest()
    main_data,ex1,L1,f1=process(range(2019,2026),'main'); hold,ex2,L2,f2=process([2026],'2026')
    outs={}
    for name,d in [('household_single_20_34_2019_2025.csv',main_data),('household_single_20_34_2026_holdout.csv',hold),('excluded_rows_2019_2025.csv',ex1),('excluded_rows_2026.csv',ex2)]:
        p=os.path.join(OUT,name); d.to_csv(p,index=False,encoding='utf-8-sig'); outs[name]={'rows':len(d),'cols':d.shape[1],'sha256':sha(p)}
    shutil.copyfile(mapping,os.path.join(OUT,'category_mapping_v2.csv'))
    outs['category_mapping_v2.csv']={'rows':len(_m),'sha256':sha(MAP_FILE),'role':'입력'}
    meta={'created_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),
     'scope':'예산 카테고리 비율 모델용 전처리. 학습·평가 분할과 모델 학습은 하지 않음.',
     'mapping_version':'v2 (output\\data_research\\category_policy\\store_category_decisions_20261015.xlsx 팀 결정 D1~D16 반영)','target':'1인 가구·가구주 20~34세·근로자 가구',
     'source_files':[{'file':os.path.basename(f),'sha256':sha(f)} for f in f1+f2],
     'steps_2019_2025':L1,'steps_2026':L2,'outputs':outs,
     'rules':['2026 파일의 주스기타음료 열 이름을 쥬스기타음료로 통일',
      '전세보증금·월세사글세·자동차보유대수 빈칸은 0으로 채움(2024년만 빈칸 저장, 다른 연도는 0)',
      '월세평가금액·전세보증금·월세사글세는 천원 단위 → 원 단위 열(_원) 추가',
      '대상: 가구원수 1명, 가구주 연령 20~34세',
      '처분가능소득 = 소득 − 비소비지출 (조사 소득은 세전, 앱 실수령액 기준과 맞추기 위함)',
      '10개 카테고리 금액은 카테고리_연결기준표_v2 기준(팀 결정 D1~D16). 자동차구입은 일회성 고액이라 별도 분리. 담배·복권·세탁·수선·우편·보험·기타금융·복지시설·기타서비스는 기타(10개 밖)',
      '생활비 = 10개 카테고리 합 − 고정비 (2026-10-22 변경: 앱 예산은 월급 − 고정비 − 저축 뒤 남은 돈을 나누므로 고정비를 생활비에서 뺌. 기타·자동차구입도 제외). 고정비 = 월세·수도·관리비·연료·통신요금(연결기준표 고정(필수), 모두 주거·통신 소속) → 고정비 열(입력값). 주거·통신 카테고리 금액 열은 고정비 포함(소비 분석용)이고, 생활비 몫은 생활비_주거·통신 열. 거래 분류에서 고정비 거래에 카테고리를 붙이는 규칙은 그대로. OTT·음악 구독은 고정비가 아니라 문화·여가 생활비에 포함(참고_콘텐츠구독 열로 추적)',
      '학습 목표: 비율_<카테고리> = 생활비 몫 ÷ 생활비 (10개 합 = 1, 주거·통신은 생활비_주거·통신 사용). 참고: 소비비율_<카테고리> = 카테고리 금액(고정비 포함) ÷ 소비지출',
      '제외: 근로자 가구 아님(팀 결정 10/15), 소득 0 이하, 처분가능소득 0 이하, 소비지출 0, 생활비 0 → 제외행 파일에 보존',
      '검토표시: 생활비 하위 0.5%, 단일 카테고리 비율 80% 이상, 자동차구입 있음 — 삭제하지 않음',
      '조사연월의 분기 해석(예: 202514 → 1분기)은 추정. 금액의 월평균 여부도 공식 확인 필요',
      '가구일련번호는 같은 가구 식별자가 아님(같은 번호 중 성별 일치 약 50%) → 출력하지 않음, 가구 단위 그룹 분할 불필요',
      '원자료 반올림으로 생긴 -5~-1원 카테고리 금액은 0으로 보정(건수는 처리기록에 기록)','물가 보정은 하지 않음(비율 예측에는 영향 작음, 금액 표시 시 별도 결정)',
      '가중값은 출력에 포함. 평균표·허용범위 계산 시 반드시 적용','공공용 중분류로 분리할 수 없는 혼합 항목(운전교습비, 목욕료, 성형외과, 동물병원, 부동산 수수료 등)은 연결기준표 메모에 기록','이전 v1 결과(전체 가구, 대응표 초안 기준)는 _이전결과_v1_전체가구 폴더에 보존'],
     'not_done':['학습·평가 분할','모델 학습','물가 보정','MDIS 이용조건 확인']}
    json.dump(meta,open(os.path.join(OUT,'processing_records.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1,default=str)
    print(json.dumps({'2019-2025':L1,'2026':L2},ensure_ascii=False,indent=1,default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, default=WORKSPACE / 'raw/household_survey_2019_2026')
    parser.add_argument('--mapping', type=Path, default=Path(__file__).with_name('category_mapping_v2.csv'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.raw, args.output, args.mapping)


if __name__ == '__main__':
    main()
