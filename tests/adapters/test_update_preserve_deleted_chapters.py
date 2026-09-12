"""Offline tests for the incremental-update branch (edit detection,
appending new chapters, and preserving deleted chapters).

Everything is served by StagedSiteAdapter (a BaseSiteAdapter stub backed
by in-memory chapter lists/content) so no network fetching takes place.
"""
import io
import os
import re
import zipfile
import hashlib
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from fanficfare.adapters.base_adapter import BaseSiteAdapter
from fanficfare.configurable import Configuration
from fanficfare.epubutils import get_update_data
from fanficfare.htmlcleanup import stripHTML
from fanficfare.writers.writer_epub import EpubWriter as WriterFic


class FakeSiteAdapter(BaseSiteAdapter):
    SITE = 'example.com'

    @classmethod
    def getSiteDomain(cls):
        return cls.SITE

    @classmethod
    def getConfigSection(cls):
        return cls.SITE

    def getConfigSections(cls):
        return [cls.SITE]

    def getSiteURLPattern(self):
        return '^http://' + self.SITE

    def normalize_chapterurl(self, url):
        return url

    def extractChapterUrlsAndMetadata(self):
        self.story.setMetadata('storyId', '12345')
        self.story.setMetadata('title', 'Test Story')
        self.story.setMetadata('author', 'Test Author')
        self.story.setMetadata('authorId', '12345')
        self.story.setMetadata('authorUrl', 'http://example.com/user/profile/12345')
        self.story.setMetadata('langcode', 'en')
        self.story.setMetadata('publisher', 'example.com')
        self.story.setMetadata('datePublished', datetime(2020, 1, 1))
        self.story.setMetadata('dateUpdated', datetime(2020, 1, 1))
        self.story.setMetadata('status', 'In-Progress')
        self.story.setMetadata('description', 'A test story.')
        self.story.setMetadata('category', 'General')
        self.story.setMetadata('rating', 'General Audiences')
        self.story.setMetadata('words', '100')
        for title, url in self.site_chapters:
            self.add_chapter(title, url)

    def getChapterTextNum(self, url, index):
        return '<p>new chapter content for %s</p>' % url


def make_adapter(old_urls, site_chapters, tmp_path, include_images='false',
                 oldimgs=None, reupload_detection='none'):
    configuration = Configuration(['example.com'], "EPUB", lightweight=True)
    configuration.read(os.path.join(
        os.path.dirname(__file__), '..', '..', 'fanficfare', 'defaults.ini'))
    personal = tmp_path / 'personal.ini'
    personal.write_text(
        '[defaults]\n'
        'update_preserve_deleted_chapters:true\n'
        'update_check_recent_chapters:0\n'
        'update_check_chapter_age_days:0\n'
        'update_reupload_detection:%s\n'
        'update_reupload_similarity_threshold:0.8\n'
        'include_images:%s\n' % (reupload_detection, include_images))
    configuration.read(str(personal))

    adapter = FakeSiteAdapter(
        configuration, 'http://example.com/story/12345-test-story')
    adapter.site_chapters = site_chapters
    adapter.oldchaptersmap = {}
    for old_url in old_urls:
        adapter.oldchaptersmap[old_url] = BeautifulSoup(
            '<h3>%s</h3><p>old body %s</p>' % (old_url, old_url),
            'html.parser')
    adapter.oldchaptershashes = None
    adapter.oldchaptersdata = None
    adapter.oldimgs = oldimgs
    adapter.oldchapterhashes = {u: '0' * 64 for u in old_urls}
    adapter.oldchaptercheckdates = {u: '2020-01-01 00:00:00'
                                    for u in old_urls}
    return adapter


OLD_URLS = ['http://example.com/story/ch/1',
            'http://example.com/story/ch/2',
            'http://example.com/story/ch/3',
            'http://example.com/story/ch/4',
            'http://example.com/story/ch/5']

# Site now has B, D (surviving) plus new F. A, C, E were deleted.
SITE_CHAPTERS = [('B title', 'http://example.com/story/ch/2'),
                 ('D title', 'http://example.com/story/ch/4'),
                 ('F title', 'http://example.com/story/ch/6')]


