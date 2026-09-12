# -*- coding: utf-8 -*-

import json
import os
import zipfile

from epub_reconstruct.merge import merge, word_count


STORY_URL = 'https://www.royalroad.com/fiction/12345/the-book'
CHAP_URL = 'https://www.royalroad.com/fiction/12345/the-book/chapter'


def _json(name, obj):
    with open(name, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh)


def _img_data(tag):
    return b'GIF89a%s' % tag.encode('ascii')


def _make_source_parts(base, name, ids, date_updated='2024-01-01T00:00:00',
                       css='body { color: red; }', title='The Book',
                       bodies=None, images=(), image_refs=None, date_created='',
                       subjects=('Alpha',), slug_map=None):
    '''Build a merge-ready parts dir: chapters/0001.. files/images and
    reconstruction.json.  ids are RR chapter ids.  bodies overrides the
    generated '<p>text for <id></p>' per id.'''
    parts = os.path.join(base, name)
    os.makedirs(os.path.join(parts, 'chapters'), exist_ok=True)
    os.makedirs(os.path.join(parts, 'files', 'OEBPS', 'images'),
                exist_ok=True)

    with open(os.path.join(parts, 'files', 'OEBPS', 'stylesheet.css'), 'w',
              encoding='utf-8') as fh:
        fh.write(css)

    chapters = []
    for i, cid in enumerate(ids, 1):
        body = bodies.get(cid, '<p>body for %d</p>' % cid) if bodies \
            else '<p>body for %d</p>' % cid
        refs = (image_refs or {}).get(cid)
        if refs:
            body = body + ''.join(
                '<img src="images/%s"/>' % r for r in refs)
        with open(os.path.join(parts, 'chapters', '%04d.html' % i), 'w',
                  encoding='utf-8') as fh:
            fh.write(body)
        slug = slug_map.get(cid, 'chapter-%d' % cid) if slug_map else \
            'chapter-%d' % cid
        chapters.append({
            'file': 'chapters/%04d.html' % i,
            'url': '%s/%d/%s' % (CHAP_URL, cid, slug),
            'title': 'Chapter %d' % cid,
            'origtitle': 'Chapter %d' % cid,
            'toctitle': 'Chapter %d' % cid,
            'hash': '',
            'lastcheck': '',
        })

    image_recs = []
    for n, tag in images:
        with open(os.path.join(parts, 'files', 'OEBPS', 'images', n),
                  'wb') as fh:
            fh.write(_img_data(tag))
        image_recs.append({
            'newsrc': 'images/%s' % n,
            'path': 'OEBPS/images/%s' % n,
            'mime': 'image/gif',
        })

    recon = {
        'format_version': 1,
        'original_filename': '%s.epub' % name,
        'epub_mtime': 1600000000.0,
        'rootdir': '',
        'site': 'www.royalroad.com',
        'storyUrl': STORY_URL,
        'metadata': {
            'title': title,
            'author': ['Author One'],
            'authorId': ['99'],
            'authorUrl': ['https://www.royalroad.com/user/99'],
            'storyId': '12345',
            'langcode': 'en',
            'datePublished': '2023-01-01',
            'dateCreated': date_created or '2023-01-02T00:00:00',
            'dateUpdated': date_updated,
            'description': 'desc %s' % name,
            'category': ['Fiction'],
            'genre': ['Action'],
            'status': 'In-Progress',
            'numChapters': len(ids),
            'numWords': len(ids) * 8,
            'publisher': 'RoyalRoad',
            'series': '',
            'series_index': '',
        },
        'subjects': list(subjects),
        'has_chapter_hashes': False,
        'chapters': chapters,
        'cover': None,
        'images': image_recs,
        'css_path': 'OEBPS/stylesheet.css',
        'settings': {
            'include_titlepage': True,
            'include_tocpage': False,
            'include_logpage': False,
            'include_images': True,
            'epub_version': '2.0',
            'titlepage_entries': ['category', 'status', 'datePublished',
                                  'dateUpdated', 'dateCreated',
                                  'publisher', 'description',
                                  'numChapters', 'numWords'],
        },
        'calibre_bookmarks': '',
        'log_page': '',
        'entry_order': ['title_page'],
    }
    _json(os.path.join(parts, 'reconstruction.json'), recon)
    return parts


def _run_merge(tmp_path, parts_list, make_parts=True, **kwargs):
    out_dir = str(tmp_path / 'out_parts')
    report_path = os.path.join(out_dir, 'merge_report.json')
    merge([os.path.abspath(p) for p in parts_list],
          parts_out=out_dir, no_reconstruct=True)
    return out_dir, json.load(open(report_path, encoding='utf-8'))


