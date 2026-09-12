# -*- coding: utf-8 -*-

import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile

from .deconstruct import deconstruct
from .reconstruct import reconstruct
from .util import DeconstructError, sha256_bytes

# <img ... src="images/OLD"> -> <img ... src="images/NEW"> (also '
IMG_SRC_RE = re.compile(
    r'(<img[^>]*?\bsrc\s*=\s*)(["\'])(images/[^"\']+)(["\'])')
SINGLE_QUOTE = re.compile(
    r"(<img[^>]*?\bsrc\s*=\s*)(['])(images/[^'\"]+)(['])")

# Chapter-identity pattern bank: first match (per site) wins.
# Keys are (kind, value); RR /chapter/<id>/ is the canonical one.
ID_BANK = (
    ('rr', re.compile(r'/chapter/(?P<id>\d+)/')),
    ('ao3', re.compile(r'/chapters/(?P<id>\d+)/')),
    ('ffn', re.compile(r'/s/\d+/(?P<id>\d+)/')),
)


def chapter_key(url):
    '''Stable chapter identity: the numeric id from a known URL pattern
    (retitles / slug changes keep the id), else the full URL, else a
    (title,index) fallback.'''
    if url:
        for kind, pat in ID_BANK:
            m = pat.search(url)
            if m:
                return (kind, m.group('id'))
        return ('url', url)
    return ('empty', '')


def _key_id_display(key):
    if isinstance(key, tuple) and len(key) == 2 and key[0] in (
            'rr', 'ao3', 'ffn'):
        try:
            return int(key[1])
        except (TypeError, ValueError):
            return key[1]
    return key[1] if isinstance(key, tuple) and len(key) == 2 else str(key)


def _date_key(source):
    '''Sortable recency proxy: latest of dateUpdated/dateCreated/mtime.'''
    best = 0.0
    for field in ('dateUpdated', 'dateCreated'):
        ts = _parse_ts((source.get('metadata') or {}).get(field, ''))
        if ts:
            best = max(best, ts)
    mtime = source.get('epub_mtime') or 0
    return max(best, float(mtime))


def _parse_ts(value):
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(str(value), fmt).timestamp()
        except (ValueError, TypeError):
            continue
    return None


def _parse_date(value):
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(str(value), fmt)
        except (ValueError, TypeError):
            continue
    return None


def load_source(path):
    '''Turn an epub (auto-deconstruct to a temp parts dir) or a parts
    dir into a reconstructed recon dict with '_parts_dir' set.'''
    if str(path).lower().endswith('.epub'):
        parts = tempfile.mkdtemp(prefix='merge_in_')
        deconstruct(path, parts)
    else:
        parts = path
    recon_path = os.path.join(parts, 'reconstruction.json')
    if not os.path.exists(recon_path):
        raise DeconstructError('no reconstruction.json in %s' % parts)
    with open(recon_path, 'r', encoding='utf-8') as fh:
        recon = json.load(fh)
    recon['_parts_dir'] = parts
    recon['_input'] = path
    return recon


def _read_source_file(recon, rel):
    full = os.path.join(recon['_parts_dir'], 'files', rel.replace('/', os.sep))
    if os.path.exists(full):
        with open(full, 'rb') as fh:
            return fh.read()
    return None


def _read_chapter_body(recon, rec):
    full = os.path.join(recon['_parts_dir'], rec['file'].replace('/', os.sep))
    if not os.path.exists(full):
        raise DeconstructError('missing chapter body: %s' % rec['file'])
    with open(full, 'r', encoding='utf-8', errors='replace') as fh:
        return fh.read()


def word_count(body):
    return len(re.findall(r'\S+', body or ''))