def test_preserved_deleted_written_chapters_have_required_keys(tmp_path):
    adapter = make_adapter(OLD_URLS, SITE_CHAPTERS, tmp_path)
    adapter.getStory()

    urls = [ch['url'] for ch in adapter.story.chapters]
    assert urls == OLD_URLS + ['http://example.com/story/ch/6']

    # Regression: every chapter dict must carry the keys that
    # Story.getChapters() reads unconditionally (the real-world run
    # crashed here with KeyError: 'new').
    for expected_key in ('new', 'number', 'index04', 'index',
                         'origtitle', 'toctitle'):
        for ch in adapter.story.chapters:
            assert expected_key in ch, (expected_key, ch['url'])


def test_update_counters_preserve_no_reupload(tmp_path):
    adapter = make_adapter(OLD_URLS, SITE_CHAPTERS, tmp_path)
    adapter.getStory()

    # No reupload-detection: the one new chapter is a pure addition.
    assert adapter.story.chapter_written_count == 6
    assert adapter.story.chapter_added_count == 1
    assert adapter.story.chapter_replaced_count == 0


def test_update_counters_reupload_similarity(tmp_path, monkeypatch):
    # ch/1 is gone from the site, but its content is reuploaded under the
    # new ch/6 url. With similarity detection enabled and identical text
    # (Jaccard 1.0 >= 0.8) ch/6 must count as a replacement, not an
    # addition; ch/7 is a genuine new chapter.
    def similar_text(self, url, index):
        if url == 'http://example.com/story/ch/6':
            # Same markup as the old ch/1 soup, so the stripped text is
            # identical to the old chapter's and similarity is 1.0.
            return '<h3>http://example.com/story/ch/1</h3>' \
                   '<p>old body http://example.com/story/ch/1</p>'
        return '<p>new chapter content for %s</p>' % url

    monkeypatch.setattr(FakeSiteAdapter, 'getChapterTextNum', similar_text)

    old_urls = ['http://example.com/story/ch/1',
                'http://example.com/story/ch/2',
                'http://example.com/story/ch/3']
    site = [('B title', 'http://example.com/story/ch/2'),
            ('C title', 'http://example.com/story/ch/3'),
            ('F title', 'http://example.com/story/ch/6'),
            ('G title', 'http://example.com/story/ch/7')]
    adapter = make_adapter(old_urls, site, tmp_path,
                           reupload_detection='similarity')
    adapter.getStory()

    urls = [ch['url'] for ch in adapter.story.chapters]
    assert urls == ['http://example.com/story/ch/2',
                    'http://example.com/story/ch/3',
                    'http://example.com/story/ch/6',
                    'http://example.com/story/ch/7']
    assert adapter.story.chapter_written_count == 4
    assert adapter.story.chapter_added_count == 1
    assert adapter.story.chapter_replaced_count == 1


def test_getChapters_with_preserved_deleted_chapters(tmp_path):
    adapter = make_adapter(OLD_URLS, SITE_CHAPTERS, tmp_path)
    adapter.getStory()

    chapters = adapter.story.getChapters(fortoc=True)
    assert [c['url'] for c in chapters] == \
        OLD_URLS + ['http://example.com/story/ch/6']


def test_write_epub_with_preserved_deleted_chapters(tmp_path):
    adapter = make_adapter(OLD_URLS, SITE_CHAPTERS, tmp_path)
    adapter.getStory()

    writer = WriterFic(adapter.configuration, adapter)
    writer.writeStory(outstream=io.BytesIO())

    # Writing again (like an update run) must also succeed.
    writer = WriterFic(adapter.configuration, adapter)
    writer.writeStory(outstream=io.BytesIO())