def test_disjoint_groups(tmp_path):
    p1 = _make_source_parts(str(tmp_path), 's0', [1, 2, 3])
    p2 = _make_source_parts(str(tmp_path), 's1', [4, 5, 6])
    out_dir, report = _run_merge(tmp_path, [p1, p2])
    assert report['union_chapters'] == 6
    assert report['merged_chapters'] == 6
    assert report['match_union'] is True
    recon = json.load(open(os.path.join(out_dir, 'reconstruction.json'),
                           encoding='utf-8'))
    urls = [c['url'] for c in recon['chapters']]
    assert urls == [p1 and '%s/%d/chapter-%d' % (CHAP_URL, i, i)
                    for i in [1, 2, 3, 4, 5, 6]]


def test_identical_overlap_dedup(tmp_path):
    p1 = _make_source_parts(str(tmp_path), 's0', [1, 2, 3, 4, 5],
                            date_updated='2024-01-01T00:00:00')
    p2 = _make_source_parts(str(tmp_path), 's1', [1, 2, 3, 4, 5],
                            date_updated='2024-02-01T00:00:00')
    out_dir, report = _run_merge(tmp_path, [p1, p2])
    assert report['union_chapters'] == 5
    assert report['merged_chapters'] == 5
    assert report['match_union'] is True
    for sel in report['selections'].values():
        assert sel['identical'] is True
        assert sel['chosen'] == 1
    recon = json.load(open(os.path.join(out_dir, 'reconstruction.json'),
                           encoding='utf-8'))
    assert len(recon['chapters']) == 5


def test_overlap_newest_wins_and_tiebreak(tmp_path):
    bodies0 = {1: '<p>STALE for 1</p>', 2: '<p>STALE for 2</p>'}
    bodies1 = {1: '<p>FRESH for 1</p>', 2: '<p>FRESH for 2</p>'}
    p1 = _make_source_parts(str(tmp_path), 'old', [1, 2, 3],
                            date_updated='2024-01-01T00:00:00',
                            bodies=bodies0)
    p2 = _make_source_parts(str(tmp_path), 'new', [1, 2],
                            date_updated='2024-02-01T00:00:00',
                            bodies=bodies1)
    out_dir, report = _run_merge(tmp_path, [p1, p2])
    for k in ('1', '2'):
        assert report['selections'][k]['chosen'] == 1
        assert report['selections'][k]['identical'] is False
    # arg-index tiebreak with equal dates: newest by CLI order, base never
    p3 = _make_source_parts(str(tmp_path), 'tie0', [1, 9, 10],
                            date_updated='2024-01-01T00:00:00')
    p4 = _make_source_parts(str(tmp_path), 'tie1', [1, 11],
                            date_updated='2024-01-01T00:00:00')
    _, report2 = _run_merge(tmp_path, [p3, p4])
    assert report2['selections']['1']['chosen'] == 1


def test_retitle_same_id(tmp_path):
    slug_map = {100: 'old-slug'}
    p1 = _make_source_parts(str(tmp_path), 's0', [100],
                            slug_map={100: 'old-slug'})
    p2 = _make_source_parts(str(tmp_path), 's1', [100],
                            date_updated='2024-02-01T00:00:00',
                            slug_map={100: 'new-slug'})
    out_dir, report = _run_merge(tmp_path, [p1, p2])
    assert report['union_chapters'] == 1
    assert report['merged_chapters'] == 1
    assert report['selections']['100']['chosen'] == 1
    recon = json.load(open(os.path.join(out_dir, 'reconstruction.json'),
                           encoding='utf-8'))
    assert recon['chapters'][0]['url'].endswith('/100/new-slug')


def test_lcs_insertion(tmp_path):
    p1 = _make_source_parts(str(tmp_path), 's0', [1, 2, 3, 4, 5])
    p2 = _make_source_parts(str(tmp_path), 's1',
                            [3, 10, 4, 5, 6, 7, 8, 9, 11, 12],
                            date_updated='2024-02-01T00:00:00')
    out_dir, report = _run_merge(tmp_path, [p1, p2])
    recon = json.load(open(os.path.join(out_dir, 'reconstruction.json'),
                           encoding='utf-8'))
    ids = []
    import re
    for c in recon['chapters']:
        ids.append(int(re.search(r'/chapter/(\d+)/', c['url']).group(1)))
    assert ids == [1, 2, 3, 10, 4, 5, 6, 7, 8, 9, 11, 12]
    assert report['match_union'] is True


