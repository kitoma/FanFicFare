# -*- coding: utf-8 -*-

import datetime
import os

import pytest

import fanficfare
from fanficfare.configurable import Configuration
from fanficfare.story import Story
from fanficfare.writers.writer_epub import EpubWriter

from epub_reconstruct.deconstruct import deconstruct
from epub_reconstruct.reconstruct import (
    DEFAULTS_HASH_META_FREE_START, StubAdapter, reconstruct,
)
from epub_reconstruct.verify import verify

DEFAULTS = os.path.join(os.path.dirname(fanficfare.__file__),
                        'defaults.ini')

STORY_URL = 'https://www.example.com/fiction/12345'


def _base_configuration(legacy):
    conf = Configuration(['www.example.com'], 'epub')
    conf.read([DEFAULTS])
    conf.set('overrides', 'add_chapter_numbers', 'false')
    conf.set('overrides', 'mark_new_chapters', 'false')
    conf.set('overrides', 'internalize_text_links', 'false')
    conf.set('overrides', 'include_subject_tags', 'extratags')
    conf.set('overrides', 'keep_in_order_extratags', 'true')
    conf.set('overrides', 'titlepage_entries',
             'category,status,datePublished,dateUpdated,dateCreated,'
             'publisher,description,numChapters,numWords')
    conf.set('overrides', 'include_images', 'true')
    if legacy:
        conf.set('overrides', 'chapter_start',
                 DEFAULTS_HASH_META_FREE_START)
    return conf


def _synthetic_story(conf, chapter_count, legacy):
    story = Story(conf)
    story.setMetadata('title', 'Synthetic Story')
    story.setMetadata('site', 'www.example.com')
    story.setMetadata('storyUrl', STORY_URL)
    story.setMetadata('storyId', '12345')
    story.setMetadata('langcode', 'en')
    story.addToList('author', 'Synthetic Author')
    story.addToList('authorUrl', 'https://www.example.com/user/99')
    story.addToList('authorId', '99')
    story.addToList('category', 'Fiction')
    story.addToList('genre', 'Action')
    story.addToList('genre', 'Drama')
    story.setMetadata('status', 'In-Progress')
    story.setMetadata('datePublished',
                      datetime.datetime(2024, 1, 2, 0, 0, 0))
    story.setMetadata('dateCreated',
                      datetime.datetime(2024, 3, 4, 5, 6, 7))
    story.setMetadata('dateUpdated',
                      datetime.datetime(2024, 5, 6, 7, 8, 9))
    story.setMetadata('description', 'A synthetic story.\nTwo lines.')
    story.setMetadata('numChapters', str(chapter_count))
    story.setMetadata('numWords', 12345)
    for subj in ('Alpha', 'Beta', 'Gamma'):
        story.addToList('extratags', subj)

    jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00' \
        b'\x01\x00\x00\xff\xd9'
    story.img_store.add_img(url='images/cover.jpg', ext='jpg',
                            mime='image/jpeg', data=jpeg, cover=True)
    story.cover = 'images/cover.jpg'

    for i in range(1, chapter_count + 1):
        story.addChapter({
            'url': '%s/%s-%d' % (STORY_URL, 'chapters', i),
            'title': 'Chapter %d' % i,
            'origtitle': 'Chapter %d' % i,
            'toctitle': 'Chapter %d' % i,
            'html': '<p>Chapter %d body text here.</p>' % i,
            'chapterhash': '' if legacy else 'abc%d' % i,
            'chapterlastcheck': '' if legacy else '2024-01-01',
        })
    return story


def _build_synthetic_epub(tmp_path, chapter_count, legacy):
    conf = _base_configuration(legacy)
    story = _synthetic_story(conf, chapter_count, legacy)
    writer = EpubWriter(conf, StubAdapter(story))
    from io import BytesIO
    out = BytesIO()
    writer.writeStory(outstream=out)
    epub_path = os.path.join(str(tmp_path), 'orig_%s.epub' % (
        'legacy' if legacy else 'modern'))
    with open(epub_path, 'wb') as fh:
        fh.write(out.getvalue())
    return epub_path


def _run_roundtrip(tmp_path, chapter_count=5, legacy=False):
    epub = _build_synthetic_epub(str(tmp_path), chapter_count, legacy)
    parts = os.path.join(str(tmp_path), 'parts')
    out_epub = os.path.join(str(tmp_path), 'out.epub')
    with open(epub, 'rb') as fh:
        os.utime(epub, (1577836800.25, 1577836800.25))
    deconstruct(epub, parts)
    reconstruct(parts, out_epub)
    ok = verify(epub, out_epub, parts_dir=parts)
    assert ok
    return epub, parts, out_epub


def test_roundtrip_modern(tmp_path):
    epub, parts, out = _run_roundtrip(tmp_path, chapter_count=5,
                                      legacy=False)
    import json
    with open(os.path.join(parts, 'reconstruction.json')) as fh:
        recon = json.load(fh)
    assert recon['has_chapter_hashes'] is True
    assert recon['site'] == 'www.example.com'
    assert recon['metadata']['title'] == 'Synthetic Story'
    assert len(recon['chapters']) == 5
    assert len(recon['subjects']) == 3


def test_roundtrip_legacy(tmp_path):
    epub, parts, out = _run_roundtrip(tmp_path, chapter_count=7,
                                      legacy=True)
    import json
    with open(os.path.join(parts, 'reconstruction.json')) as fh:
        recon = json.load(fh)
    assert recon['has_chapter_hashes'] is False
    assert len(recon['chapters']) == 7


def test_deconstruct_recovers_opf_metadata(tmp_path):
    epub = _build_synthetic_epub(tmp_path, 3, legacy=False)
    parts = str(tmp_path / 'parts')
    deconstruct(epub, parts)
    import json
    with open(os.path.join(parts, 'reconstruction.json')) as fh:
        recon = json.load(fh)
    md = recon['metadata']
    assert md['author'] == ['Synthetic Author']
    assert md['authorUrl'] == ['https://www.example.com/user/99']
    assert md['numWords'] == '12,345'
    assert md['dateUpdated'] == '2024-05-06T07:08:09'
    assert recon['cover'] and recon['cover']['newsrc'] == 'images/cover.jpg'


def test_legacy_chapters_byte_identical(tmp_path):
    epub = _build_synthetic_epub(str(tmp_path), 3, legacy=True)
    parts = os.path.join(str(tmp_path), 'parts')
    out = os.path.join(str(tmp_path), 'out.epub')
    deconstruct(epub, parts)
    import json
    with open(os.path.join(parts, 'reconstruction.json')) as fh:
        recon = json.load(fh)
    assert recon['has_chapter_hashes'] is False
    reconstruct(parts, out)
    import zipfile
    zo = zipfile.ZipFile(epub)
    zn = zipfile.ZipFile(out)
    for name in zo.namelist():
        base = os.path.basename(name)
        if base.startswith('file') and base.endswith('.xhtml'):
            assert zo.read(name) == zn.read(name)
    zo.close()
    zn.close()
    ok = verify(epub, out, parts_dir=parts)
    assert ok