def test_preserved_deleted_chapter_images_preserved(tmp_path):
    # A preserved (deleted-from-site) chapter that contains an image.
    # The real update path hands us the old soup with img src already
    # reset to longdesc (the original URL) by epubutils.get_update_data,
    # plus adapter.oldimgs holding the old epub's image bytes keyed by
    # that longdesc URL -- so re-processing must find the image in the
    # store and NOT try to re-download it.
    img_url = 'https://img.example.com/pic.jpg'
    old_chapters = {'http://example.com/story/ch/1':
                    '<h3>Chapter One</h3><p>old body</p>'
                    '<img alt="pic" src="%s" longdesc="%s"/>'
                    % (img_url, img_url)}
    old_imgs = {img_url: ('OEBPS/images/pic.jpg', b'\xff\xd8fakejpeg')}

    adapter = make_adapter(list(old_chapters), SITE_CHAPTERS, tmp_path,
                           include_images='true', oldimgs=old_imgs)
    adapter.oldchaptersmap = {u: BeautifulSoup(html, 'html.parser')
                              for u, html in old_chapters.items()}
    adapter.getStory()

    out = io.BytesIO()
    writer = WriterFic(adapter.configuration, adapter)
    writer.writeStory(outstream=out)

    import zipfile
    zf = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    chaps = [n for n in zf.namelist() if n.startswith('OEBPS/file')
             and n.endswith('.xhtml')]
    img_files = [n for n in zf.namelist() if n.startswith('OEBPS/images/')]
    assert img_files, 'preserved chapter image was not written to epub'
    assert any('<img' in zf.read(c).decode('utf-8', 'replace')
               for c in chaps), \
        'preserved chapter image tag was dropped from html'
    # src rewritten to the local stored file -- not a remote URL.
    preserved_body = zf.read(chaps[0]).decode('utf-8', 'replace')
    for c in chaps:
        body = zf.read(c).decode('utf-8', 'replace')
        if '<img' in body:
            preserved_body = body
            break
    assert 'src="images/' in preserved_body
    # longdesc keeps the original URL (by design), but src must point at
    # the local stored file -- not the remote URL.
    import re
    srcs = re.findall(r'src="([^"]+)"', preserved_body)
    assert srcs and all(s.startswith('images/') for s in srcs), \
        'img src must be rewritten to local stored file'


class StagedSiteAdapter(BaseSiteAdapter):
    SITE = 'example.com'

    @classmethod
    def getSiteDomain(cls):
        return cls.SITE

    @classmethod
    def getConfigSection(cls):
        return cls.SITE

    def getConfigSections(cls):
        return [cls.SITE]

    def getSiteURLPattern(self):
        return '^http://' + self.SITE

    def normalize_chapterurl(self, url):
        return url

    def extractChapterUrlsAndMetadata(self):
        self.story.setMetadata('storyId', '12345')
        self.story.setMetadata('title', 'Test Story')
        self.story.setMetadata('author', 'Test Author')
        self.story.setMetadata('authorId', '12345')
        self.story.setMetadata('authorUrl', 'http://example.com/user/profile/12345')
        self.story.setMetadata('langcode', 'en')
        self.story.setMetadata('publisher', 'example.com')
        self.story.setMetadata('datePublished', datetime(2020, 1, 1))
        self.story.setMetadata('dateUpdated', datetime(2020, 1, 1))
        self.story.setMetadata('status', 'In-Progress')
        self.story.setMetadata('description', 'A test story.')
        self.story.setMetadata('category', 'General')
        self.story.setMetadata('rating', 'General Audiences')
        self.story.setMetadata('words', '100')
        for title, url in self.site_chapters:
            self.add_chapter(title, url)

    def getChapterTextNum(self, url, index):
        return self.content[url]


STORY_URL = 'http://example.com/story/12345-test-story'
CH = 'http://example.com/story/ch/%d'


def staged_config(tmp_path):
    configuration = Configuration(['example.com'], 'EPUB', lightweight=True)
    configuration.read(os.path.join(
        os.path.dirname(__file__), '..', '..', 'fanficfare', 'defaults.ini'))
    personal = tmp_path / 'personal.ini'
    personal.write_text(
        '[defaults]\n'
        'update_preserve_deleted_chapters:true\n'
        'update_check_recent_chapters:5\n'
        'update_check_chapter_age_days:0\n'
        'update_check_sleep_time:0\n'
        'update_reupload_detection:none\n'
        'update_reupload_similarity_threshold:0.8\n'
        'include_images:false\n')
    configuration.read(str(personal))
    return configuration


def chapter_hash(html):
    text = stripHTML(BeautifulSoup(html, 'html.parser'))
    text = re.sub(r'\s+', ' ', text).strip()
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def chapters_for(content):
    return [('Chapter %d' % n, CH % n) for n in sorted(content)]


def staged_download(configuration, content):
    adapter = StagedSiteAdapter(configuration, STORY_URL)
    adapter.site_chapters = chapters_for(content)
    adapter.content = {CH % n: html for n, html in content.items()}
    adapter.getStory()
    out = io.BytesIO()
    WriterFic(adapter.configuration, adapter).writeStory(outstream=out)
    return out.getvalue()


