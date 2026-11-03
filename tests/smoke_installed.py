"""Run against a built/installed wheel, never the source checkout."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [entry for entry in sys.path if Path(entry or '.').resolve() != SOURCE_ROOT]

from paperstudio.paths import SOURCE_CHECKOUT, WEB
from paperstudio.server import create_server


def main():
    assert not SOURCE_CHECKOUT, 'smoke test accidentally imported the checkout'
    assert (WEB/'index.html').is_file()
    with tempfile.TemporaryDirectory() as tmp:
        server=create_server(0,tmp)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for path in ['/', '/styles.css', '/standards.js', '/api/bootstrap']:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
                conn.request('GET',path);response=conn.getresponse();body=response.read();conn.close()
                assert response.status==200,(path,response.status)
                assert body,path
                if path=='/api/bootstrap':
                    data=json.loads(body)
                    exported=server.app.export(data['sample'])
                    assert exported['url'].endswith('/article.html')
            print('Installed wheel: static files, bootstrap and HTML export passed.')
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
