"""C-owned JSON/CSV package importer. Preview unless --apply is supplied."""
import argparse
import json
import os
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,default=root/'data_foundation/real_data/project_flow/manifest.json')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    from data_foundation.ingestion.project_flow import validate_package,import_package
    payload=validate_package(args.manifest)
    result={'status':'preview','rows':len(payload['rows']),'start':payload['start'],'end':payload['end']}
    if args.apply:
        from dotenv import dotenv_values
        from data_foundation import create_store
        config=dotenv_values(root/'api_platform/.env')
        url=os.environ.get('DATABASE_URL') or config.get('DATABASE_URL')
        if not url: raise SystemExit('请在api_platform/.env配置DATABASE_URL')
        store=create_store(url,data_root=root/'api_platform/data')
        try: result=import_package(store,args.manifest)
        finally: store.dispose()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
