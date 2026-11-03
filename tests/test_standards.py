import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from paperstudio.standards import audit, content_identity, fingerprint, paper_id, validate_plan
from paperstudio.standards_worker import run_job
from paperstudio.server import Application
from paperstudio.storage import write_json

TMP = Path(__file__).resolve().parents[1] / 'artifacts' / 'test-tmp'
TMP.mkdir(parents=True, exist_ok=True)
URL = 'https://xiumi.us/studio/v5#/paper/for/123/cube/0'
COPY = 'https://xiumi.us/studio/v5#/paper/for/456/cube/0'


def sample():
    style = dict(font=14, line=1.8, letter=1, color='rgb(86, 86, 86)', align='left', indent=30, fontFamily='Arial, sans-serif')
    cells = []
    for i, text in enumerate(['这是一篇推文的第一段正文，保持全部文字。', '这是接下来的一段正文，不改写其中内容。']):
        cells.append(dict(id=str(i), text=text, html='<p>'+text+'</p>', style=copy.deepcopy(style), paragraphs=[dict(text=text, style=copy.deepcopy(style))], top=100+i*80, bottom=180+i*80, topLevel=True))
    return dict(url=URL, title='测试文章', cells=cells, images=[dict(src='https://img.xiumi.us/example.png', loaded=True)])


class StandardRulesTests(unittest.TestCase):
    def test_url_rejects_foreign_hosts_and_new_or_malformed_routes(self):
        self.assertEqual(paper_id(URL), '123')
        for url in ['http://xiumi.us/studio/v5#/paper/for/123', 'https://xiumi.us.evil.test/studio/v5#/paper/for/123', 'https://user@xiumi.us/studio/v5#/paper/for/123', 'https://xiumi.us/studio/v5#/paper/for/new', None]:
            with self.subTest(url=url), self.assertRaises(ValueError):paper_id(url)

    def test_detects_real_gap_and_not_already_inserted_blank(self):
        s=sample();r=audit(s)
        self.assertEqual([i['id'] for i in r['issues'] if i['automatic']], ['0:gap'])
        s['cells'][1]['top']+=25.2
        self.assertFalse(any(i['field']=='gap' for i in audit(s)['issues']))

    def test_heading_and_caption_are_preserved_even_with_different_formats(self):
        s=sample();s['cells'][0]['style'].update(font=26,align='center')
        s['cells'][1]['style'].update(font=12,align='center')
        r=audit(s)
        self.assertEqual([c['role'] for c in r['cells']],['heading','caption'])
        self.assertFalse(r['issues'])

    def test_footer_gets_its_own_rules_and_card_position_is_manual(self):
        s=sample();c=s['cells'][0];c['text']='张三 | 图片\n李四 | 文字\n王五 | 编辑\n赵六 | 校对';c['topLevel']=False
        r=audit(s)
        self.assertEqual(r['cells'][0]['role'],'footer')
        fields={i['field']:i for i in r['issues'] if i['cellId']=='0'}
        self.assertEqual(fields['line']['target'],1.5);self.assertEqual(fields['color']['target'],'rgb(160, 160, 160)')
        self.assertFalse(fields['outside']['automatic'])

    def test_unknown_short_text_requires_classification_and_mixed_media_never_autoformats(self):
        s=sample();c=s['cells'][0];c['text']='很短的图注文字';c['paragraphs'][0]['style']['indent']=0
        self.assertEqual(audit(s)['cells'][0]['role'],'unknown')
        c['embeddedMedia']=True
        self.assertFalse(any(i['automatic'] for i in audit(s,{'0':'body'})['issues'] if i['cellId']=='0'))

    def test_emphasis_colors_and_semantic_punctuation_stay_manual(self):
        s=sample();c=s['cells'][0];c['paragraphs'][0]['style']['color']='rgb(255, 0, 0)';c['emphasisColors']=['rgb(0, 0, 255)'];c['text']+='12:15开场。'
        issues={i['field']:i for i in audit(s)['issues'] if i['cellId']=='0'}
        self.assertFalse(issues['color']['automatic']);self.assertFalse(issues['punctuation']['automatic'])

    def test_fingerprint_ignores_viewport_scroll_but_detects_edits(self):
        s=sample();h=fingerprint(s);s['cells'][0]['top']-=300
        self.assertEqual(h,fingerprint(s));s['cells'][0]['html']+='修改'
        self.assertNotEqual(h,fingerprint(s))

    def test_content_identity_keeps_duplicates_and_order(self):
        s=sample();changed=copy.deepcopy(s);changed['cells'].append(copy.deepcopy(changed['cells'][0]))
        self.assertNotEqual(content_identity(s),content_identity(changed))
        changed=copy.deepcopy(s);changed['cells'].insert(1,{'text':'\n'})
        self.assertEqual(content_identity(s),content_identity(changed))

    def test_plan_rejects_stale_classification_and_bad_selection(self):
        r=audit(sample());plan=dict(fingerprint=r['fingerprint'],roles={'0':'body','1':'body'},selected=['0:gap'])
        self.assertEqual(validate_plan(r,plan)['selected'],['0:gap'])
        for change in [dict(fingerprint='old'),dict(roles={'0':'body'}),dict(selected=[{}]),dict(selected=[])]:
            with self.assertRaises(ValueError):validate_plan(r,{**plan,**change})