def compute_chapter_hash(html_content):
    '''Mirror of FanFicFare BaseAdapter.compute_chapter_hash so chapters
    whose source epub lacked hash metas get the same hash as a fresh
    site download would produce (keeps the merged epub update-clean).'''
    if not html_content:
        return ''
    try:
        from bs4 import BeautifulSoup as _BS
        from fanficfare.htmlcleanup import stripHTML
        text = stripHTML(_BS(html_content, 'html.parser'))
    except Exception:
        try:
            text = re.sub(r'<[^>]+>', ' ', html_content)
        except Exception:
            text = html_content
    text = re.sub(r'\s+', ' ', text).strip()
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _merge_sequences(keys_a, keys_b):
    '''Git-style LCS sequence merge of two chapter-key sequences.

    Anchors are the LCS common subsequence; between consecutive anchors
    emit key A's unique items (in A order) then key B's unique items (in
    B order), so the older side always precedes the newer side.'''
    sm = difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            out.extend(keys_a[i1:i2])
        elif tag == 'replace':
            out.extend(keys_a[i1:i2])
            out.extend(keys_b[j1:j2])
        elif tag == 'delete':
            out.extend(keys_a[i1:i2])
        elif tag == 'insert':
            out.extend(keys_b[j1:j2])
    return out


def _check_same_book(sources):
    site = sources[0].get('site', '')
    storyid = (sources[0].get('metadata') or {}).get('storyId', '')
    for i, src in enumerate(sources[1:], 1):
        s_site = src.get('site', '')
        s_id = (src.get('metadata') or {}).get('storyId', '')
        if storyid and s_id and (s_site != site or s_id != storyid):
            raise DeconstructError(
                'source %d is a different book (%s / storyId %s != %s / %s)'
                % (i, s_site, s_id, site, storyid))


def _union_list(*lists):
    out = []
    seen = set()
    for lst in lists:
        for item in lst or []:
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def _canonical_base_for_sha(img_indexes, sha):
    '''Preferred display basename for a content hash: base source first,
    then earlier CLI args.'''
    for idx in img_indexes:
        if idx is None:
            continue
        for base, rec in idx.items():
            if rec['sha'] == sha:
                return base
    return None


def _rewrite_img_refs(body, mapping):
    '''Replace every src="images/X" using mapping[X]->new name.'''
    def repl(m):
        prefix, quote, ref, close = m.group(1), m.group(2), m.group(3), m.group(4)
        newname = mapping.get(ref)
        if not newname:
            return m.group(0)
        return '%s%s%s%s' % (prefix, quote, 'images/' + newname, close)

    body = IMG_SRC_RE.sub(repl, body)
    body = SINGLE_QUOTE.sub(repl, body)
    return body