def test_split_pair_older_before_newer(tmp_path):
    p1 = _make_source_parts(str(tmp_path), 's0', [1, 2, 3])
    p2 = _make_source_parts(str(tmp_path), 's1', [1, 2, 25, 3],
                            date_updated='2024-02-01T00:00:00')
    out_dir, report = _run_merge(tmp_path, [p1, p2])
    recon = json.load(open(os.path.join(out_dir, 'reconstruction.json'),
                           encoding='utf-8'))
    import re
    ids = [int(re.search(r'/chapter/(\d+)/', c['url']).group(1))
           for c in recon['chapters']]
    assert ids == [1, 2, 25, 3]  # inserted ch 25 lands after old ch 2


def test_css_newest_and_metadata(tmp_path):
    p1 = _make_source_parts(
        str(tmp_path), 's0', [1, 2, 3], date_updated='2024-01-01T00:00:00',
        date_created='2023-01-02T00:00:00', title='Old Title',
        css='body { color: red; }', subjects=('Alpha', 'Beta'))
    p2 = _make_source_parts(
        str(tmp_path), 's1', [3, 4], date_updated='2024-02-01T00:00:00',
        date_created='2023-05-02T00:00:00', title='New Title',
        css='body { color: blue; }', subjects=('Beta', 'Gamma'))
    out_dir, report = _run_merge(tmp_path, [p1, p2])
    recon = json.load(open(os.path.join(out_dir, 'reconstruction.json'),
                           encoding='utf-8'))
    css = open(os.path.join(out_dir, 'files', 'OEBPS', 'stylesheet.css'),
               encoding='utf-8').read()
    assert css == 'body { color: blue; }'
    assert recon['metadata']['title'] == 'New Title'
    assert recon['subjects'] == ['Alpha', 'Beta', 'Gamma']
    assert recon['metadata']['dateUpdated'] == '2024-02-01T00:00:00'
    assert recon['metadata']['dateCreated'] == '2023-05-02T00:00:00'
    assert recon['metadata']['datePublished'] == '2023-01-01'
    # numWords recomputed over merged bodies (4 unique chapters)
    assert recon['metadata']['numChapters'] == 4
    total = sum(word_count(open(os.path.join(
        out_dir, 'chapters', rec['file'].split('/')[-1]),
        encoding='utf-8').read()) for rec in recon['chapters'])
    assert recon['metadata']['numWords'] == total


def test_image_rename_and_ref_rewrite(tmp_path):
    images = (('ffdl-0.jpg', 'img-A'), ('ffdl-92.jpg', 'img-A'))
    refs = {7: ['ffdl-92.jpg']}
    p1 = _make_source_parts(str(tmp_path), 's0', [7, 8],
                            images=(('ffdl-0.jpg', 'img-A'),),
                            image_refs={7: ['ffdl-0.jpg']})
    p2 = _make_source_parts(str(tmp_path), 's1', [7, 9],
                            date_updated='2024-02-01T00:00:00',
                            images=(('ffdl-92.jpg', 'img-A'),),
                            image_refs=refs)
    out_dir, report = _run_merge(tmp_path, [p1, p2])
    # overlap chapter 7 chosen from newest (p2): ref rewritten to ch7 name
    assert report['selections']['7']['chosen'] == 1
    renames = [r for r in report['image_renames'] if r['chapter_id'] == 7]
    assert len(renames) == 1
    assert renames[0]['from'] == 'images/ffdl-92.jpg'
    # canonical base deduped by sha (same content) → first seen basename
    assert renames[0]['to'].startswith('images/ch7-')
    assert renames[0]['to'].endswith('.jpg')
    assert renames[0]['sha']
    # the merged chapter body references the renamed image
    ch7 = open(os.path.join(out_dir, 'chapters', '0001.html'),
               encoding='utf-8').read()
    assert 'images/ch7-' in ch7
    assert 'src="images/ch7-' in ch7
    # no raw original name in body
    assert 'ffdl-92.jpg"' not in ch7
    # renamed image is physically present in merged parts
    to_name = os.path.basename(renames[0]['to'])
    assert os.path.exists(os.path.join(out_dir, 'files', 'OEBPS', 'images',
                                       to_name))
    # only the referenced overlap chapter (7) gets a rename here
    assert len(report['image_renames']) == 1