class StandardsServiceTests(unittest.TestCase):
    def test_idempotency_concurrency_and_restart_without_spawning_browser(self):
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            app=Application(temp);payload=dict(url=URL,requestId='a'*32,options={})
            with patch('paperstudio.standards_service.availability',return_value={'available':True}),patch('paperstudio.standards_service.subprocess.Popen',return_value=types.SimpleNamespace(poll=lambda:None)) as spawn:
                first=app.standards.start(payload)
                self.assertEqual(first,app.standards.start(payload));spawn.assert_called_once()
                with self.assertRaises(ValueError):app.standards.start({**payload,'requestId':'b'*32})
                with self.assertRaises(ValueError):app.standards.start({**payload,'url':COPY})
                restarted=Application(temp)
                self.assertEqual(restarted.standards.get('a'*32)['stage'],'interrupted');spawn.assert_called_once()

    def test_apply_only_accepts_automatic_selected_items_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            app=Application(temp);folder=app.standards.folder('a'*32);s=sample();r=audit(s)
            write_json(folder/'status.json',dict(id='a'*32,stage='awaiting_review'))
            write_json(folder/'snapshot.json',s);write_json(folder/'report.json',r)
            plan=dict(fingerprint=r['fingerprint'],roles={'0':'body','1':'body'},selected=['0:margins'],bodyColor='#565656')
            with self.assertRaises(ValueError):app.standards.apply('a'*32,plan)
            plan['selected']=['0:gap'];app.standards.apply('a'*32,plan);app.standards.apply('a'*32,plan)
            self.assertTrue(app.standards.get('a'*32)['planSubmitted'])
            with self.assertRaises(ValueError):app.standards.reclassify('a'*32,{'roles':plan['roles']})
            with self.assertRaises(ValueError):app.standards.cancel('a'*32)