def merge(source_paths, parts_out=None, out_epub=None, no_reconstruct=False):
    '''Merge 2+ epubs/parts dirs of the same book into a single,
    more complete FFF epub.'''

    if len(source_paths) < 2:
        raise DeconstructError('merge needs at least two sources')

    sources = [load_source(p) for p in source_paths]
    _check_same_book(sources)

    # --- recency / overlap selection -----------------------------
    recency = [_date_key(s) for s in sources]
    newest_idx = max(range(len(sources)), key=lambda i: (recency[i], i))

    base = sources[0]
    newest = sources[newest_idx]

    # chapter records per source, keyed; sources' chapter-key sequences.
    seqs = []
    by_key = {}
    for i, src in enumerate(sources):
        seq = []
        for rec in src.get('chapters', []):
            key = chapter_key(rec.get('url', ''))
            seq.append(key)
            body = _read_chapter_body(src, rec)
            entry = {
                'source': i, 'rec': rec, 'body': body,
                'words': word_count(body),
            }
            by_key.setdefault(key, []).append(entry)
        seqs.append(seq)

    # selection: newest wins, ranked by (recency desc, arg index desc).
    selections = {}
    for key, entries in by_key.items():
        entries.sort(key=lambda e: (recency[e['source']], e['source']),
                     reverse=True)
        chosen = entries[0]
        bodies = [e['body'] for e in entries]
        identical = len(set(bodies)) <= 1
        selections[key] = {
            'chosen': chosen,
            'sources': [e['source'] for e in entries],
            'identical': identical,
        }

    # --- ordering (LCS sequence merge) ---------------------------
    merged_keys = list(seqs[0])
    for i in range(1, len(sources)):
        merged_keys = _merge_sequences(merged_keys, seqs[i])

    # --- image plan ----------------------------------------------
    img_indexes = []   # per source: basename -> {sha, path, mime, data}
    for src in sources:
        idx = {}
        for r in src.get('images', []) or []:
            base_n = os.path.basename((r.get('newsrc') or '').split('?')[0])
            data = _read_source_file(src, r.get('path') or r.get('newsrc'))
            if base_n and data is not None:
                idx[base_n] = {
                    'sha': sha256_bytes(data),
                    'path': r.get('path') or r.get('newsrc'),
                    'mime': r.get('mime', ''),
                    'data': data,
                }
        img_indexes.append(idx)

    emitted_images = {}   # basename -> {'data','mime','sha'}
    image_report = []

    # Cover from the newest source (fall back to base).
    cover = None
    for i in (newest_idx, 0):
        cov = sources[i].get('cover')
        if cov and cov.get('newsrc'):
            data = _read_source_file(sources[i], cov.get('path') or cov['newsrc'])
            if data is not None:
                cover = {
                    'newsrc': cov['newsrc'],
                    'path': cov.get('path') or cov['newsrc'],
                    'mime': cov.get('mime', 'image/jpeg'),
                    'data': data,
                    'source': i,
                    'sha': sha256_bytes(data),
                }
                break

    ordered = []   # each element {key, chapter_index(1-based) order}
    chapter_files = []   # per merged chapter: {key, order, chosen...}

    order = 0
    for key in merged_keys:
        order += 1
        sel = selections[key]
        e = sel['chosen']
        body = e['body']

        # this chapter's image refs -> renamed targets
        idx = img_indexes[e['source']]
        mapping = {}
        for m in re.finditer(r'images/([^"\'\s]+)', body):
            ref = m.group(1)
            if ref in mapping:
                continue
            src_rec = idx.get(ref)
            if not src_rec:
                continue
            canonical = _canonical_base_for_sha(img_indexes, src_rec['sha'])
            newname = 'ch%s-%s' % (_key_id_display(key),
                                    canonical or ref)
            mapping['images/%s' % ref] = newname
        new_body = _rewrite_img_refs(body, mapping)

        for old, new in mapping.items():
            image_report.append({
                'chapter_id': _key_id_display(key),
                'chapter_order': order,
                'from': old,
                'to': 'images/%s' % new,
                'sha': img_indexes[e['source']].get(old[len('images/'):],
                                                     {}).get('sha'),
                'source': e['source'],
            })
            if new not in emitted_images and old[len('images/'):] and \
                    old[len('images/'):] in img_indexes[e['source']]:
                rec2 = img_indexes[e['source']][old[len('images/'):]]
                emitted_images[new] = {
                    'data': rec2['data'], 'mime': rec2['mime'],
                    'sha': rec2['sha'],
                }

        hash_val = (e['rec'].get('hash') or '').strip()
        lastcheck = (e['rec'].get('lastcheck') or '').strip()
        if not hash_val:
            hash_val = compute_chapter_hash(body)
            if hash_val:
                # The chapter was last checked when its source epub was
                # downloaded, not at merge time: use the source epub's
                # mtime as the lastcheck timestamp.
                mt = sources[e['source']].get('epub_mtime')
                if mt:
                    lastcheck = datetime.datetime.fromtimestamp(
                        mt).strftime('%Y-%m-%d %H:%M:%S')
        hash_source = 'preserved' if (
            (e['rec'].get('hash') or '').strip()) else 'computed'

        chapter_files.append({
            'key': key,
            'id': _key_id_display(key),
            'order': order,
            'source': e['source'],
            'rec': e['rec'],
            'body': new_body,
            'orig_body': body,
            'mapping': mapping,
            'hash': hash_val,
            'lastcheck': lastcheck,
            'hash_source': hash_source,
        })

    # Union-carry images never referenced by any surviving body, using
    # their original names (renamed files carry a ch#- prefix, so no
    # name clashes).  Skips duplicates by (name + content).
    seen_names = set(emitted_images)
    for i, src in enumerate(sources):
        for r in src.get('images', []) or []:
            base_n = os.path.basename((r.get('newsrc') or '').split('?')[0])
            if not base_n or base_n in seen_names:
                continue
            imgidx = img_indexes[i].get(base_n)
            if imgidx is None:
                continue
            if cover and cover['sha'] and imgidx['sha'] == cover['sha']:
                continue
            emitted_images[base_n] = {
                'data': imgidx['data'], 'mime': imgidx['mime'],
                'sha': imgidx['sha'],
            }
            seen_names.add(base_n)

    merged_images = [{
        'newsrc': 'images/%s' % name,
        'path': 'OEBPS/images/%s' % name,
        'mime': rec['mime'],
        'data': rec['data'],
    } for name, rec in sorted(emitted_images.items())]

    # --- css: newest source verbatim -----------------------------
    css_rel = base.get('css_path') or newest.get('css_path') or ''
    css_bytes = None
    if newest.get('css_path'):
        css_bytes = _read_source_file(newest, newest['css_path'])
    if css_bytes is None and base.get('css_path'):
        css_bytes = _read_source_file(base, base['css_path'])
        css_rel = base['css_path']
    if css_bytes is None:
        css_bytes = b'' if not css_rel else b''

    # --- metadata -------------------------------------------------
    bmd = base.get('metadata', {})
    nmd = newest.get('metadata', {})

    published = None
    for src in sources:
        d = _parse_date((src.get('metadata') or {}).get('datePublished', ''))
        if d and (published is None or d < published):
            published = d
            datePublished_str = (src.get('metadata') or {}).get(
                'datePublished', '')

    final_words = sum(word_count(cf['body']) for cf in chapter_files)

    metadata = {
        'title': nmd.get('title') or bmd.get('title', ''),
        'author': list(bmd.get('author') or []),
        'authorId': list(bmd.get('authorId') or []),
        'authorUrl': list(bmd.get('authorUrl') or []),
        'storyId': bmd.get('storyId', ''),
        'langcode': bmd.get('langcode', 'en'),
        'datePublished': datePublished_str if published else
        (nmd.get('datePublished') or bmd.get('datePublished', '')),
        'dateCreated': nmd.get('dateCreated') or bmd.get('dateCreated', ''),
        'dateUpdated': nmd.get('dateUpdated') or bmd.get('dateUpdated', ''),
        'description': nmd.get('description') or bmd.get('description', ''),
        'category': _union_list(bmd.get('category'),
                                nmd.get('category')),
        'genre': _union_list(bmd.get('genre'), nmd.get('genre')),
        'status': nmd.get('status') or bmd.get('status', ''),
        'numChapters': len(chapter_files),
        'numWords': final_words,
        'publisher': bmd.get('publisher', ''),
        'series': bmd.get('series', ''),
        'series_index': bmd.get('series_index', ''),
    }

    settings = dict(base.get('settings', {}))
    nset = newest.get('settings', {})
    for field in ('titlepage_entries', 'titlepage_use_table',
                  'epub_version'):
        if nset.get(field) is not None:
            settings[field] = nset[field]

    # Hash-capable template only needs a chapterhash; an empty
    # chapterlastcheck is written as content="" which FFF treats as
    # "no recheck window" (update_check_chapter_age_days defaults to 0).
    has_hashes = bool(chapter_files) and all(
        cf['hash'] for cf in chapter_files)

    recon = {
        'format_version': 1,
        'original_filename': 'merged-%d-chapters.epub' % len(chapter_files),
        'epub_mtime': max(s.get('epub_mtime') or 0 for s in sources),
        'rootdir': base.get('rootdir', ''),
        'site': base.get('site', ''),
        'storyUrl': base.get('storyUrl', ''),
        'metadata': metadata,
        'subjects': _union_list(base.get('subjects'),
                                *[s.get('subjects') for s in sources[1:]]),
        'has_chapter_hashes': has_hashes,
        'chapters': [
            {
                'file': 'chapters/%04d.html' % cf['order'],
                'url': cf['rec'].get('url', ''),
                'title': cf['rec'].get('title', ''),
                'origtitle': cf['rec'].get('origtitle',
                                            cf['rec'].get('title', '')),
                'toctitle': cf['rec'].get('toctitle',
                                          cf['rec'].get('title', '')),
                'hash': cf['hash'],
                'lastcheck': cf['lastcheck'],
            }
            for cf in chapter_files
        ],
        'cover': ({
            'newsrc': cover['newsrc'],
            'path': 'OEBPS/images/%s' % os.path.basename(
                cover['newsrc'].split('?')[0]),
            'mime': cover.get('mime', 'image/jpeg'),
        } if cover else None),
        'images': [{'newsrc': im['newsrc'], 'path': im['path'],
                    'mime': im['mime']} for im in merged_images],
        'css_path': css_rel,
        'settings': settings,
        'calibre_bookmarks': base.get('calibre_bookmarks', ''),
        'log_page': base.get('log_page', ''),
        'entry_order': list(base.get('entry_order') or []),
    }

    # --- emit merged parts ----------------------------------------
    if parts_out is None:
        parts_out = tempfile.mkdtemp(prefix='merge_out_')
    out_files = os.path.join(parts_out, 'files')
    out_chapters = os.path.join(parts_out, 'chapters')
    os.makedirs(out_files, exist_ok=True)
    os.makedirs(out_chapters, exist_ok=True)

    base_files = os.path.join(base['_parts_dir'], 'files')
    if os.path.isdir(base_files):
        shutil.copytree(base_files, out_files, dirs_exist_ok=True)

    for im in merged_images:
        target = os.path.join(out_files, im['path'].replace('/', os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as fh:
            fh.write(im['data'])

    if cover:
        target = os.path.join(out_files, recon['cover']['path'].replace(
            '/', os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as fh:
            fh.write(cover['data'])

    if css_bytes is not None and css_rel:
        target = os.path.join(out_files, css_rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as fh:
            fh.write(css_bytes)

    for cf in chapter_files:
        target = os.path.join(out_chapters, '%04d.html' % cf['order'])
        with open(target, 'w', encoding='utf-8') as fh:
            fh.write(cf['body'])

    recon['_parts_dir'] = os.path.abspath(parts_out)
    with open(os.path.join(parts_out, 'reconstruction.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(recon, fh, indent=2, sort_keys=False)

    # --- report ---------------------------------------------------
    segments = []
    if chapter_files:
        run_start = 0
        for i in range(1, len(chapter_files) + 1):
            if i == len(chapter_files) or \
                    chapter_files[i]['source'] != chapter_files[run_start]['source']:
                seg = chapter_files[run_start:i]
                segments.append({
                    'source': seg[0]['source'],
                    'from': seg[0]['id'],
                    'to': seg[-1]['id'],
                    'count': len(seg),
                })
                run_start = i

    union_keys = set().union(*[set(s) for s in seqs])
    hash_source_by_key = {cf['key']: cf['hash_source']
                          for cf in chapter_files}
    report = {
        'sources': [
            {
                'index': i,
                'input': str(sources[i].get('_input')),
                'chapters': len(seqs[i]),
                'dateUpdated': (sources[i].get('metadata') or {}).get(
                    'dateUpdated', ''),
                'dateCreated': (sources[i].get('metadata') or {}).get(
                    'dateCreated', ''),
            }
            for i in range(len(sources))
        ],
        'newest_source': newest_idx,
        'union_chapters': len(union_keys),
        'merged_chapters': len(chapter_files),
        'match_union': set(merged_keys) == union_keys and \
            len(merged_keys) == len(union_keys),
        'numWords': final_words,
        'hashes_policy': 'keep hashes' if has_hashes else 'hash-free',
        'hash_counts': {
            v: sum(1 for cf in chapter_files if cf['hash_source'] == v)
            for v in ('preserved', 'computed')
        },
        'selections': {
            '%s' % _key_id_display(key): {
                'sources': sel['sources'],
                'identical': sel['identical'],
                'chosen': sel['chosen']['source'],
                'hash': hash_source_by_key.get(
                    key, 'none'),
            }
            for key, sel in sorted(selections.items(),
                                   key=lambda kv: _key_id_display(kv[0]))
        },
        'image_renames': image_report,
        'segments': segments,
    }
    with open(os.path.join(parts_out, 'merge_report.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(report, fh, indent=2, sort_keys=False)

    print('merged %d sources -> %s parts' % (len(sources), parts_out))
    print('  union chapters: %d  merged: %d  match_union: %s' % (
        len(union_keys), len(chapter_files),
        report['match_union'] and len(merged_keys) == len(union_keys)))
    print('  newest source: %d  numWords: %d  image files: %d' % (
        newest_idx, final_words, len(emitted_images)))

    if out_epub and not no_reconstruct:
        reconstruct(parts_out, out_epub)
    elif not out_epub and not no_reconstruct:
        print('  (no --out given: parts only, reconstruct skipped)')

    return recon, report, parts_out