import copy
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from paperstudio.ai import AIError, AIService, apply_plan, endpoint, model_prompt, request_plan, safe_config
from paperstudio.document import SAMPLE, normalize, render, standalone
from paperstudio.storage import write_json

TEMP=Path(__file__).resolve().parents[1]/'artifacts'/'test-tmp'
TEMP.mkdir(parents=True,exist_ok=True)
PLAN={'theme':'doocs-classic','fontSize':15,'lineHeight':2,'blockSpacing':.75,
      'design':{'font':'serif','heading':'center','quote':'pull','image':'paper','align':'left','accent':'#714b40'},
      'edits':[{'index':0,'appearance':'lead'}],'notes':['让开篇更松，标题轻一些，原文引语独立呈现。']}
ENV={'PAPER_AI_API_KEY':'','PAPER_AI_MODEL':'','PAPER_AI_BASE_URL':'https://api.example.com/v1'}


class AITests(unittest.TestCase):
    def service(self,root):
        with patch.dict(os.environ,ENV):return AIService(Path(root)/'ai-jobs')

    def config(self):return safe_config({'baseUrl':'https://api.example.com/v1','model':'test-model','apiKey':'test-key-only-for-unit-tests'})

    def wait_job(self,service,job_id):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=service.get(job_id)
            if result['stage'] not in {'queued','generating'}:return result
            time.sleep(.01)
        self.fail('local test job did not finish')

    def test_model_prompt_contains_no_photo_bytes_paths_or_credentials(self):
        doc=copy.deepcopy(SAMPLE);doc['blocks'][1]['src']='data:image/jpeg;base64,/9j/SECRETPHOTO'
        output=json.dumps(model_prompt(doc,'排成采访'))
        for item in ['SECRETPHOTO','data:image','auditorium.jpg','test-key-only']:self.assertNotIn(item,output)
        self.assertIn('JSON',output)

    def test_plan_keeps_all_words_metadata_photos_and_order(self):
        source=normalize(SAMPLE);before=copy.deepcopy(source)
        result,checks,notes=apply_plan(source,copy.deepcopy(PLAN))
        self.assertEqual(source,before)
        for field in ['title','subtitle','author','column']:self.assertEqual(result[field],source[field])
        self.assertEqual([b['text'] for b in result['blocks']],[b['text'] for b in source['blocks']])
        self.assertEqual(result['blocks'][1]['src'],source['blocks'][1]['src'])
        self.assertTrue(checks['preserved']);self.assertEqual(checks['images'],1)
        self.assertEqual(result['blocks'][0]['appearance'],'lead')
        self.assertEqual(normalize(result)['aiLayout']['font'],'serif')
        output=standalone(result,{'/assets/auditorium.jpg':'data:image/jpeg;base64,TEST'})
        self.assertIn('data:image/jpeg;base64,TEST',output)
        self.assertIn('font-size:21px',output)
        self.assertIn('font-family:STSong',output.replace("'Songti SC',",''))

    def test_split_exact_original_with_quoted_excerpt_and_whitespace_preserved(self):
        source=normalize({**copy.deepcopy(SAMPLE),'blocks':[{'type':'paragraph','text':'开场。\n“这是原话。”\n收束。'}]})
        plan={**PLAN,'edits':[{'index':0,'segments':[{'role':'heading','text':'开场。'},{'role':'quote','text':'“这是原话。”'},{'role':'paragraph','text':'收束。'}]}]}
        result,checks,_=apply_plan(source,plan)
        self.assertEqual(''.join(b['text'] for b in result['blocks']),source['blocks'][0]['text'])
        self.assertEqual([b['type'] for b in result['blocks']],['heading','quote','paragraph'])
        self.assertEqual(checks['splitCount'],2)

    def test_changed_omitted_reordered_words_and_invented_quote_are_rejected(self):
        source=normalize({**copy.deepcopy(SAMPLE),'blocks':[{'type':'paragraph','text':'甲段。乙段。'}]})
        for fragments in [[{'text':'改写。'}],[{'text':'甲段。'}],[{'text':'乙段。'},{'text':'甲段。'}],[{'text':'甲段。乙段。','role':'quote'}]]:
            plan={**PLAN,'edits':[{'index':0,'segments':fragments}]}
            with self.subTest(parts=fragments),self.assertRaises(AIError):apply_plan(source,plan)

    def test_image_split_and_duplicate_or_out_of_range_edits_are_rejected(self):
        for edits in [[{'index':1,'segments':[{'text':'x'}]}],[{'index':999}],[{'index':0},{'index':0}],[{'index':True}]]:
            with self.subTest(edits=edits),self.assertRaises(AIError):apply_plan(normalize(SAMPLE),{**PLAN,'edits':edits})

    def test_arbitrary_css_and_model_markup_do_not_execute(self):
        plan=copy.deepcopy(PLAN);plan['design'].update(accent='url(evil)',font='<script>',css='position:fixed')
        source=normalize({**copy.deepcopy(SAMPLE),'title':'<script>unsafe</script>'})
        result,_,_=apply_plan(source,plan);output=render(result)
        self.assertNotIn('<script>',output);self.assertNotIn('position:fixed',output);self.assertNotIn('evil',output)
        self.assertIn('&lt;script&gt;',output)

    def test_heading_quote_and_image_treatments_change_structure_not_just_color(self):
        a=copy.deepcopy(PLAN);b=copy.deepcopy(PLAN)
        a['design'].update(heading='rule',quote='line',image='plain')
        b['design'].update(heading='center',quote='pull',image='bleed')
        left=BeautifulSoup(render(apply_plan(normalize(SAMPLE),a)[0]),'html.parser')
        right=BeautifulSoup(render(apply_plan(normalize(SAMPLE),b)[0]),'html.parser')
        self.assertIn('border-left:3px',left.h2['style']);self.assertIn('text-align:center',right.h2['style'])
        self.assertIn('border:0;border-left:2px',left.blockquote['style'])
        self.assertIn('font-size:21px',right.blockquote['style'])
        self.assertIn('margin:1.5em -22px',right.figure['style'])

    def test_keys_are_not_reused_for_a_different_host_and_can_be_cleared(self):
        first=self.config()
        self.assertEqual(safe_config({'model':'other'},first)['apiKey'],first['apiKey'])
        self.assertEqual(safe_config({'baseUrl':'https://other.example/v1'},first)['apiKey'],'')
        self.assertEqual(safe_config({'clearKey':True},first)['apiKey'],'')
        for url in ['http://example.com/v1','https://localhost/v1','https://127.0.0.1/v1','https://user:pw@example.com/v1','https://example.com/v1?key=x']:
            with self.assertRaises(ValueError):endpoint(url)

    def test_unconfigured_service_does_not_create_jobs_or_call_model(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as root:
            service=self.service(root)
            with patch('paperstudio.ai.request_plan') as model:
                with self.assertRaises(AIError):service.start(SAMPLE,'人物采访')
                model.assert_not_called()
            self.assertFalse(service.status()['configured'])
            self.assertEqual(list((Path(root)/'ai-jobs').glob('*')),[])

    def test_async_job_preserves_original_drafts_and_never_persists_key(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as root:
            service=self.service(root);config=self.config();status=service.configure(config)
            self.assertNotIn('apiKey',status);self.assertNotIn(config['apiKey'],json.dumps(status))
            source=copy.deepcopy(SAMPLE)
            with patch('paperstudio.ai.request_plan',return_value=(copy.deepcopy(PLAN),{'total_tokens':321})) as model:
                job=service.start(source,'做成采访专刊');ready=self.wait_job(service,job['id']);model.assert_called_once()
            self.assertEqual(ready['stage'],'ready');self.assertTrue(ready['checks']['preserved'])
            self.assertEqual(source,SAMPLE)
            for path in Path(root).rglob('*'):
                if path.is_file():self.assertNotIn(config['apiKey'],path.read_text(encoding='utf-8'))
            self.assertFalse((Path(root)/'drafts').exists())

    def test_job_failure_is_explicit_without_model_retry_or_result(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as root:
            service=self.service(root);service.configure(self.config())
            with patch('paperstudio.ai.request_plan',side_effect=AIError('测试网络失败','network')) as model:
                failed=self.wait_job(service,service.start(SAMPLE,'采访')['id']);model.assert_called_once()
            self.assertEqual(failed['stage'],'failed');self.assertNotIn('document',failed)

    def test_concurrent_requests_are_blocked_and_restart_does_not_resend(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as root:
            service=self.service(root);service.configure(self.config());release=threading.Event()
            def wait_model(*args):release.wait(2);return copy.deepcopy(PLAN),{}
            with patch('paperstudio.ai.request_plan',side_effect=wait_model) as model:
                job=service.start(SAMPLE,'采访')
                with self.assertRaises(AIError):service.start(SAMPLE,'重复点击')
                release.set();self.wait_job(service,job['id']);model.assert_called_once()
            unfinished='a'*32;write_json(Path(root)/'ai-jobs'/unfinished/'status.json',{'id':unfinished,'stage':'generating'})
            with patch('paperstudio.ai.request_plan') as model:
                restarted=self.service(root);model.assert_not_called()
            self.assertEqual(restarted.get(unfinished)['stage'],'interrupted')

    def test_same_request_id_recovers_a_job_without_another_model_request(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as root:
            service=self.service(root)
            service.configure(self.config())
            release=threading.Event()
            request_id='b'*32

            def wait_model(*args):
                release.wait(2)
                return copy.deepcopy(PLAN), {}

            with patch('paperstudio.ai.request_plan',side_effect=wait_model) as model:
                try:
                    queued=service.start(SAMPLE,'采访',request_id)
                    self.assertEqual(queued['id'],request_id)
                    self.assertEqual(service.start(SAMPLE,' 采访 ',request_id)['id'],request_id)
                    with self.assertRaises(AIError) as conflict:
                        service.start(SAMPLE,'不同的要求',request_id)
                    self.assertEqual(conflict.exception.code,'request_conflict')
                finally:
                    release.set()
                ready=self.wait_job(service,request_id)
                self.assertEqual(service.start(SAMPLE,'采访',request_id),ready)
                restarted=self.service(root)
                self.assertFalse(restarted.status()['configured'])
                self.assertEqual(restarted.start(SAMPLE,'采访',request_id),ready)
                model.assert_called_once()

    def test_transport_sends_one_json_request_and_handles_bad_responses(self):
        responses=[];requests=[]
        class Connection:
            def __init__(self,*args):pass
            def request(self,*args,**kwargs):requests.append((args,kwargs))
            def close(self):pass
            def getresponse(self):return responses.pop(0)
        class Response:
            def __init__(self,data,status=200):self.status=status;self.buffer=io.BytesIO(json.dumps(data).encode())
            def read1(self,size):return self.buffer.read(size)
        envelope={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(PLAN)}}],'usage':{'total_tokens':50}}
        with patch('paperstudio.ai.public_addresses',return_value=['93.184.216.34']),patch('paperstudio.ai.PinnedHTTPS',Connection):
            responses.append(Response(envelope));plan,usage=request_plan(self.config(),normalize(SAMPLE),'采访')
            self.assertEqual(plan['theme'],PLAN['theme']);self.assertEqual(usage['total_tokens'],50)
            payload=json.loads(requests[-1][1]['body']);self.assertEqual(payload['response_format'],{'type':'json_object'})
            self.assertNotIn('/assets/',json.dumps(payload))
            for status,data in [(302,{}),(401,{}),(429,{}),(200,{'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}),(200,{'choices':[{'finish_reason':'stop','message':{'refusal':'no'}}]})]:
                responses.append(Response(data,status))
                with self.assertRaises(AIError):request_plan(self.config(),normalize(SAMPLE),'采访')


if __name__=='__main__':unittest.main()
