"""Collect registered public anchors, generate authorized project series and optionally import MySQL."""
import argparse
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--offline',action='store_true',help='使用已保存的公开证据，不重复请求网页')
    args=parser.parse_args()
    from data_foundation.ingestion.public_bridge import load_records
    from data_foundation.ingestion.project_flow import build_package
    base=ROOT/'data_foundation/real_data/2026-10-12-v2'
    output=ROOT/'data_foundation/real_data/project_flow'
    crawler=ROOT/'爬取工具/data/local/project_flow'
    crawler.mkdir(parents=True,exist_ok=True)
    sources=json.loads((output/'reviewed_sources.json').read_text(encoding='utf-8'))
    captures=[]
    for source in sources:
        name=hashlib.sha256(source['source_url'].encode()).hexdigest()[:20]+'.html'
        target=output/'captures'/name;target.parent.mkdir(parents=True,exist_ok=True)
        status='saved_excerpt'
        if not args.offline:
            try:
                request=Request(source['source_url'],headers={'User-Agent':'AnhuiTourismProject/1.0 (public statistical research)'})
                with urlopen(request,timeout=15) as response:
                    raw=response.read(8_000_001)
                if len(raw)>8_000_000: raise ValueError('页面过大')
                text=raw.decode('utf-8',errors='replace')
                if not source['excerpt'] in text: raise ValueError('需人工核对正文')
                target.write_bytes(raw);status='captured_original'
            except Exception as exc:
                status='saved_excerpt:'+type(exc).__name__
        if target.is_file():
            source['evidence']['original_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
            source['evidence']['original_file']='captures/'+name
        captures.append({'url':source['source_url'],'status':status,'excerpt':source['excerpt']})
    records=load_records(ROOT/'爬取工具/data/local',base/'manifest.json')
    holidays=json.loads((base/'holiday_calendar.json').read_text(encoding='utf-8'))['holidays']
    for h in holidays:
        for k in ['start_date','end_date']: h[k]=date.fromisoformat(h[k])
    payload=build_package(output,records,holidays,sources)
    for name in ['manifest.json','flow_data.csv','reviewed_sources.json']:
        shutil.copy2(output/name,crawler/name)
    report={'status':'generated','rows':len(payload['rows']),'start':payload['start'],'end':payload['end'],
            'updated_at':datetime.now(timezone.utc).isoformat(),'captures':captures}
    if args.apply:
        result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'data_foundation/examples/import_project_flow.py'),'--apply'],
            cwd=ROOT/'api_platform',capture_output=True,text=True,encoding='utf-8')
        if result.returncode: raise RuntimeError('C导入失败，请在终端运行import_project_flow.py检查；'+result.stderr[-1000:])
        report['mysql_import']=json.loads(result.stdout)
    (crawler/'update-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
