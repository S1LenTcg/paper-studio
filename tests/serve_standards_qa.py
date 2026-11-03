"""Local UI fixture only. No browser worker or Xiumi connection is started."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperstudio.server import create_server
from paperstudio.standards import audit
from paperstudio.storage import write_json, now
from test_standards import sample


def main():
    root=Path(__file__).resolve().parents[1]/'artifacts'/'test-tmp'/'standards-browser-qa'
    server=create_server(8766,root)
    s=sample();s['title']='本地模拟验收稿（非秀米任务）'
    s['cells'][0]['style']['font']=16;s['cells'][0]['paragraphs'][0]['style']['font']=16
    s['cells'][1]['paragraphs'][0]['style']['line']=1.4
    folder=root/'standard-jobs'/('f'*32)
    write_json(folder/'snapshot.json',s);write_json(folder/'report.json',audit(s))
    write_json(folder/'status.json',dict(id='f'*32,stage='awaiting_review',title=s['title'],message='仅供本地界面交互验收，不连接秀米。',sourceUrl=s['url'],copyAttempted=False,saveAttempted=False,options={'rememberLogin':False,'leaveOpen':True},createdAt=now()))
    print('Local UI fixture: http://127.0.0.1:8766 (NO XIUMI)',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
