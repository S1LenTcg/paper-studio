import copy
import unittest
from bs4 import BeautifulSoup

from paperstudio.document import SAMPLE, normalize, render, standalone
from paperstudio.open_themes import OPEN_THEMES
from paperstudio.reference import analyze_article
from paperstudio.reference_layout import draw_tree


class OpenThemeTests(unittest.TestCase):
    def test_upstream_themes_apply_distinct_shape_and_typography_not_just_color(self):
        outputs={}
        for theme in OPEN_THEMES:
            doc={**copy.deepcopy(SAMPLE),'theme':theme}
            output=render(doc);dom=BeautifulSoup(output,'html.parser');outputs[theme]=dom
            self.assertIn('background:#315e68',dom.h2['style'])
            self.assertEqual([int(n['data-block-index']) for n in dom.select('[data-block-index]')],list(range(len(doc['blocks']))))
            self.assertEqual(dom.select_one('blockquote > p')['style'].split('margin:')[-1].split(';')[0],'0')
            for unresolved in ['var(', 'calc(', 'color-mix(']:self.assertNotIn(unresolved,output)
        self.assertNotIn('border-radius',outputs['doocs-classic'].h2['style'])
        self.assertIn('border-radius:8px 24px 8px 24px',outputs['doocs-simple'].h2['style'])
        self.assertIn('box-shadow:0 4px 6px',outputs['doocs-grace'].h2['style'])

    def test_spacing_changes_vertical_rhythm_and_survives_json_normalization(self):
        doc={**copy.deepcopy(SAMPLE),'theme':'doocs-classic','blockSpacing':.5}
        tight=BeautifulSoup(render(normalize(doc)),'html.parser')
        doc['blockSpacing']=1.2
        loose=BeautifulSoup(render(normalize(doc)),'html.parser')
        self.assertNotEqual(tight.h2['style'],loose.h2['style'])
        self.assertNotEqual(tight.select_one('[data-block-index="0"]')['style'],loose.select_one('[data-block-index="0"]')['style'])
        self.assertEqual(normalize(doc)['blockSpacing'],1.2)

    def test_html_export_is_inlined_portable_and_escapes_own_content(self):
        for theme in OPEN_THEMES:
            doc={**copy.deepcopy(SAMPLE),'theme':theme,'title':'<script>unsafe</script>'}
            output=standalone(doc,{'/assets/auditorium.jpg':'data:image/jpeg;base64,ABC'})
            self.assertIn('data:image/jpeg;base64,ABC',output)
            self.assertNotIn('<script>',output)
            self.assertIn('&lt;script&gt;',output)
            self.assertNotIn('<link',output)
            self.assertIn('CC BY 2.0',output)

    def test_unfilled_question_groups_do_not_leave_trailing_ornaments(self):
        tree={'tag':'section','style':{},'children':[
            {'tag':'p','style':{},'slot':0,'role':'paragraph'},
            {'tag':'section','style':{'border-top':'1px solid #cc0000'},'children':[]},
            {'tag':'p','style':{},'slot':1,'role':'paragraph'}]}
        own=draw_tree(tree,{0:[{'type':'paragraph','text':'自己的开场'}]})[0]
        self.assertNotIn('border-top',own)
        filled=draw_tree(tree,{0:[{'text':'开场'}],1:[{'text':'后续内容'}]})[0]
        self.assertIn('border-top',filled)


if __name__=='__main__':unittest.main()
