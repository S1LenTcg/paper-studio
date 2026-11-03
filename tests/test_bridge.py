"""Exercise worker decisions with an entirely in-memory browser substitute.

These tests do not start Chrome or contact Xiumi. They verify the guards that
must run before remote writes and the difference between save and verification.
"""
import json
import copy
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from paperstudio.bridge import run_job, prepare_content, verify, draft_url, open_editor, BridgeError, JobCancelled
from paperstudio.document import SAMPLE, normalize
from paperstudio.storage import write_json
from bs4 import BeautifulSoup

TEST_TMP = Path(__file__).resolve().parents[1] / 'artifacts' / 'test-tmp'
TEST_TMP.mkdir(parents=True, exist_ok=True)


class BridgeTests(unittest.TestCase):
    def exercise(self, authenticated=True, saved=True, redirect_existing=False,
                 before=None, after=None, write_url=None, reopen_url=None, save_raises=False, theme=None, options=None):
        doc=normalize(SAMPLE)
        if theme:
            doc['theme']=theme
        class FakeElement:
            def is_displayed(self):return True
        class FakeDriver:
            current_url='about:blank'
            refreshed=False
            closed=False
            def set_page_load_timeout(self,value):pass
            def get(self,url):self.current_url='https://xiumi.us/studio/v5#/paper/for/999' if redirect_existing and '/paper/for/new' in url else url
            def find_elements(self,*args):return [FakeElement()]
            def refresh(self):
                self.refreshed=True
                if reopen_url:self.current_url=reopen_url
            def execute_script(self,*args):
                result={'title':doc['title'].replace('\n',' '),'text':' '.join([doc['title'],doc['subtitle'],*[b['text'] for b in doc['blocks']]]),
                        'editableCount':len(doc['blocks'])+1,'images':[{'src':'https://img.xiumi.us/test.png','loaded':True}]}
                result.update((after if self.refreshed else before) or {})
                return result
            def save_screenshot(self,path):pass
            def quit(self):self.closed=True
        driver=FakeDriver()
        driver.service=types.SimpleNamespace(stop=Mock())
        upstream=types.ModuleType('upstream')
        upstream._xiumi_login_state=Mock(return_value={'authenticated':authenticated})
        upstream._visible_login_links=Mock(return_value=[])
        upstream._wait_for_manual_login=Mock()
        def open_new(browser,*args):
            if not authenticated:upstream._wait_for_manual_login(browser,600)
            driver.get('https://xiumi.us/studio/v5#/paper/for/new')
        upstream._open_xiumi_editor=Mock(side_effect=open_new)
        upstream._fill_xiumi_fields=Mock()
        def fill(*args,**kwargs):
            if write_url:driver.current_url=write_url
            return ({'status':'skipped','uploaded':0,'total':0},True)
        upstream._fill_xiumi_body_then_images=Mock(side_effect=fill)
        upstream._mark_xiumi_document_dirty=Mock()
        def save(_):
            if saved:driver.current_url='https://xiumi.us/studio/v5#/paper/for/123'
            if save_raises:raise RuntimeError('simulated lost response')
        upstream._click_save=Mock(side_effect=save)
        class Options:
            def __init__(self):self.arguments=[];self.experimental_options={};driver.options=self
            def add_argument(self,value):self.arguments.append(value)
            def add_experimental_option(self,name,value):self.experimental_options[name]=value
        modules={name:types.ModuleType(name) for name in ['all_to_xiumi','selenium','selenium.webdriver','selenium.webdriver.chrome','selenium.webdriver.chrome.options','selenium.webdriver.chrome.service','selenium.webdriver.common','selenium.webdriver.common.by']}
        modules['all_to_xiumi'].xiumi_publish=upstream
        modules['selenium'].webdriver=modules['selenium.webdriver']
        modules['selenium.webdriver'].Chrome=Mock(return_value=driver)
        modules['selenium.webdriver.chrome.options'].Options=Options
        modules['selenium.webdriver.chrome.service'].Service=lambda **kwargs:None
        modules['selenium.webdriver.common.by'].By=types.SimpleNamespace(CSS_SELECTOR='css')
        clock=iter(range(1,2000))
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            folder=Path(temp)/'jobs'/('a'*32)
            write_json(folder/'document.json',doc)
            write_json(folder/'status.json',{'id':'a'*32,'stage':'queued','options':options or {}})
            with patch.dict('sys.modules',modules),patch('paperstudio.bridge.availability',return_value={'available':True}),patch('paperstudio.bridge.time.sleep'),patch('paperstudio.bridge.time.monotonic',side_effect=lambda:next(clock)),patch('traceback.print_exc'):
                run_job(folder)
            status=json.loads((folder/'status.json').read_text(encoding='utf-8'))
        if not (options or {}).get('leaveOpen'):
            self.assertTrue(driver.closed)
        return status,driver,upstream

    def test_worker_requires_login_before_writing(self):
        status,driver,upstream=self.exercise(authenticated=False)
        self.assertEqual(status['stage'],'failed')
        self.assertEqual(status['errorCode'],'login_timeout')
        self.assertEqual(status['failedStage'],'waiting_login')
        upstream._fill_xiumi_fields.assert_not_called()
        upstream._click_save.assert_not_called()

    def test_existing_draft_redirect_prevents_writing(self):
        status,driver,upstream=self.exercise(redirect_existing=True)
        self.assertEqual(status['stage'],'failed')
        upstream._fill_xiumi_fields.assert_not_called()
        upstream._click_save.assert_not_called()

    def test_new_url_without_numeric_id_is_uncertain_not_success(self):
        status,driver,upstream=self.exercise(saved=False)
        self.assertEqual(status['stage'],'uncertain')
        self.assertFalse(driver.refreshed)
        upstream._click_save.assert_called_once()

    def test_saved_draft_is_reopened_and_manual_checks_remain_pending(self):
        status,driver,upstream=self.exercise()
        self.assertEqual(status['stage'],'verified')
        self.assertTrue(driver.refreshed)
        self.assertEqual(status['verification']['manual']['independentImageReplacement'],'pending')
        upstream._click_save.assert_called_once()

    def test_missing_local_or_unloaded_photo_prevents_save_even_if_upstream_says_applied(self):
        for photos in ([],[{'src':'image-0.jpg','loaded':True}],
                       [{'src':'data:image/png;base64,ABC','loaded':True}],
                       [{'src':'https://img.xiumi.us/test.png','loaded':False}]):
            with self.subTest(photos=photos):
                status,driver,upstream=self.exercise(before={'images':photos})
                self.assertEqual(status['stage'],'failed')
                self.assertEqual(status['errorCode'],'preflight_failed')
                upstream._click_save.assert_not_called()
                self.assertFalse(driver.refreshed)

    def test_one_component_for_open_theme_prevents_save(self):
        status,_,upstream=self.exercise(theme='doocs-classic',before={'editableCount':1})
        self.assertEqual(status['stage'],'failed')
        self.assertFalse(status['preflight']['automatic']['editableBlocks'])
        upstream._click_save.assert_not_called()

    def test_redirect_after_writing_prevents_saving_existing_or_foreign_document(self):
        for url in ('https://xiumi.us/studio/v5#/paper/for/456',
                    'https://other.example/studio/v5#/paper/for/new'):
            status,_,upstream=self.exercise(write_url=url)
            self.assertEqual(status['stage'],'failed')
            upstream._click_save.assert_not_called()

    def test_saved_images_must_match_before_save_and_loaded_on_reopen(self):
        status,driver,upstream=self.exercise(after={'images':[{'src':'https://img.xiumi.us/wrong.png','loaded':True}]})
        self.assertEqual(status['stage'],'review_required')
        self.assertEqual(status['draftUrl'],'https://xiumi.us/studio/v5#/paper/for/123')
        self.assertFalse(status['verification']['automatic']['imageOrder'])
        upstream._click_save.assert_called_once()

    def test_save_exception_keeps_known_draft_link_and_does_not_retry(self):
        status,_,upstream=self.exercise(save_raises=True)
        self.assertEqual(status['stage'],'uncertain')
        self.assertTrue(status['saveAttempted'])
        self.assertIn('/paper/for/123',status['draftUrl'])
        upstream._click_save.assert_called_once()

    def test_reopen_of_different_draft_never_passes(self):
        status,_,upstream=self.exercise(reopen_url='https://xiumi.us/studio/v5#/paper/for/999')
        self.assertEqual(status['stage'],'uncertain')
        self.assertIn('/paper/for/123',status['draftUrl'])
        upstream._click_save.assert_called_once()

    def test_finished_or_save_attempted_worker_is_not_replayed(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            folder=Path(temp)
            for state in ({'stage':'verified'},{'stage':'saving'},{'stage':'queued','saveAttempted':True}):
                write_json(folder/'status.json',state)
                with patch('paperstudio.bridge.availability') as available:
                    run_job(folder)
                    available.assert_not_called()

    def test_opt_in_login_profile_and_detached_inspection_window(self):
        status,driver,upstream=self.exercise(options={'rememberLogin':True,'leaveOpen':True})
        self.assertTrue(status['browserLeftOpen'])
        self.assertFalse(driver.closed)
        driver.service.stop.assert_called_once()
        self.assertTrue(any(a.startswith('--user-data-dir=') and a.endswith(str(Path('browser/xiumi'))) for a in driver.options.arguments))
        self.assertNotIn('--ignore-certificate-errors',driver.options.arguments)
        self.assertTrue(driver.options.experimental_options['detach'])
        upstream._open_xiumi_editor.assert_called_once()

    def test_default_worker_does_not_reuse_browser_login(self):
        _,driver,upstream=self.exercise()
        self.assertFalse(any(a.startswith('--user-data-dir=') for a in driver.options.arguments))


class LoginFlowTests(unittest.TestCase):
    def test_installed_upstream_navigation_core_uses_the_app_login_wait(self):
        # Execute only the installed navigation functions, with in-memory UI
        # dependencies. Do not import its config/.env or start a real browser.
        import ast
        import re
        from paperstudio.bridge import source_path
        source=source_path()/'all_to_xiumi'/'xiumi_publish.py'
        if not source.is_file():self.skipTest('optional upstream checkout not installed')
        names={'_open_xiumi_editor','_open_xiumi_editor_from_my_xiumi','_wait_for_xiumi_login_on_home',
               '_wait_until_xiumi_home_settled','_is_xiumi_editor_url','_ensure_xiumi_logged_in_before_edit'}
        tree=ast.parse(source.read_text(encoding='utf-8'))
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual(len(nodes),len(names))
        upstream=types.ModuleType('upstream_navigation_test')
        class Driver:
            current_url='about:blank';authenticated=False;visited=[]
            def get(self,url):self.current_url=url;self.visited.append(url)
            def execute_script(self,*args):return 'complete'
        driver=Driver()
        link=Mock();link.is_displayed.return_value=True
        link.click.side_effect=lambda:setattr(driver,'authenticated',True)
        class Wait:
            def __init__(self,browser,timeout):self.browser=browser
            def until(self,fn):assert fn(self.browser)
        def new_paper(*args,**kwargs):
            driver.current_url='https://xiumi.us/studio/v5#/paper/for/new/cube/0'
            return {'clicked':True}
        upstream.__dict__.update(re=re,time=types.SimpleNamespace(time=lambda:0,sleep=lambda _:None),WebDriverWait=Wait,
            _xiumi_login_state=lambda b:{'authenticated':b.authenticated},_log_xiumi_debug=Mock(),
            _page_excerpt=lambda *args:'测试首页',
            _visible_login_links=lambda b:[link],_wait_for_manual_login=Mock(),
            _click_xiumi_ui_candidates=Mock(side_effect=new_paper),_dismiss_xiumi_recover_dialog=Mock(),_wait_editor_ready=Mock())
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),upstream.__dict__)
        phases=[]
        open_editor(upstream,driver,lambda stage,*args,**kw:phases.append(stage),lambda:False)
        link.click.assert_called_once()
        self.assertIn('waiting_login',phases)
        self.assertEqual(driver.visited,['https://xiumi.us/studio/v5?lang=zh_CN#/']*2)
        self.assertTrue(draft_url(driver.current_url,new=True))

    def test_upstream_new_paper_flow_opens_login_and_resumes_after_manual_login(self):
        driver=types.SimpleNamespace(current_url='https://xiumi.us/studio/v5#/paper/for/new')
        link=Mock();link.is_displayed.return_value=True
        original=Mock()
        upstream=types.SimpleNamespace(_wait_for_manual_login=original,_visible_login_links=Mock(return_value=[link]),
                                       _xiumi_login_state=Mock(return_value={'authenticated':True}))
        def enter(browser,home,login_timeout,ready_timeout):
            self.assertEqual(home,'https://xiumi.us/studio/v5?lang=zh_CN#/')
            self.assertTrue(upstream._wait_for_manual_login(browser,login_timeout))
        upstream._open_xiumi_editor=Mock(side_effect=enter)
        updates=[]
        open_editor(upstream,driver,lambda stage,message,**extra:updates.append((stage,message,extra)),lambda:False)
        link.click.assert_called_once();original.assert_not_called()
        self.assertIs(upstream._wait_for_manual_login,original)
        self.assertTrue(any(stage=='waiting_login' and extra.get('loginDeadline') for stage,_,extra in updates))

    def test_login_cancel_stops_without_writing_and_restores_callback(self):
        original=Mock();checks=iter([False,True])
        upstream=types.SimpleNamespace(_wait_for_manual_login=original,_visible_login_links=lambda _:[],
                                      _xiumi_login_state=lambda _:{'authenticated':False})
        upstream._open_xiumi_editor=lambda driver,*_:upstream._wait_for_manual_login(driver,600)
        with self.assertRaises(JobCancelled):
            open_editor(upstream,object(),lambda *_,**kw:None,lambda:next(checks))
        self.assertIs(upstream._wait_for_manual_login,original)


