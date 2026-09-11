# -*- coding: utf-8 -*-

import datetime
import json
import os
from io import BytesIO

from .util import DeconstructError


DEFAULTS_HASH_META_FREE_START = '''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<title>${chapter}</title>
<link href="stylesheet.css" type="text/css" rel="stylesheet"/>
<meta name="chapterurl" content="${url}" />
<meta name="chapterorigtitle" content="${origchapter}" />
<meta name="chaptertoctitle" content="${tocchapter}" />
<meta name="chaptertitle" content="${chapter}" />
</head>
<body class="fff_chapter">
<h3 class="fff_chapter_title">${chapter}</h3>
'''


class StubAdapter(object):
    '''Runner-supplied adapter: hands back the pre-built Story so the
    real EpubWriter works fully offline.'''

    def __init__(self, story):
        self._story = story

    def getStory(self, notification=lambda x, y: x):
        return self._story

    def getStoryMetadataOnly(self):
        return self._story

    def include_css_urls(self, url, css):
        return css


def _get_config_sections(storyurl):
    from fanficfare import adapters
    try:
        return adapters.getConfigSectionsFor(storyurl)
    except Exception:
        site = None
        try:
            from fanficfare.adapters import get_section_url
            site = get_section_url(storyurl)
        except Exception:
            site = 'www.example.com'
        return [site]


def _get_configuration(sections, recon):
    from fanficfare.configurable import Configuration
    import fanficfare
    defaults = os.path.join(os.path.dirname(fanficfare.__file__),
                            'defaults.ini')
    # FFF's Configuration uses configparser BasicInterpolation: values
    # carrying literal '%' must be escaped as '%%' when writing, which
    # getConfig() then reverses on read (defaults.ini itself stores
    # date*_format as %%Y-%%m-%%d etc.).
    def cfgset(conf, key, value):
        conf.set('overrides', key, value.replace('%', '%%'))
    conf = Configuration(sections, 'epub')
    conf.read([defaults])

    settings = recon.get('settings', {})
    ovr = {
        'add_chapter_numbers': 'false',
        'mark_new_chapters': 'false',
        'internalize_text_links': 'false',
        'include_titlepage': 'true' if settings.get(
            'include_titlepage', True) else 'false',
        'include_tocpage': 'true' if settings.get(
            'include_tocpage', False) else 'false',
        'include_logpage': 'true' if settings.get(
            'include_logpage', False) else 'false',
        'include_images': 'true' if settings.get(
            'include_images', True) else 'false',
        'epub_version': settings.get('epub_version', '2.0'),
        'include_subject_tags': 'extratags' if recon.get('subjects')
        else '',
        'keep_in_order_extratags': 'true' if recon.get('subjects')
        else '',
    }
    if settings.get('titlepage_use_table'):
        ovr['titlepage_use_table'] = 'true'
    tpe = settings.get('titlepage_entries')
    if tpe:
        ovr['titlepage_entries'] = ','.join(
            e.lstrip('<').rstrip('>') if e.startswith('<') and
            e.endswith('>') else e for e in tpe)

    # output_css: the captured stylesheet, minus the site's
    # add_to_output_css that getConfig() appends back at write time.
    css_text = _read_utf8_file(recon, recon.get('css_path', ''))
    if css_text:
        addto = _get_add_to_output_css(conf)
        base = css_text
        if addto and addto and base.endswith(addto):
            base = base[:-len(addto)]
        ovr['output_css'] = base

    if not recon.get('has_chapter_hashes'):
        ovr['chapter_start'] = DEFAULTS_HASH_META_FREE_START

    for key, value in ovr.items():
        cfgset(conf, key, value)
    return conf


def _get_add_to_output_css(conf):
    out = ''
    for section in reversed(conf.sectionslist):
        try:
            out += conf.get(section, 'add_to_output_css')
        except Exception:
            pass
    return out


def _read_utf8_file(recon, filepath):
    parts_file = filepath.replace('/', os.sep)
    full = os.path.join(recon['_parts_dir'], 'files', parts_file)
    if not os.path.exists(full):
        return ''
    with open(full, 'r', encoding='utf-8', errors='replace') as fh:
        return fh.read()