def test_full_loop_epub_inputs(tmp_path):
    from io import BytesIO

    from fanficfare.configurable import Configuration
    from fanficfare.story import Story

    from epub_reconstruct.deconstruct import deconstruct
    from epub_reconstruct.reconstruct import StubAdapter as SA
    from fanficfare.writers.writer_epub import EpubWriter

    import fanficfare
    DEFAULTS = os.path.join(os.path.dirname(fanficfare.__file__),
                            'defaults.ini')

    def build(conf_count, date_updated):
        conf = Configuration(['www.royalroad.com'], 'epub')
        conf.read([DEFAULTS])
        conf.set('overrides', 'add_chapter_numbers', 'false')
        conf.set('overrides', 'mark_new_chapters', 'false')
        conf.set('overrides', 'internalize_text_links', 'false')
        conf.set('overrides', 'include_subject_tags', 'extratags')
        conf.set('overrides', 'keep_in_order_extratags', 'true')
        conf.set('overrides', 'include_images', 'true')
        conf.set('overrides', 'titlepage_entries',
                 'category,status,datePublished,dateUpdated,dateCreated,'
                 'publisher,description,numChapters,numWords')
        import datetime
        story = Story(conf)
        story.setMetadata('title', 'Merge Me')
        story.setMetadata('site', 'www.royalroad.com')
        story.setMetadata('storyUrl', STORY_URL)
        story.setMetadata('storyId', '12345')
        story.setMetadata('langcode', 'en')
        story.addToList('author', 'Author One')
        story.addToList('authorId', '99')
        story.addToList('authorUrl', 'https://www.royalroad.com/user/99')
        story.setMetadata('datePublished',
                          datetime.datetime(2023, 1, 1, 0, 0, 0))
        story.setMetadata('dateCreated',
                          datetime.datetime(2023, 1, 2, 0, 0, 0))
        story.setMetadata('dateUpdated',
                          datetime.datetime(2024, 1, 1, 0, 0, 0) if
                          not date_updated else date_updated)
        story.setMetadata('description', 'merge me')
        story.setMetadata('numChapters', str(conf_count))
        story.setMetadata('numWords', 1000)
        jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00' \
            b'\x01\x00\x00\xff\xd9'
        story.img_store.add_img(url='images/cover.jpg', ext='jpg',
                                mime='image/jpeg', data=jpeg, cover=True)
        story.cover = 'images/cover.jpg'
        story.img_store.add_img(url='images/ffdl-0.jpg', ext='jpg',
                                mime='image/jpeg', data=jpeg)
        for i in range(1, conf_count + 1):
            body = '<p>Chapter %d</p>' % i
            if i == 1:
                body += '<img src="images/ffdl-0.jpg"/>'
            story.addChapter({
                'url': '%s/%d/chapter-%d' % (CHAP_URL, i, i),
                'title': 'Chapter %d' % i,
                'origtitle': 'Chapter %d' % i,
                'toctitle': 'Chapter %d' % i,
                'html': body,
            })
        writer = EpubWriter(conf, SA(story))
        out = BytesIO()
        writer.writeStory(outstream=out)
        return out.getvalue()

    epub_a = os.path.join(str(tmp_path), 'a.epub')
    epub_b = os.path.join(str(tmp_path), 'b.epub')
    with open(epub_a, 'wb') as fh:
        fh.write(build(3, None))
    with open(epub_b, 'wb') as fh:
        import datetime
        fh.write(build(3, datetime.datetime(2024, 2, 1, 0, 0, 0)))

    parts_a = os.path.join(str(tmp_path), 'pa')
    parts_b = os.path.join(str(tmp_path), 'pb')
    deconstruct(epub_a, parts_a)
    deconstruct(epub_b, parts_b)
    out_parts = os.path.join(str(tmp_path), 'merged_parts')
    out_epub = os.path.join(str(tmp_path), 'merged.epub')
    merge([os.path.abspath(parts_a), os.path.abspath(parts_b)],
          parts_out=out_parts, out_epub=out_epub)
    report_path = os.path.join(out_parts, 'merge_report.json')
    report = json.load(open(report_path, encoding='utf-8'))
    assert report['union_chapters'] == 3
    assert report['merged_chapters'] == 3
    recon = json.load(open(os.path.join(out_parts, 'reconstruction.json'),
                           encoding='utf-8'))
    assert len(recon['chapters']) == 3
    # merged epub opens and every chapter image ref resolves
    z = zipfile.ZipFile(out_epub)
    names = set(z.namelist())
    for n in names:
        if n.endswith('.xhtml') and n.startswith('OEBPS/file'):
            body = z.read(n).decode('utf-8', 'replace')
            for m in __import__('re').finditer(
                    r'src="(images/[^"]+)"', body):
                assert ('OEBPS/' + m.group(1)) in names, (n, m.group(1))
    z.close()