def staged_update(configuration, working, content):
    adapter = StagedSiteAdapter(configuration, STORY_URL)
    adapter.site_chapters = chapters_for(content)
    adapter.content = {CH % n: html for n, html in content.items()}
    (adapter.oldchapters, adapter.oldimgs, adapter.oldcover,
     adapter.calibrebookmark, adapter.logfile, adapter.oldchaptersmap,
     adapter.oldchaptersdata, adapter.oldchapterhashes,
     adapter.oldchaptercheckdates) = get_update_data(io.BytesIO(working))[2:11]
    adapter.getStory()
    out = io.BytesIO()
    WriterFic(adapter.configuration, adapter).writeStory(outstream=out)
    return out.getvalue()


def manipulate_chapter2_text_and_hash(epub_bytes):
    zf = zipfile.ZipFile(io.BytesIO(epub_bytes))
    names = zf.namelist()
    ch2 = sorted(n for n in names
                 if re.match(r'^OEBPS/file\d+\.xhtml$', n))[1]
    data = zf.read(ch2).decode('utf-8')
    new_body = '<p>chapter 2: b [[LOCAL-EDIT]]</p>'
    data = data.replace('<p>chapter 2: b [[INIT]]</p>', new_body)
    data = re.sub(r'(<meta name="chapterhash" content=")[^"]*(")',
                  r'\1%s\2' % chapter_hash(new_body), data)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zo:
        for n in names:
            zo.writestr(n, data if n == ch2 else zf.read(n))
    return out.getvalue()


def read_chapters(epub_bytes):
    zf = zipfile.ZipFile(io.BytesIO(epub_bytes))
    out = []
    for n in sorted(n for n in zf.namelist()
                    if re.match(r'^OEBPS/file\d+\.xhtml$', n)):
        soup = BeautifulSoup(zf.read(n).decode('utf-8'), 'html.parser')
        url = soup.find('meta', attrs={'name': 'chapterurl'})['content']
        chash = soup.find('meta', attrs={'name': 'chapterhash'})['content']
        out.append((url, chash, stripHTML(soup.find('body'))))
    return out