def _parse_date(value):
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _build_story(conf, recon):
    from fanficfare.story import Story
    story = Story(conf)
    md = recon['metadata']

    story.setMetadata('title', md['title'])
    story.setMetadata('site', recon['site'])
    if recon['storyUrl']:
        story.setMetadata('storyUrl', recon['storyUrl'])
    if md.get('storyId'):
        story.setMetadata('storyId', md['storyId'])
    if md.get('langcode'):
        story.setMetadata('langcode', md['langcode'])

    for val in md.get('author', []) or []:
        story.addToList('author', val)
    for val in md.get('authorUrl', []) or []:
        story.addToList('authorUrl', val)
    for val in md.get('authorId', []) or []:
        story.addToList('authorId', val)

    dPublished = _parse_date(md.get('datePublished', ''))
    dCreated = _parse_date(md.get('dateCreated', ''))
    dUpdated = _parse_date(md.get('dateUpdated', ''))
    if dPublished:
        story.setMetadata('datePublished', dPublished)
    if dCreated:
        story.setMetadata('dateCreated', dCreated)
    elif recon.get('epub_mtime'):
        story.setMetadata('dateCreated',
                          datetime.datetime.fromtimestamp(
                              recon['epub_mtime']))
    if dUpdated:
        story.setMetadata('dateUpdated', dUpdated)

    if md.get('description'):
        story.setMetadata('description', md['description'])
    if md.get('status'):
        story.setMetadata('status', md['status'])
    nc = md.get('numChapters')
    if nc is None or not nc or (isinstance(nc, int) and nc <= 0):
        nc = len(recon.get('chapters', []))
    story.setMetadata('numChapters', nc)
    if md.get('numWords'):
        story.setMetadata('numWords', md['numWords'])
    for val in md.get('category', []) or []:
        story.addToList('category', val)
    for val in md.get('genre', []) or []:
        story.addToList('genre', val)
    for val in recon.get('subjects') or []:
        story.addToList('extratags', val)
    if md.get('series') and md.get('series_index') is not None:
        try:
            idx = float(md['series_index'])
        except (TypeError, ValueError):
            idx = 1.0
        story.setMetadata('series', '%s [%g]' % (md['series'], idx))

    return story


def _load_images(story, recon):
    parts = recon['_parts_dir']
    cover = recon.get('cover') or {}
    if cover and cover.get('newsrc'):
        data = _read_part_bytes(parts, cover.get('path') or cover['newsrc'])
        if data:
            ext = cover['newsrc'].rsplit('.', 1)[-1]
            story.img_store.add_img(url=cover['newsrc'], ext=ext,
                                    mime=cover.get('mime', 'image/jpeg'),
                                    data=data, cover=True)
            story.cover = cover['newsrc']
    for img in recon.get('images', []) or []:
        if img['newsrc'] == (cover or {}).get('newsrc'):
            continue
        data = _read_part_bytes(parts, img.get('path') or img['newsrc'])
        if not data:
            continue
        ext = img['newsrc'].rsplit('.', 1)[-1]
        story.img_store.add_img(url=img['newsrc'], ext=ext,
                                mime=img.get('mime', 'image/jpeg'),
                                data=data, cover=False)


def _read_part_bytes(parts_dir, newsrc):
    rel = newsrc.replace('/', os.sep)
    full = os.path.join(parts_dir, 'files', rel)
    if not os.path.exists(full):
        return None
    with open(full, 'rb') as fh:
        return fh.read()


def _load_chapters(story, recon):
    parts = recon['_parts_dir']
    for rec in recon['chapters']:
        rel = rec['file'].replace('/', os.sep)
        full = os.path.join(parts, rel)
        if not os.path.exists(full):
            raise DeconstructError('missing chapter body: %s' % rec['file'])
        with open(full, 'r', encoding='utf-8', errors='replace') as fh:
            body = fh.read()
        chap = {
            'url': rec.get('url', ''),
            'title': rec.get('title', ''),
            'origtitle': rec.get('origtitle', rec.get('title', '')),
            'toctitle': rec.get('toctitle', rec.get('title', '')),
            'html': body,
        }
        if rec.get('hash'):
            chap['chapterhash'] = rec['hash']
        if rec.get('lastcheck'):
            chap['chapterlastcheck'] = rec['lastcheck']
        story.addChapter(chap)


def reconstruct(parts_dir, out_epub_path):
    json_path = os.path.join(parts_dir, 'reconstruction.json')
    if not os.path.exists(json_path):
        raise DeconstructError('no reconstruction.json in %s' % parts_dir)
    with open(json_path, 'r', encoding='utf-8') as fh:
        recon = json.load(fh)
    recon['_parts_dir'] = os.path.abspath(parts_dir)

    sections = _get_config_sections(recon.get('storyUrl', '') or
                                    recon.get('site', ''))
    conf = _get_configuration(sections, recon)

    story = _build_story(conf, recon)
    _load_images(story, recon)
    _load_chapters(story, recon)

    bookmarks = recon.get('calibre_bookmarks') or ''
    if bookmarks:
        story.calibrebookmark = bookmarks
    logpage = recon.get('log_page') or ''
    if logpage:
        full = os.path.join(recon['_parts_dir'], 'files',
                            logpage.replace('/', os.sep))
        if os.path.exists(full):
            with open(full, 'r', encoding='utf-8', errors='replace') as fh:
                story.logfile = fh.read()

    from fanficfare.writers.writer_epub import EpubWriter
    writer = EpubWriter(conf, StubAdapter(story))
    out = BytesIO()
    writer.writeStory(outstream=out)
    data = out.getvalue()

    outdir = os.path.dirname(os.path.abspath(out_epub_path)) or '.'
    os.makedirs(outdir, exist_ok=True)
    with open(out_epub_path, 'wb') as fh:
        fh.write(data)
    mtime = recon.get('epub_mtime')
    if mtime:
        os.utime(out_epub_path, (mtime, mtime))

    print('reconstructed %s -> %s (%d bytes)' % (
        recon.get('original_filename', 'epub'), out_epub_path, len(data)))
    print('  chapters: %d  images(+cover): %d  site: %s' % (
        len(recon['chapters']),
        len(recon.get('images', []) or []) + (1 if recon.get('cover') else 0),
        recon.get('site', '')))
    return True