class PayloadTests(unittest.TestCase):
    def test_complex_reference_structure_stays_intact_with_honest_component_count(self):
        from paperstudio.reference import analyze_article
        source=(Path(__file__).parent/'fixtures'/'reference_composition.html').read_text(encoding='utf-8')
        profile=analyze_article(source)['profile']
        doc=normalize({**SAMPLE,'referenceStyle':profile})
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            content,manifest=prepare_content(doc,Path(temp))
            self.assertEqual(manifest['components'],1)
            self.assertTrue(manifest['warnings'])
            self.assertEqual(len(BeautifulSoup(content,'html.parser').find_all('img')),1)

    def test_all_open_themes_keep_source_blocks_photos_and_captions_separate(self):
        for theme in ('doocs-classic','doocs-simple','doocs-grace'):
            doc=normalize({**SAMPLE,'theme':theme})
            # Two photos, including identical inputs, need distinct stable paths.
            doc['blocks'].append(copy.deepcopy(doc['blocks'][1]))
            doc['blocks'].append({'type':'divider','text':'结束标记'})
            with self.subTest(theme=theme),tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
                content,manifest=prepare_content(doc,Path(temp))
                soup=BeautifulSoup(content,'html.parser')
                self.assertEqual(manifest['components'],len(doc['blocks'])+1)
                self.assertEqual(manifest['images'],2)
                self.assertEqual(len(soup.find_all('section',recursive=False)),len(doc['blocks'])+1)
                self.assertEqual([i['src'] for i in soup.find_all('img')],['image-0.jpg','image-1.jpg'])
                self.assertEqual((Path(temp)/'image-0.jpg').read_bytes(),(Path(temp)/'image-1.jpg').read_bytes())
                for block in doc['blocks']:
                    self.assertIn(''.join(block['text'].split()),''.join(soup.get_text().split()))
                self.assertNotIn('/assets/',content)
                self.assertNotIn('data:image/',content)
                parts=soup.find_all('section',recursive=False)
                self.assertIn('padding:26px 22px 0 22px',parts[0]['style'])
                self.assertIn('padding:0 22px 34px 22px',parts[-1]['style'])

    def test_repeated_or_reordered_paragraphs_cannot_pass_content_check(self):
        doc=normalize({**SAMPLE,'subtitle':'','blocks':[{'type':'paragraph','text':'重复内容'},
            {'type':'paragraph','text':'中间内容'},{'type':'paragraph','text':'重复内容'}]})
        snap={'title':doc['title'].replace('\n',' '),'images':[],'editableCount':4}
        for text in ('重复内容中间内容','中间内容重复内容重复内容'):
            self.assertFalse(verify(doc,{**snap,'text':text})['automatic']['text'])
        self.assertTrue(verify(doc,{**snap,'text':'重复内容中间内容重复内容'})['automaticPassed'])

    def test_body_title_is_optional_and_zero_photos_are_valid(self):
        doc=normalize({**SAMPLE,'subtitle':'','blocks':[{'type':'paragraph','text':'只有正文'}]})
        snap={'title':doc['title'].replace('\n',' '),'text':'只有正文','images':[],'editableCount':1}
        self.assertTrue(verify(doc,snap,expected_components=1)['automaticPassed'])

    def test_image_order_and_exact_count_are_verified(self):
        doc=normalize(SAMPLE)
        doc['blocks'].append(copy.deepcopy(doc['blocks'][1]))
        snap={'title':doc['title'].replace('\n',' '),'text':' '.join([doc['title'],doc['subtitle'],*[b['text'] for b in doc['blocks']]]),
              'editableCount':len(doc['blocks'])+1,
              'images':[{'src':'https://img.xiumi.us/1.jpg','loaded':True},{'src':'https://img.xiumi.us/2.jpg','loaded':True}]}
        self.assertTrue(verify(doc,snap,baseline_images=[i['src'] for i in snap['images']])['automaticPassed'])
        self.assertFalse(verify(doc,snap,baseline_images=[i['src'] for i in reversed(snap['images'])])['automaticPassed'])
        snap['images'].append({'src':'https://img.xiumi.us/extra.jpg','loaded':True})
        self.assertFalse(verify(doc,snap)['automaticPassed'])

    def test_xiumi_display_transform_does_not_change_image_identity(self):
        doc=normalize(SAMPLE)
        snap={'title':doc['title'].replace('\n',' '),'text':' '.join([doc['title'],doc['subtitle'],*[b['text'] for b in doc['blocks']]]),
              'editableCount':len(doc['blocks'])+1,'images':[{'src':'https://img.xiumi.us/test.jpg?x-oss-process=style/xmwebp','loaded':True}]}
        self.assertTrue(verify(doc,snap,baseline_images=['https://img.xiumi.us/test.jpg'])['automaticPassed'])
        self.assertFalse(verify(doc,snap,baseline_images=['https://img.xiumi.us/other.jpg'])['automaticPassed'])

    def test_editor_urls_are_exact_and_same_origin(self):
        self.assertTrue(draft_url('https://xiumi.us/studio/v5?lang=zh_CN#/paper/for/123/cube/0'))
        self.assertTrue(draft_url('https://xiumi.us/studio/v5#/paper/for/new',new=True))
        for url in ('https://xiumi.us.evil.example/studio/v5#/paper/for/123',
                    'https://xiumi.us@evil.example/studio/v5#/paper/for/123',
                    'http://xiumi.us/studio/v5#/paper/for/123',
                    'https://xiumi.us/elsewhere#/paper/for/123',
                    'https://xiumi.us/studio/v5#/paper/for/123bad',
                    'https://xiumi.us/studio/v5#/paper/for/new-bad'):
            self.assertFalse(draft_url(url))
            self.assertFalse(draft_url(url,new=True))


if __name__ == '__main__':
    unittest.main()
