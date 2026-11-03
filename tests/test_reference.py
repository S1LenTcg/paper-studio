import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup

from paperstudio.document import SAMPLE, normalize, render, standalone
from paperstudio.reference import MAX_HTML, ReferenceError, analyze_article, fetch_article, public_addresses, validated_url
from paperstudio.reference_style import normalize_profile
from paperstudio.server import Application

FIXTURE = (Path(__file__).parent / 'fixtures' / 'reference_article.html').read_text(encoding='utf-8')
COMPOSITION = (Path(__file__).parent / 'fixtures' / 'reference_composition.html').read_text(encoding='utf-8')
TEMP = Path(__file__).resolve().parents[1] / 'artifacts' / 'test-tmp'
TEMP.mkdir(parents=True,exist_ok=True)


class ReferenceTests(unittest.TestCase):
    def test_keeps_intro_nested_cards_photo_frame_and_section_rhythm(self):
        profile=analyze_article(COMPOSITION)['profile']
        blocks=[{'type':'paragraph','text':'我写的导语'}, {'type':'heading','text':'我写的第一组'},
                {'type':'image','src':'/assets/sample.png','text':'自己的照片'}, {'type':'paragraph','text':'第一组自己的正文'},
                {'type':'heading','text':'我写的第二组'}, {'type':'paragraph','text':'第二组自己的正文'}]
        doc={**copy.deepcopy(SAMPLE),'title':'只作为文稿信息的标题','subtitle':'','column':'','author':'','blocks':blocks,'referenceStyle':profile}
        output=render(doc);dom=BeautifulSoup(output,'html.parser')
        self.assertTrue(profile['layout'])
        intro=dom.select_one('[data-block-index="0"]')
        self.assertTrue(any('background-color:#fff6f8' in n.get('style','') for n in intro.parents))
        heading=dom.select_one('[data-block-index="1"]')
        self.assertIn('text-align:center',heading['style'])
        self.assertTrue(any('width:94%' in n.get('style','') and '#ffffff' in n.get('style','') for n in heading.parents))
        self.assertTrue(any('background-color:#c70606' in n.get('style','') for n in heading.parents))
        image=dom.select_one('[data-block-index="2"]')
        self.assertTrue(any('border:2px solid #c70606' in n.get('style','') for n in image.parents))
        self.assertIn('transform:rotate(45deg)',output)
        self.assertEqual([int(n['data-block-index']) for n in dom.select('[data-block-index]')],list(range(6)))
        self.assertNotIn(doc['title'],output)
        self.assertNotIn('第一组参考章节',output)
        self.assertNotIn('source-photo.jpg',output)
        profile['showTitle']=True
        self.assertIn(doc['title'],render(doc))

    def test_same_palette_different_nesting_produces_different_composition(self):
        flat='<article style="color:#333333;background:#fff6f8"><h2 style="color:#c70606">参考标题</h2><p style="margin:32px 0">'+('普通正文，' * 20)+'</p><p>'+('另一段正文，' * 20)+'</p></article>'
        first=analyze_article(COMPOSITION)['profile'];second=analyze_article(flat)['profile']
        base={**copy.deepcopy(SAMPLE),'subtitle':'','author':'','blocks':[{'type':'heading','text':'自己的小标题'},{'type':'paragraph','text':'自己的正文'}]}
        a=render({**base,'referenceStyle':first});b=render({**base,'referenceStyle':second})
        self.assertIn('width:94%',a);self.assertNotIn('width:94%',b)
        self.assertNotEqual(a,b)

    def test_more_content_than_source_retains_every_block_in_own_order(self):
        profile=analyze_article(COMPOSITION)['profile']
        blocks=[]
        for i in range(12):
            blocks.extend([{'type':'heading','text':f'自己的章节{i}'},{'type':'paragraph','text':f'自己的正文{i}'},{'type':'quote','text':f'自己的引用{i}'}])
        doc={**copy.deepcopy(SAMPLE),'subtitle':'','author':'','blocks':blocks,'referenceStyle':profile}
        dom=BeautifulSoup(render(doc),'html.parser')
        self.assertEqual([n['data-block-index'] for n in dom.select('[data-block-index]')],[str(i) for i in range(len(blocks))])
        for i,block in enumerate(blocks):self.assertEqual(dom.select_one(f'[data-block-index="{i}"]').get_text(),block['text'])

    def test_layout_and_source_preview_remove_active_markup_and_remote_css(self):
        malicious=COMPOSITION.replace('padding:13px','padding:13px;background-image:url(https://evil.test/track);position:fixed').replace('我写的','unused')
        record=analyze_article(malicious)
        text=json.dumps(record['profile']['layout'])
        for unsafe in ['evil.test','position:fixed','alert(', '<script']:
            self.assertNotIn(unsafe,text);self.assertNotIn(unsafe,record['sourcePreview'])
        self.assertNotIn('src=',record['sourcePreview'])
        self.assertIn('原文图片位置',record['sourcePreview'])
        self.assertIn('aspect-ratio:',record['sourcePreview'])
        bad={'version':1,'tree':{'tag':'script','style':{},'children':[]}}
        with self.assertRaises(ValueError):normalize_profile({'layout':bad})

    def test_cached_old_reference_upgrades_without_network_or_draft_write(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as temp:
            app=Application(temp);record=app.read_reference({'html':COMPOSITION})
            saved=app.drafts.save(copy.deepcopy(SAMPLE));before=(app.drafts.root/(saved['id']+'.json')).read_bytes()
            path=Path(temp)/'references'/record['id']/'reference.json'
            old=json.loads(path.read_text(encoding='utf-8'));old['profile'].pop('layout');old.pop('sourcePreview');old.pop('layoutRevision')
            path.write_text(json.dumps(old),encoding='utf-8')
            with patch('paperstudio.server.fetch_article') as network:
                upgraded=app.reference(record['id']);network.assert_not_called()
            self.assertIn('layout',upgraded['profile']);self.assertIn('sourcePreview',upgraded)
            self.assertEqual(before,(app.drafts.root/(saved['id']+'.json')).read_bytes())

    def test_tiny_decorative_images_do_not_consume_photo_positions(self):
        source=COMPOSITION.replace('<figure style="margin:0px;border:', '<section style="width:11px"><img data-src="https://example.com/ornament.png" style="width:100%"></section><figure style="margin:0px;border:')
        record=analyze_article(source)
        self.assertEqual(record['layoutSummary']['roles']['image'],1)
        self.assertEqual(record['layoutSummary']['decorativeImagesOmitted'],1)
        self.assertEqual(record['sourcePreview'].count('原文图片位置'),1)

    def test_lazy_photo_placeholder_keeps_full_intrinsic_frame_width(self):
        source=COMPOSITION.replace('<figure style="margin:0px;border:', '<figure style="display:inline-block;max-width:100%;margin:0px;border:')
        dom=BeautifulSoup(analyze_article(source)['sourcePreview'],'html.parser')
        photo=dom.select_one('[data-kind="image"]')
        self.assertIn('width:100%',photo.parent['style'])
        self.assertIn('aspect-ratio:',str(photo))

    def test_extracts_role_styles_including_multiline_badge_background(self):
        result=analyze_article(FIXTURE,'https://example.com/article')
        spec=result['profile']
        self.assertEqual(result['title'],'测试采访标题')
        self.assertEqual(spec['paper'],'#fff0dd')
        self.assertEqual(spec['paragraph']['size'],16)
        self.assertEqual(spec['lineHeight'],2)
        self.assertTrue(spec['indent'])
        self.assertEqual(spec['heading']['background'],'#d81f1f')
        self.assertEqual(spec['heading']['color'],'#ffefdb')
        self.assertEqual(spec['quote']['borderLeft'],3)
        self.assertEqual(spec['cover']['align'],'center')
        self.assertEqual(spec['cover']['font'],'hand')
        self.assertEqual(spec['imageRadius'],8)
        self.assertEqual(spec['frameWidth'],2)

    def test_applies_style_to_own_content_not_reference_text_or_images(self):
        result=analyze_article(FIXTURE)
        doc=copy.deepcopy(SAMPLE);doc['referenceStyle']=result['profile'];doc['fontSize']=16
        output=render(doc)
        self.assertIn('data-layout="reference-structure"',output)
        self.assertIn('linear-gradient(90deg,rgb(255,86,66),rgb(216,31,31))',output)
        self.assertIn('font-size:16px',output)
        self.assertIn(SAMPLE['blocks'][0]['text'],output)
        self.assertNotIn('这是一篇用于验证样式提取的参考文章',output)
        self.assertNotIn('https://mmbiz.qpic.cn/example.jpg',output)
        self.assertNotIn('<script',output)

    def test_profile_survives_save_reload_and_export_without_refetch(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as temp:
            app=Application(temp)
            result=app.read_reference({'html':FIXTURE,'filename':'reference.html'})
            self.assertEqual(result['profile']['id'],result['id'])
            self.assertEqual(app.reference_list()[0]['title'],'测试采访标题')
            doc=copy.deepcopy(SAMPLE);doc['referenceStyle']=result['profile']
            saved=app.drafts.save(doc)
            loaded=app.drafts.get(saved['id'])['document']
            with patch('paperstudio.reference.fetch_article') as network:
                output=standalone(loaded,app.sample_url())
                network.assert_not_called()
            self.assertIn('data:image/jpeg;base64,',output)
            self.assertEqual(loaded['referenceStyle']['heading']['background'],'#d81f1f')
            self.assertEqual(app.reference(result['id'])['source'],'html')

    def test_login_verification_and_non_article_pages_are_not_success(self):
        for source in ['<html><body>环境异常，请完成验证。</body></html>','<html><body>这是一个导航页面</body></html>']:
            with self.assertRaises(ReferenceError):analyze_article(source)

    def test_unsafe_css_values_and_unknown_properties_are_discarded(self):
        spec=normalize_profile({'paper':'url(https://evil.test)','heading':{'color':'red;position:fixed','font':'url(evil)','size':'bad','borderColor':'expression(alert(1))','rawHtml':'<script>x</script>'},'rawCss':'@import url(x)'})
        serialized=json.dumps(spec)
        self.assertNotIn('evil',serialized)
        self.assertNotIn('<script',serialized)
        self.assertNotIn('expression',serialized)
        self.assertEqual(spec['paper'],'#ffffff')
        with self.assertRaises(ValueError):normalize_profile({'id':'../../source.html'})

    def test_invalid_urls_and_private_dns_are_rejected(self):
        for url in ['file:///etc/passwd','http://example.com','https://localhost/x','https://127.0.0.1/x','https://[::1]/','https://user:password@example.com/x','https://example.com:8443/x','https://example.com/\r\nInjected: x']:
            with self.subTest(url=url),self.assertRaises(ReferenceError):validated_url(url)
        with patch('paperstudio.reference.socket.getaddrinfo',return_value=[(2,1,6,'',('10.0.0.1',443))]):
            with self.assertRaises(ReferenceError):public_addresses('public.example')
        self.assertIn('%E6%8E%A8%E6%96%87',validated_url('https://example.com/推文'))

    def test_redirect_to_private_target_is_not_followed(self):
        class Response:
            status=302
            def getheader(self,key,default=''):return 'https://127.0.0.1/private' if key=='Location' else default
        class Connection:
            def __init__(self,*args):pass
            def request(self,*args,**kwargs):pass
            def getresponse(self):return Response()
            def close(self):pass
        with patch('paperstudio.reference.public_addresses',return_value=['93.184.216.34']),patch('paperstudio.reference.PinnedHTTPS',Connection):
            with self.assertRaises(ReferenceError):fetch_article('https://example.com/post')

    def test_fetch_reads_public_html_and_enforces_size_limit(self):
        content=FIXTURE.encode()
        class Response:
            status=200
            def __init__(self):self.buffer=io.BytesIO(content)
            def getheader(self,key,default=''):return 'text/html; charset=utf-8' if key=='Content-Type' else default
            def read1(self,size):return self.buffer.read(size)
        class Connection:
            def __init__(self,*args):pass
            def request(self,*args,**kwargs):pass
            def getresponse(self):return Response()
            def close(self):pass
        with patch('paperstudio.reference.public_addresses',return_value=['93.184.216.34']),patch('paperstudio.reference.PinnedHTTPS',Connection):
            source,url=fetch_article('https://example.com/post#fragment')
            self.assertEqual(source,FIXTURE)
            self.assertEqual(url,'https://example.com/post')
            content=b'x'*(MAX_HTML+1)
            with self.assertRaises(ReferenceError):fetch_article('https://example.com/post')


if __name__=='__main__':unittest.main()