"""
testcase scenario:

- initial download: site has chapters 1..3 ("a","b","c"), each carrying
  an [[INIT]] marker; epub downloaded and markers verified.
- first update: site now has 4 chapters -- 1 and 2 unchanged, 3 updated
  ([[UPD1]] marker), 4 brand new ([[NEW1]]).  The working copy is the
  initial epub with chapter 2's text *and* hash manipulated.  Expected:
  ch1 untouched (same content, same stored hash), ch2 restored to the
  initial version, ch3 replaced with the [[UPD1]] content (new hash),
  ch4 appended.
- second update: site deleted chapters 1 and 2 and added 8 more (5..12,
  each with [[UPD2]]); ch3 kept; ch4 updated ([[UPD2]] marker).  The
  working copy is the first-update epub, unmodified.  Expected: ch1 and
  ch2 preserved despite deletion, ch3 still present, ch4 re-written with
  the [[UPD2]] marker, ch5..12 appended.

  ch4's re-check in the second update is the epub-window case: the
  edit-check window is the last N chapters of the OLD EPUB in reading
  order (not the last N site chapters), so ch4 -- site index 1 with a
  10-chapter site -- is still re-checked because it is the last chapter
  of the 4-chapter epub.
"""
def test_staged_update_flow(tmp_path):
    config = staged_config(tmp_path)

    # initial download: 3 chapters, each marked [[INIT]]; hashes must
    # equal the hash of the served content.
    s0 = {1: '<p>chapter 1: a [[INIT]]</p>',
          2: '<p>chapter 2: b [[INIT]]</p>',
          3: '<p>chapter 3: c [[INIT]]</p>'}
    initial = staged_download(config, s0)
    got = read_chapters(initial)
    assert [u for u, _, _ in got] == [CH % n for n in (1, 2, 3)]
    for n, (_, chash, text) in zip((1, 2, 3), got):
        assert 'chapter %d' % n in text and '[[INIT]]' in text
        assert chash == chapter_hash(s0[n])
    original_hashes = {url: chash for url, chash, _ in got}

    # first update: site gains ch4 (new) and ch3 is edited ([[UPD1]]).
    # Working copy = initial epub with ch2's text AND hash manipulated.
    s1 = {1: s0[1],
          2: s0[2],
          3: '<p>chapter 3: c [[UPD1]]</p>',
          4: '<p>chapter 4: d [[NEW1]]</p>'}
    working = manipulate_chapter2_text_and_hash(initial)
    first = staged_update(config, working, s1)
    got = read_chapters(first)
    assert [u for u, _, _ in got] == [CH % n for n in (1, 2, 3, 4)]

    # ch1 untouched: same content, same stored hash as the original.
    u1, c1, t1 = got[0]
    assert u1 == CH % 1
    assert 'chapter 1: a [[INIT]]' in t1
    assert c1 == original_hashes[u1]

    # ch2 locally edited (text + hash) -> restored to the initial
    # version by the update (content and stored hash).
    u2, c2, t2 = got[1]
    assert u2 == CH % 2
    assert 'chapter 2: b [[INIT]]' in t2
    assert 'LOCAL-EDIT' not in t2
    assert c2 == chapter_hash(s1[2])

    # ch3 replaced with the [[UPD1]] content (new hash).
    u3, c3, t3 = got[2]
    assert u3 == CH % 3
    assert 'chapter 3: c [[UPD1]]' in t3
    assert c3 == chapter_hash(s1[3])

    # ch4 appended as a brand new chapter.
    u4, c4, t4 = got[3]
    assert u4 == CH % 4
    assert 'chapter 4: d [[NEW1]]' in t4
    assert c4 == chapter_hash(s1[4])

    # second update: site deletes ch1/ch2, edits ch4 ([[UPD2]]), adds
    # ch5..12 (each [[UPD2]]).  Working copy = first-update epub, no
    # manipulation.
    s2 = {3: s1[3],
          4: '<p>chapter 4: d [[UPD2]]</p>'}
    s2.update({n: '<p>chapter %d: %s [[UPD2]]</p>' % (n, chr(96 + n))
               for n in range(5, 13)})
    second = staged_update(config, first, s2)
    got = read_chapters(second)
    assert [u for u, _, _ in got] == [CH % n for n in range(1, 13)]
    for url, _, text in got:
        num = int(url.rsplit('/', 1)[1])
        if num in (1, 2):
            # preserved despite being deleted on the site.
            assert '[[INIT]]' in text
        elif num == 3:
            # still present, unchanged from the previous stage.
            assert '[[UPD1]]' in text
        elif num == 4:
            # re-checked via the epub-based recent window (site index 1
            # of 10, but last chapter of the 4-chapter epub) -> UPD2.
            assert '[[UPD2]]' in text
        else:
            # brand-new chapters 5..12.
            assert 'chapter %d:' % num in text and '[[UPD2]]' in text


def test_preserved_deleted_chapter_keeps_title(tmp_path):
    # Regression: a preserved (deleted-from-site) chapter must keep its
    # real title.  epubutils.get_update_data strips the leading
    # fff_chapter_title h3 from the stored soup, so the preserve path
    # must fall back to the <meta name="chaptertitle"> in
    # oldchaptersdata instead of the URL slug.  Without the fix the
    # preserved "Chapter 2" would come back as the slug "2".
    config = staged_config(tmp_path)

    s0 = {1: '<p>chapter 1: a [[INIT]]</p>',
          2: '<p>chapter 2: b [[INIT]]</p>',
          3: '<p>chapter 3: c [[INIT]]</p>'}
    initial = staged_download(config, s0)

    def chapter_body_num(epub_bytes, num):
        zf = zipfile.ZipFile(io.BytesIO(epub_bytes))
        for n in sorted(n for n in zf.namelist()
                        if re.match(r'^OEBPS/file\d+\.xhtml$', n)):
            data = zf.read(n).decode('utf-8')
            if '<meta name="chapterurl" content="%s"' % (CH % num) in data:
                return data
        return None

    assert '<h3 class="fff_chapter_title">Chapter 2</h3>' in \
        chapter_body_num(initial, 2)

    # Second update: site deletes ch1 and ch2 (keeps ch3, adds ch4).
    s1 = {3: s0[3], 4: '<p>chapter 4: d [[NEW1]]</p>'}
    updated = staged_update(config, initial, s1)

    # ch2 is preserved from the deleted chapter and keeps its title.
    body2 = chapter_body_num(updated, 2)
    assert body2 is not None, 'deleted chapter was not preserved'
    assert '<h3 class="fff_chapter_title">Chapter 2</h3>' in body2, \
        'preserved chapter title was slug-ified'
    assert '<meta name="chaptertitle" content="Chapter 2" />' in body2