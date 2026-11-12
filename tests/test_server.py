import copy
import http.client
import json
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from paperstudio.document import SAMPLE
from paperstudio.server import Application, create_server
from paperstudio.storage import DraftStore, write_json
from paperstudio.reference import ReferenceError
from paperstudio.ai import AIError

TEST_TMP = Path(__file__).resolve().parents[1] / 'artifacts' / 'test-tmp'
TEST_TMP.mkdir(parents=True, exist_ok=True)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        cls.server=create_server(0,cls.temp.name)
        cls.port=cls.server.server_port
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        h={'Content-Type':'application/json'}
        if headers:h.update(headers)
        conn.request(method,path,body=json.dumps(data).encode() if data is not None else None,headers=h)
        res=conn.getresponse();status=res.status;body=res.read();response_headers=dict(res.getheaders());conn.close()
        return status,body,response_headers

    def test_health_and_static_resources(self):
        for route in ['/','/app.js','/ai.js','/styles.css','/compact.css','/creative','/creative.css','/creative.js','/assets/sample.png','/api/health','/api/bootstrap','/api/ai/settings']:
            with self.subTest(route=route):
                status,body,headers=self.request('GET',route)
                self.assertEqual(status,200);self.assertTrue(body)
                self.assertIn('Content-Security-Policy',headers)

    def test_ai_unconfigured_and_cross_origin_paths_do_not_start_model(self):
        with patch.object(self.server.app.ai,'start',side_effect=AIError('先配置模型','not_configured')) as start:
            request_id=uuid.uuid4().hex
            status,body,_=self.request('POST','/api/ai/jobs',{
                'document':SAMPLE,'requirement':'采访专刊','requestId':request_id,
            })
            self.assertEqual(status,422);self.assertEqual(json.loads(body)['code'],'not_configured')
            start.assert_called_once_with(SAMPLE,'采访专刊',request_id)
            status,_,_=self.request('POST','/api/ai/settings',{'apiKey':'x'},{'Origin':'https://evil.example'})
            self.assertEqual(status,403)

    def test_ai_settings_exclude_key_and_do_not_write_key_file(self):
        status,body,_=self.request('POST','/api/ai/settings',{'baseUrl':'https://api.example.com/v1','model':'test-model','apiKey':'unit-test-dummy-key'})
        self.assertEqual(status,200);self.assertTrue(json.loads(body)['configured'])
        status,body,_=self.request('GET','/api/ai/settings')
        self.assertEqual(status,200);self.assertNotIn(b'unit-test-dummy-key',body);self.assertNotIn('apiKey',json.loads(body))
        self.request('POST','/api/ai/settings',{'clearKey':True})

    def test_reference_html_import_and_error_response(self):
        source=(Path(__file__).parent/'fixtures'/'reference_article.html').read_text(encoding='utf-8')
        status,body,_=self.request('POST','/api/references',{'html':source,'filename':'reference.html'})
        self.assertEqual(status,201);record=json.loads(body)
        status,body,_=self.request('GET','/api/references/'+record['id'])
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['profile']['paper'],'#fff0dd')
        status,body,_=self.request('GET','/data/references/'+record['id']+'/source.html')
        self.assertEqual(status,404)
        with patch('paperstudio.server.fetch_article',side_effect=ReferenceError('平台要求验证','verification_required')):
            status,body,_=self.request('POST','/api/references',{'url':'https://example.com/post'})
        self.assertEqual(status,422)
        self.assertEqual(json.loads(body)['code'],'verification_required')

    def test_draft_roundtrip_updates_same_id(self):
        doc=copy.deepcopy(SAMPLE)
        status,body,_=self.request('POST','/api/drafts',{'document':doc})
        self.assertEqual(status,200);record=json.loads(body)
        doc['title']='修改后的标题'
        status,body,_=self.request('POST','/api/drafts',{'document':doc,'id':record['id'],
                                                       'expectedVersion':record['version']})
        self.assertEqual(status,200)
        updated=json.loads(body)
        self.assertEqual(updated['id'],record['id'])
        self.assertNotEqual(updated['version'],record['version'])
        status,body,_=self.request('GET','/api/drafts/'+record['id'])
        self.assertEqual(json.loads(body),updated)

    def test_concurrent_draft_updates_preserve_the_winning_tab_and_allow_a_copy(self):
        _,body,_=self.request('POST','/api/drafts',{'document':SAMPLE})
        original=json.loads(body)
        ready=threading.Barrier(2)

        def save_from_tab(title):
            ready.wait(timeout=5)
            status,body,_=self.request('POST','/api/drafts',{
                'id':original['id'],'expectedVersion':original['version'],
                'document':{**SAMPLE,'title':title},
            })
            return status,json.loads(body),title

        with ThreadPoolExecutor(max_workers=2) as executor:
            results=sorted(executor.map(save_from_tab,['标签页一','标签页二']),key=lambda result:result[0])
        (success,winner,_),(conflict,error,losing_title)=results
        self.assertEqual((success,conflict),(200,409))
        self.assertEqual(error['code'],'draft_conflict')
        status,body,_=self.request('POST','/api/drafts',{'document':SAMPLE,'id':original['id']})
        self.assertEqual(status,409)
        _,body,_=self.request('GET','/api/drafts/'+original['id'])
        self.assertEqual(json.loads(body),winner)
        status,body,_=self.request('POST','/api/drafts',{'document':{**SAMPLE,'title':losing_title}})
        self.assertEqual(status,200)
        saved_copy=json.loads(body)
        self.assertNotEqual(saved_copy['id'],original['id'])
        self.assertEqual(saved_copy['document']['title'],losing_title)

    def test_draft_save_can_replay_the_last_write_but_cannot_overwrite_a_newer_version(self):
        first={'id':uuid.uuid4().hex,'writeId':uuid.uuid4().hex,'document':SAMPLE}
        status,body,_=self.request('POST','/api/drafts',first)
        self.assertEqual(status,200)
        created=json.loads(body)
        replay_status,replay_body,_=self.request('POST','/api/drafts',first)
        self.assertEqual(replay_status,200)
        self.assertEqual(json.loads(replay_body),created)
        update={**first,'expectedVersion':created['version'],'writeId':uuid.uuid4().hex,
                'document':{**SAMPLE,'title':'新版'}}
        status,body,_=self.request('POST','/api/drafts',update)
        self.assertEqual(status,200)
        replay_status,replay_body,_=self.request('POST','/api/drafts',update)
        self.assertEqual(replay_status,200)
        self.assertEqual(json.loads(replay_body),json.loads(body))
        status,_,_=self.request('POST','/api/drafts',first)
        self.assertEqual(status,409)

    def test_legacy_draft_can_be_opened_and_updated_with_a_version(self):
        draft_id=uuid.uuid4().hex
        path=self.server.app.drafts.root/(draft_id+'.json')
        write_json(path,{'id':draft_id,'updatedAt':'2026-11-03T00:00:00+00:00','document':SAMPLE})
        status,body,_=self.request('GET','/api/drafts/'+draft_id)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['version'],'legacy')
        self.assertNotIn('version',json.loads(path.read_text(encoding='utf-8')))
        status,body,_=self.request('POST','/api/drafts',{
            'id':draft_id,'expectedVersion':'legacy','document':{**SAMPLE,'title':'更新旧稿'},
        })
        self.assertEqual(status,200)
        self.assertRegex(json.loads(body)['version'],r'^[0-9a-f]{32}$')

    def test_export_is_portable_and_downloadable(self):
        status,body,_=self.request('POST','/api/exports',{'document':SAMPLE})
        self.assertEqual(status,201);url=json.loads(body)['url']
        status,body,headers=self.request('GET',url)
        self.assertEqual(status,200)
        self.assertIn('attachment;',headers['Content-Disposition'])
        self.assertIn(b'data:image/jpeg;base64,',body)
        self.assertNotIn(b'/assets/sample.png',body)
        self.assertNotIn(b'/assets/auditorium.jpg',body)

    def test_preview_reuses_local_images_but_copy_html_stays_portable(self):
        status,body,_=self.request('POST','/api/render',{'document':SAMPLE,'preview':True})
        self.assertEqual(status,200)
        self.assertIn(b'/assets/auditorium.jpg',body)
        self.assertNotIn(b'data:image/jpeg;base64,',body)
        status,body,_=self.request('POST','/api/render',{'document':SAMPLE})
        self.assertEqual(status,200)
        self.assertIn(b'data:image/jpeg;base64,',body)
        self.assertNotIn(b'/assets/auditorium.jpg',body)

    def test_only_public_builtin_images_can_be_cached(self):
        _,_,headers=self.request('GET','/assets/auditorium.jpg')
        self.assertIn('max-age=',headers['Cache-Control'])
        for route in ['/app.js','/api/drafts','/api/bootstrap']:
            with self.subTest(route=route):
                _,_,headers=self.request('GET',route)
                self.assertEqual(headers['Cache-Control'],'no-store')

    def test_malformed_draft_does_not_break_other_drafts(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            store=DraftStore(tmp)
            good=store.save(SAMPLE)
            for value in [None,[],{'document':None},{'id':'bad','updatedAt':2,'document':{'title':[],'theme':{}}}]:
                write_json(Path(tmp)/(uuid.uuid4().hex+'.json'),value)
            self.assertEqual([r['id'] for r in store.list()],[good['id']])

    def test_existing_png_drafts_still_export_original_image(self):
        doc=copy.deepcopy(SAMPLE);doc['blocks'][1]['src']='/assets/sample.png'
        status,body,_=self.request('POST','/api/exports',{'document':doc})
        self.assertEqual(status,201)
        status,body,_=self.request('GET',json.loads(body)['url'])
        self.assertEqual(status,200)
        self.assertTrue(b'data:image/png;base64,' in body)
        self.assertFalse(b'Phil Roeder' in body)

    def test_no_data_directory_or_traversal_exposure(self):
        for route in ['/data/drafts','/../AGENTS.md','/api/drafts/..','/api/exports/../../AGENTS.md','/api/jobs/../../private']:
            with self.subTest(route=route):
                status,body,_=self.request('GET',route)
                self.assertIn(status,[400,404]);self.assertNotIn(b'# Paper Studio',body)

    def test_cross_origin_and_foreign_host_rejected(self):
        for method,headers in [('POST',{'Origin':'https://example.com'}),('GET',{'Host':'rebind.example.com'}),('POST',{'Sec-Fetch-Site':'cross-site'})]:
            with self.subTest(headers=headers):
                status,_,_=self.request(method,'/api/drafts',{'document':SAMPLE},headers)
                self.assertEqual(status,403)

    def test_wrong_content_type_and_bad_payload_rejected(self):
        status,_,_=self.request('POST','/api/drafts',{'document':SAMPLE},{'Content-Type':'text/plain'})
        self.assertEqual(status,415)
        status,_,_=self.request('POST','/api/render',{'document':{'title':'x','blocks':[{'type':'x'}]}})
        self.assertEqual(status,400)

    def test_xiumi_never_accepts_existing_draft_destination(self):
        status,_,_=self.request('POST','/api/xiumi/jobs',{'document':SAMPLE,'destination':'existing_draft'})
        self.assertEqual(status,400)

    def test_standards_http_routes_guards_and_selection(self):
        from test_standards import sample
        from paperstudio.standards import audit
        service=self.server.app.standards
        status,body,_=self.request('GET','/api/standards')
        self.assertEqual(status,200);self.assertIn('profile',json.loads(body))
        for route in ['/standards.js','/standards.css']:
            self.assertEqual(self.request('GET',route)[0],200)
        payload={'url':'https://xiumi.us/studio/v5#/paper/for/123/cube/0','requestId':uuid.uuid4().hex}
        fake=type('Proc',(),{'poll':lambda self:None})()
        with patch('paperstudio.standards_service.availability',return_value={'available':True}),patch('paperstudio.standards_service.subprocess.Popen',return_value=fake) as spawn:
            self.assertEqual(self.request('POST','/api/standards/jobs',payload)[0],202)
            self.assertEqual(self.request('POST','/api/standards/jobs',payload)[0],202);spawn.assert_called_once()
            folder=service.folder(payload['requestId']);snap=sample();report=audit(snap)
            write_json(folder/'snapshot.json',snap);write_json(folder/'report.json',report)
            state=service.get(payload['requestId'],False);state['stage']='awaiting_review';write_json(folder/'status.json',state)
            base='/api/standards/jobs/'+payload['requestId']
            roles={'0':'body','1':'body'}
            status,body,_=self.request('POST',base+'/report',{'roles':roles})
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['summary']['automatic'],1)
            plan={'fingerprint':report['fingerprint'],'roles':roles,'selected':['0:gap'],'bodyColor':'#565656'}
            self.assertEqual(self.request('POST',base+'/apply',plan,{'Origin':'https://evil.test'})[0],403)
            self.assertEqual(self.request('POST',base+'/apply',{**plan,'selected':['0:margins']})[0],400)
            self.assertEqual(self.request('POST',base+'/apply',plan)[0],202)
            self.assertEqual(self.request('POST',base+'/apply',plan)[0],202)
            self.assertEqual(self.request('POST',base+'/cancel',{})[0],400)
            self.assertEqual(self.request('GET','/data/standard-jobs/'+payload['requestId']+'/snapshot.json')[0],404)
            state=service.get(payload['requestId'],False);state['stage']='cancelled';write_json(folder/'status.json',state)
            self.server.app.procs.pop('norm:'+payload['requestId'])

    def test_job_concurrency_and_id_validation_without_starting_browser(self):
        class FakeProcess:
            def poll(self):return None
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            app=Application(temp)
            with patch('paperstudio.server.availability',return_value={'available':True}),patch('paperstudio.server.subprocess.Popen',return_value=FakeProcess()) as spawn:
                job=app.start_job(SAMPLE)
                self.assertEqual(job['stage'],'queued')
                args=spawn.call_args.args[0]
                self.assertIsInstance(args,list)
                self.assertEqual(args[1:3],['-m','paperstudio.bridge'])
                with self.assertRaises(ValueError):app.start_job(SAMPLE)
                with self.assertRaises(ValueError):app.job('../../bad')

    def test_restart_marks_unfinished_jobs_without_retry(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            job_id='a'*32
            write_json(Path(temp)/'jobs'/job_id/'status.json',{'id':job_id,'stage':'saving'})
            with patch('paperstudio.server.subprocess.Popen') as spawn:
                app=Application(temp)
                self.assertEqual(app.job(job_id)['stage'],'interrupted')
                spawn.assert_not_called()

    def test_xiumi_same_request_is_idempotent_and_cannot_change_content(self):
        class FakeProcess:
            def poll(self):return None
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            app=Application(temp)
            request_id=uuid.uuid4().hex
            with patch('paperstudio.server.availability',return_value={'available':True}),patch('paperstudio.server.subprocess.Popen',return_value=FakeProcess()) as spawn:
                first=app.start_job(SAMPLE,request_id)
                second=app.start_job(SAMPLE,request_id)
                self.assertEqual(first['id'],second['id'])
                self.assertEqual(first['id'],request_id)
                spawn.assert_called_once()
                with self.assertRaises(ValueError):app.start_job({**SAMPLE,'title':'另一篇稿'},request_id)
                with self.assertRaises(ValueError):app.start_job(SAMPLE,'../../bad')
                self.assertEqual(len(list((Path(temp)/'jobs').iterdir())),1)

    def test_xiumi_browser_options_require_opt_in_and_are_bound_to_request(self):
        class FakeProcess:
            def poll(self):return None
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            app=Application(temp);request_id=uuid.uuid4().hex
            with patch('paperstudio.server.availability',return_value={'available':True}),patch('paperstudio.server.subprocess.Popen',return_value=FakeProcess()) as spawn:
                job=app.start_job(SAMPLE,request_id)
                self.assertFalse(job['options']['rememberLogin']);self.assertTrue(job['options']['leaveOpen'])
                self.assertEqual(spawn.call_args.kwargs['env']['PYTHONIOENCODING'],'utf-8')
                self.assertEqual(spawn.call_args.kwargs['env']['WANYOU_SELENIUM_BROWSER'],'chrome')
                with self.assertRaises(ValueError):app.start_job(SAMPLE,request_id,{'rememberLogin':True})
                with self.assertRaises(ValueError):app.start_job(SAMPLE,uuid.uuid4().hex,{'rememberLogin':'yes'})
                spawn.assert_called_once()

    def test_xiumi_same_request_after_restart_only_returns_interrupted_record(self):
        class FakeProcess:
            def poll(self):return None
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            request_id=uuid.uuid4().hex
            with patch('paperstudio.server.availability',return_value={'available':True}),patch('paperstudio.server.subprocess.Popen',return_value=FakeProcess()) as spawn:
                app=Application(temp);app.start_job(SAMPLE,request_id)
                path=Path(temp)/'jobs'/request_id/'status.json'
                record=json.loads(path.read_text(encoding='utf-8'));record.update(stage='saving',saveAttempted=True)
                write_json(path,record)
                restarted=Application(temp)
                with patch('paperstudio.server.availability',return_value={'available':False}):
                    recovered=restarted.start_job(SAMPLE,request_id)
                self.assertEqual(recovered['stage'],'interrupted')
                self.assertTrue(recovered['saveAttempted'])
                spawn.assert_called_once()

    def test_xiumi_concurrent_same_request_starts_exactly_one_worker(self):
        from concurrent.futures import ThreadPoolExecutor
        class FakeProcess:
            def poll(self):return None
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            app=Application(temp);request_id=uuid.uuid4().hex
            with patch('paperstudio.server.availability',return_value={'available':True}),patch('paperstudio.server.subprocess.Popen',return_value=FakeProcess()) as spawn:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    ids=list(executor.map(lambda _:app.start_job(SAMPLE,request_id)['id'],range(2)))
                self.assertEqual(ids,[request_id,request_id]);spawn.assert_called_once()

    def test_xiumi_history_does_not_spawn_and_excludes_body_and_invalid_links(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            valid=uuid.uuid4().hex
            write_json(Path(temp)/'jobs'/valid/'status.json',{'id':valid,'stage':'uncertain','title':'测试稿','createdAt':'2026-11-01','draftUrl':'https://evil.example/#/paper/for/123','private':'not-returned'})
            bad=Path(temp)/'jobs'/('f'*32)/'status.json';bad.parent.mkdir(parents=True);bad.write_text('broken',encoding='utf-8')
            with patch('paperstudio.server.subprocess.Popen') as spawn:
                app=Application(temp);records=app.job_list()
                self.assertEqual(len(records),1);self.assertEqual(records[0]['title'],'测试稿')
                self.assertIsNone(records[0]['draftUrl']);self.assertNotIn('private',records[0]);self.assertNotIn('document',records[0])
                spawn.assert_not_called()

    def test_old_login_timeout_is_explained_without_mutating_the_record(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            job_id=uuid.uuid4().hex;folder=Path(temp)/'jobs'/job_id
            original={'id':job_id,'stage':'failed','message':'接入未完成（TimeoutError）','saveAttempted':False}
            write_json(folder/'status.json',original)
            (folder/'worker.log').write_text('File "paperstudio/bridge.py", in run_job\n  raise TimeoutError("login wait")',encoding='utf-8')
            app=Application(temp);record=app.job(job_id)
            self.assertEqual(record['errorCode'],'login_timeout')
            self.assertEqual(record['failedStage'],'waiting_login')
            self.assertEqual(json.loads((folder/'status.json').read_text(encoding='utf-8')),original)
            (folder/'worker.log').write_text('TimeoutError in unrelated upload',encoding='utf-8')
            self.assertNotIn('errorCode',app.job(job_id))

    def test_xiumi_http_request_id_and_history_routes(self):
        class FakeProcess:
            def poll(self):return 0
        request_id=uuid.uuid4().hex
        data={'destination':'new_draft','document':SAMPLE,'requestId':request_id}
        with patch('paperstudio.server.availability',return_value={'available':True}),patch('paperstudio.server.subprocess.Popen',return_value=FakeProcess()) as spawn:
            status,body,_=self.request('POST','/api/xiumi/jobs',data)
            self.assertEqual(status,202)
            status,body,_=self.request('POST','/api/xiumi/jobs',data)
            self.assertEqual(status,202);self.assertEqual(json.loads(body)['id'],request_id)
            spawn.assert_called_once()
        status,body,_=self.request('GET','/api/xiumi/jobs')
        self.assertEqual(status,200);self.assertTrue(any(r['id']==request_id for r in json.loads(body)))
        status,body,_=self.request('POST','/api/xiumi/jobs',{**data,'requestId':'../bad'})
        self.assertEqual(status,400)


if __name__ == '__main__':
    unittest.main()