class StandardsWorkerTests(unittest.TestCase):
    def exercise(self, stale=False, copy_lost=False, content_loss=False, persistence_loss=False, wrong_copy=False):
        source=sample();work=copy.deepcopy(source);driver=types.SimpleNamespace(current_url=URL)
        driver.set_page_load_timeout=Mock();driver.get=lambda url:setattr(driver,'current_url',url)
        driver.service=types.SimpleNamespace(stop=Mock());driver.quit=Mock();driver.save_screenshot=Mock()
        corrected=[];snapshot_count=[0]
        class Element:
            def click(self):pass
            def send_keys(self,*args):pass
        class FakeUI:
            Keys=types.SimpleNamespace(CONTROL='ctrl',TAB='tab')
            def __init__(self,d):pass
            def snapshot(self):
                snapshot_count[0]+=1
                if stale and snapshot_count[0]==2:
                    result=copy.deepcopy(source);result['cells'][0]['html']+='变更';return result
                result=copy.deepcopy(work)
                if driver.current_url==COPY:result['title']='测试文章（文字格式校正版）'
                if content_loss and corrected:result['cells'][0]['text']='丢字'
                return result
            def duplicate(self,source_id):
                if copy_lost:raise RuntimeError('lost response')
                driver.current_url=URL if wrong_copy else COPY;return driver.current_url
            def correct(self,index,cell,field,target):
                corrected.append(field)
                if field=='gap':work['cells'][1]['top']+=25.2
            def visible(self,selector):return Element()
        def refreshed():
            if persistence_loss:work['cells'][1]['top']=work['cells'][0]['bottom']
        driver.refresh=refreshed
        modules={n:types.ModuleType(n) for n in ['all_to_xiumi','selenium','selenium.webdriver','selenium.webdriver.chrome','selenium.webdriver.chrome.options','selenium.webdriver.chrome.service']}
        modules['all_to_xiumi'].xiumi_publish=types.SimpleNamespace()
        modules['selenium'].webdriver=types.SimpleNamespace(Chrome=lambda **kwargs:driver)
        modules['selenium.webdriver.chrome.options'].Options=lambda:types.SimpleNamespace(add_argument=Mock(),add_experimental_option=Mock())
        modules['selenium.webdriver.chrome.service'].Service=lambda **kwargs:None
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            folder=Path(temp);write_json(folder/'status.json',dict(id='a'*32,stage='queued',sourceUrl=URL,copyAttempted=False,saveAttempted=False,options=dict(rememberLogin=False,leaveOpen=True)))
            def tick(_):
                if (folder/'report.json').exists() and not (folder/'plan.json').exists():
                    r=json.loads((folder/'report.json').read_text(encoding='utf-8'))
                    write_json(folder/'plan.json',dict(fingerprint=r['fingerprint'],roles={'0':'body','1':'body'},selected=['0:gap'],bodyColor='#565656'))
            with patch.dict('sys.modules',modules),patch('paperstudio.standards_worker.open_editor'),patch('paperstudio.standards_worker.XiumiTextUI',FakeUI),patch('paperstudio.standards_worker.time.sleep',side_effect=tick),patch('traceback.print_exc'):
                run_job(folder)
            result=json.loads((folder/'status.json').read_text(encoding='utf-8'))
            run_job(folder) # terminal tasks cannot be re-run, even manually.
        return result,corrected

    def test_success_requires_copy_and_reopen_persistence(self):
        result,corrected=self.exercise()
        self.assertEqual(result['stage'],'verified');self.assertEqual(result['draftUrl'],COPY);self.assertEqual(corrected,['gap'])

    def test_stale_source_does_not_create_copy(self):
        result,corrected=self.exercise(stale=True)
        self.assertEqual(result['stage'],'failed');self.assertFalse(result['copyAttempted']);self.assertFalse(corrected)

    def test_lost_copy_response_is_uncertain_and_not_retried(self):
        result,corrected=self.exercise(copy_lost=True)
        self.assertEqual(result['stage'],'uncertain');self.assertTrue(result['copyAttempted']);self.assertFalse(corrected)

    def test_original_route_can_never_be_corrected(self):
        result,corrected=self.exercise(wrong_copy=True)
        self.assertEqual(result['stage'],'failed');self.assertFalse(corrected);self.assertFalse(result['saveAttempted'])

    def test_content_loss_stops_save(self):
        result,_=self.exercise(content_loss=True)
        self.assertEqual(result['stage'],'failed');self.assertFalse(result['saveAttempted'])

    def test_persistence_loss_is_never_reported_as_verified(self):
        result,_=self.exercise(persistence_loss=True)
        self.assertEqual(result['stage'],'review_required');self.assertTrue(result['saveAttempted'])


if __name__=='__main__':unittest.main()
