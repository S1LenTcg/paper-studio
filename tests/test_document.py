import base64
import copy
import re
import unittest

from paperstudio.document import SAMPLE, THEMES, normalize, render, sections, standalone
from paperstudio.bridge import verify


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.doc = copy.deepcopy(SAMPLE)

    def test_all_themes_keep_every_text_fragment(self):
        for theme in THEMES:
            self.doc["theme"] = theme
            output = render(self.doc)
            for text in [self.doc["title"], self.doc["subtitle"], *[b["text"] for b in self.doc["blocks"]]]:
                self.assertIn(text.replace("\n", "<br>"), output)
            self.assertIn(THEMES[theme]["accent"], output)

    def test_typed_sections_preserve_order_and_inline_style(self):
        ss = sections(self.doc)
        self.assertEqual(len(ss), len(self.doc["blocks"]) + 1)
        self.assertEqual([s.kind for s in ss[1:]], [b["type"] for b in self.doc["blocks"]])
        self.assertEqual(ss[1].xiumi()["style"]["fontSize"], "15px")
        self.assertIn('style="', ss[1].html())

    def test_untrusted_text_is_escaped(self):
        self.doc["title"] = '<script>alert("x")</script>'
        self.doc["blocks"][0]["text"] = '<img src=x onerror=alert(1)>'
        output = standalone(self.doc)
        self.assertNotIn('<script>', output)
        self.assertNotIn('<img src=x', output)
        self.assertIn('&lt;script&gt;', output)

    def test_export_bundles_default_image(self):
        src = "data:image/png;base64,ABC"
        output = standalone(self.doc, src)
        self.assertIn(src, output)
        self.assertNotIn('/assets/sample.png', output)
        self.assertNotIn('/assets/auditorium.jpg', output)

    def test_layouts_stay_different_after_removing_all_colors(self):
        monochrome=[]
        for theme in THEMES:
            self.doc['theme']=theme
            output=render(self.doc)
            output=re.sub(r'#[0-9a-fA-F]{3,8}', '#000000', output)
            output=re.sub(r'data-layout="[^"]*"', '', output)
            monochrome.append(output)
        self.assertEqual(len(set(monochrome)),len(THEMES))

    def test_no_generated_english_labels_and_optional_column_is_escaped(self):
        self.doc['column']='<script>column</script>'
        output=render(self.doc)
        self.assertIn('&lt;script&gt;column&lt;/script&gt;',output)
        self.assertNotIn('<script>',output)
        for label in ['FIELD NOTES','LETTER /','CAMPUS /','— END —']:
            self.assertNotIn(label,output)
        del self.doc['column']
        self.assertEqual(normalize(self.doc)['column'],'')

    def test_invalid_images_and_paths_are_rejected(self):
        for src in ['https://example.com/tracker.png', 'file:///C:/test.png', '../private.png', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,AAAA']:
            self.doc["blocks"][1]["src"] = src
            with self.subTest(src=src), self.assertRaises(ValueError):
                normalize(self.doc)

    def test_valid_raster_image(self):
        self.doc["blocks"][1]["src"] = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nxxx').decode()
        self.assertTrue(normalize(self.doc)["blocks"][1]["src"].startswith('data:'))

    def test_invalid_models_rejected(self):
        for key, value in [('theme','missing'), ('fontSize',float('nan')), ('lineHeight',9), ('title',''), ('blocks',[]), ('blocks',[{'type':'script','text':'x'}])]:
            data=copy.deepcopy(SAMPLE);data[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize(data)

    def test_lost_text_or_unpersisted_image_cannot_pass_verification(self):
        doc = normalize(self.doc)
        snapshot = {'title':doc['title'].replace('\n',' '), 'text':' '.join([doc['title'],doc['subtitle'],*[b['text'] for b in doc['blocks']]]),
                    'editableCount':len(doc['blocks'])+1,'images':[{'src':'data:image/png;base64,x','loaded':True}]}
        result=verify(doc,snapshot)
        self.assertFalse(result['automaticPassed'])
        self.assertFalse(result['automatic']['imagesPersisted'])
        snapshot['images'][0]['src']='https://img.xiumi.us/example.png'
        result=verify(doc,snapshot)
        self.assertTrue(result['automaticPassed'])
        self.assertTrue(all(v=='pending' for v in result['manual'].values()))
        snapshot['text']='missing'
        self.assertFalse(verify(doc,snapshot)['automaticPassed'])


if __name__ == '__main__':
    unittest.main()
