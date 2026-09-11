# -*- coding: utf-8 -*-

import json
import os
import re
from zipfile import ZipFile

from .util import (
    DeconstructError, entry_path, get_opf_path, looks_like_chapter,
    parse_chapter_file, parse_opf, parse_title_page,
    recover_from_uniqueid, recover_site_from_url, to_newsrc,
)


def _get_label_dirs():
    '''{label: entry_key} using FFF defaults + {entry_key: label}.'''
    try:
        from fanficfare.configurable import Configurable, Configuration, \
            titleLabels
        import fanficfare

        class _Labeled(Configurable):
            def __init__(self, configuration):
                super(_Labeled, self).__init__(configuration)

        defaults = os.path.join(os.path.dirname(fanficfare.__file__),
                                'defaults.ini')
        conf = Configuration(['www.example.com'], 'epub')
        conf.read([defaults])
        wrapper = _Labeled(conf)
        label_to_key = {wrapper.get_label(k): k for k in titleLabels}
        return label_to_key
    except Exception:
        return {}


def deconstruct(epub_path, parts_dir):
    zip_basename = os.path.basename(epub_path)
    labels = _get_label_dirs()
    zf = ZipFile(epub_path, 'r')
    try:
        names = zf.namelist()
        files_dir = os.path.join(parts_dir, 'files')
        chapters_dir = os.path.join(parts_dir, 'chapters')
        os.makedirs(files_dir, exist_ok=True)
        os.makedirs(chapters_dir, exist_ok=True)

        for name in names:
            data = zf.read(name)
            target = os.path.join(files_dir, name.replace('/', os.sep))
            parent = os.path.dirname(target) or files_dir
            os.makedirs(parent, exist_ok=True)
            with open(target, 'wb') as fh:
                fh.write(data)

        opf_path, rootdir = get_opf_path(zf)
        opf = parse_opf(zf.read(opf_path))

        version = opf['version']
        unique_id_val = opf['unique_id_val']
        site, authorid, storyid = recover_from_uniqueid(unique_id_val)

        md = opf['metadata']
        title = md.get('title', '')
        authors = md.get('creator')
        if not isinstance(authors, list):
            authors = [authors] if authors else []
        langcode = md.get('language', 'en')

        datePublished = _str(md.get('datePublication'))
        dateCreated = _str(md.get('dateCreation'))
        dateUpdated = opf['metas'].get('calibre:timestamp', '') or \
            _str(md.get('dateModification'))
        description = _str(md.get('description'))

        storyurl = _str(md.get('source'))
        if not storyurl:
            ident = md.get('identifier', '')
            if isinstance(ident, list):
                ident = ident[0]
            if isinstance(ident, str) and ident.startswith('URL:'):
                storyurl = ident[4:]
            elif isinstance(ident, str) and \
                    not ident.startswith('fanficfare-uid'):
                storyurl = ident

        if not site:
            site = recover_site_from_url(storyurl) or _resolve_site(opf)

        series = opf['metas'].get('calibre:series', '')
        series_index = opf['metas'].get('calibre:series_index', '')

        # ---- title page ----
        tp_name = _find_by_basename(names, 'title_page.xhtml')
        tp = None
        if tp_name in names:
            tp = parse_title_page(
                zf.read(tp_name).decode('utf-8', 'replace'),
                reverse_labels=labels)
            if not storyurl:
                storyurl = tp['storyUrl']
            if dateCreated and not re.search(r'\d{2}:\d{2}:\d{2}',
                                             dateCreated):
                dateCreated = tp['values'].get('dateCreated', '') or \
                    dateCreated

        chcount = int(re.sub(r'[^\d]', '',
                             (tp or {}).get('values', {}).get('numChapters',
                                                               '') or '0')
                      or '0')
        numWords = (tp or {}).get('values', {}).get('numWords', '')
        category_raw = (tp or {}).get('values', {}).get('category', '')
        genre_raw = (tp or {}).get('values', {}).get('genre', '')
        status = (tp or {}).get('values', {}).get('status', '')

        categories = [c.strip() for c in category_raw.split(',')
                      if c.strip()]
        genres = [g.strip() for g in genre_raw.split(',') if g.strip()]

        # ---- cover / images / css / log page ----
        manifest = opf['manifest']
        covermeta_id = opf['metas'].get('cover', '')
        images = []
        cover = None
        style_path = ''
        for mid, mitem in manifest.items():
            href = mitem['href']
            mime = mitem['type']
            epath = entry_path(rootdir, href)
            if mime.startswith('image/'):
                rec = {
                    'newsrc': to_newsrc(href, rootdir),
                    'path': epath,
                    'mime': mime,
                }
                is_cover = (covermeta_id and mid == covermeta_id) or \
                    os.path.basename(href).lower().startswith('cover')
                if is_cover:
                    cover = rec
                else:
                    images.append(rec)
            elif mime == 'text/css' and not style_path:
                style_path = epath

        css_text = ''
        if style_path and style_path in names:
            css_text = zf.read(style_path).decode('utf-8', 'replace')

        log_entry = ''
        for candidate in ('log_page.xhtml',):
            found = _find_by_basename(names, candidate)
            if found and found not in ('',):
                log_entry = found
                break

        # ---- chapters ----
        chapters = []
        seen = set()
        for idref in opf['spine']:
            mitem = manifest.get(idref)
            if not mitem:
                continue
            href = mitem['href']
            if not looks_like_chapter(href):
                continue
            cpath = entry_path(rootdir, href)
            if cpath not in names or cpath in seen:
                continue
            seen.add(cpath)
            text = zf.read(cpath).decode('utf-8', 'replace')
            parsed = parse_chapter_file(text)
            if parsed is None:
                continue
            chapters.append(parsed)

        for idx, chap in enumerate(chapters, 1):
            bodyfile = os.path.join(chapters_dir, '%04d.html' % idx)
            with open(bodyfile, 'w', encoding='utf-8') as fh:
                fh.write(chap['body'])

        has_chapter_hashes = any(c['hash'] or c['lastcheck']
                                 for c in chapters)

        calibre_bookmarks = ''
        if 'META-INF/calibre_bookmarks.txt' in names:
            calibre_bookmarks = zf.read(
                'META-INF/calibre_bookmarks.txt').decode('utf-8', 'replace')

        # ---- settings ----
        tocpath = _find_by_basename(names, 'toc_page.xhtml')
        settings = {
            'include_titlepage': tp_name in names,
            'include_tocpage': tocpath in names,
            'include_logpage': bool(log_entry),
            'include_images': bool(images) or bool(cover),
            'epub_version': version,
            'titlepage_use_table': bool(tp and tp['use_table']),
        }
        if tp is not None:
            settings['titlepage_entries'] = tp['entries']

        images_list = [{'newsrc': im['newsrc'], 'path': im['path'],
                        'mime': im['mime']} for im in images]

        metadata = {
            'title': title,
            'author': authors,
            'authorId': [authorid] if authorid else [],
            'authorUrl': (tp['authorUrls'] if tp else []),
            'storyId': storyid,
            'langcode': langcode,
            'datePublished': datePublished,
            'dateCreated': dateCreated,
            'dateUpdated': dateUpdated,
            'description': description,
            'category': categories,
            'genre': genres,
            'status': status,
            'numChapters': chcount,
            'numWords': numWords,
            'publisher': site,
            'series': series,
            'series_index': series_index,
        }

        recon = {
            'format_version': 1,
            'original_filename': zip_basename,
            'epub_mtime': os.path.getmtime(epub_path),
            'rootdir': rootdir,
            'site': site,
            'storyUrl': storyurl,
            'metadata': metadata,
            'subjects': opf['subjects'],
            'has_chapter_hashes': has_chapter_hashes,
            'chapters': [
                {
                    'file': 'chapters/%04d.html' % (i + 1),
                    'url': c['url'],
                    'title': c['title'],
                    'origtitle': c['origtitle'],
                    'toctitle': c['toctitle'],
                    'hash': c['hash'],
                    'lastcheck': c['lastcheck'],
                }
                for i, c in enumerate(chapters)
            ],
            'cover': cover,
            'images': images_list,
            'css_path': style_path,
            'settings': settings,
            'calibre_bookmarks': calibre_bookmarks,
            'log_page': log_entry,
            'entry_order': names,
        }

        with open(os.path.join(parts_dir, 'reconstruction.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump(recon, fh, indent=2, sort_keys=False)

        print('deconstructed %s -> %s' % (zip_basename, parts_dir))
        print('  site: %s  storyUrl: %s' % (site, storyurl))
        print('  title: %s' % title)
        print('  epub_version: %s  rootdir: %r' % (version, rootdir))
        print('  cover: %s  other images: %d' % (
            (cover or {}).get('newsrc', '') or '-', len(images_list)))
        print('  chapters: %d  has_chapter_hashes: %s' % (
            len(chapters), has_chapter_hashes))
        print('  subjects: %d  titlepage entries: %s' % (
            len(recon['subjects']),
            (tp['entries'] if tp is not None else '-')))
        return True
    finally:
        zf.close()


def _str(value):
    if value is None:
        return ''
    if isinstance(value, list):
        return ''.join(value)
    return str(value)


def _find_by_basename(names, basename):
    for name in names:
        if os.path.basename(name) == basename:
            return name
    return ''


def _resolve_site(opf):
    site = recover_from_uniqueid(opf.get('unique_id_val', ''))[0]
    if site:
        return site
    return _str(opf.get('metadata', {}).get('publisher'))