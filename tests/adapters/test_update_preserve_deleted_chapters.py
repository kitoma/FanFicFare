import io
import os
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from fanficfare.adapters.base_adapter import BaseSiteAdapter
from fanficfare.configurable import Configuration
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


def make_adapter(old_urls, site_chapters, tmp_path):
    configuration = Configuration(['example.com'], "EPUB", lightweight=True)
    configuration.read(os.path.join(
        os.path.dirname(__file__), '..', '..', 'fanficfare', 'defaults.ini'))
    personal = tmp_path / 'personal.ini'
    personal.write_text(
        '[defaults]\n'
        'update_preserve_deleted_chapters:true\n'
        'update_check_recent_chapters:0\n'
        'update_check_chapter_age_days:0\n'
        'update_reupload_detection:none\n'
        'update_reupload_similarity_threshold:0.8\n'
        'include_images:false\n')
    configuration.read(str(personal))

    adapter = FakeSiteAdapter(
        configuration, 'http://example.com/story/12345-test-story')
    adapter.site_chapters = site_chapters
    adapter.oldchaptersmap = {}
    for old_url in old_urls:
        adapter.oldchaptersmap[old_url] = BeautifulSoup(
            '<h3>%s</h3><p>old body %s</p>' % (old_url, old_url),
            'html.parser')
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