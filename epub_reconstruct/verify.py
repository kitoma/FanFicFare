# -*- coding: utf-8 -*-

import json
import os
import re
from zipfile import ZipFile

from .util import (
    parse_opf, parse_chapter_file, parse_container, parse_ncx,
    sha256_bytes,
)

# Pages the EpubWriter regenerates from metadata/chapters, so textual
# differences (an older FFF writer vintage, different templates) are
# expected and don't count against the round trip.
REGENERATED = {'title_page.xhtml', 'log_page.xhtml', 'toc_page.xhtml',
               'nav.xhtml'}


def _load_recon(parts_dir):
    path = os.path.join(parts_dir, 'reconstruction.json')
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def verify(orig_epub, new_epub, parts_dir=None):
    recon = _load_recon(parts_dir) if parts_dir else None

    zfo = ZipFile(orig_epub, 'r')
    zfn = ZipFile(new_epub, 'r')

    o_names = zfo.namelist()
    n_names = zfn.namelist()

    o_set, n_set = set(o_names), set(n_names)
    missing = sorted(o_set - n_set)
    extra = sorted(n_set - o_set)
    order_same = o_names == n_names

    equal_entries = []
    diff_entries = []   # (name, reason)
    chapter_bodies_equal = {}
    hash_compare = {}   # name -> ('equal','orig-missing','new-missing','diff')
    semantic = {}       # name -> ('ok',) or ('diff', [messages])

    for name in o_names:
        if name not in n_set:
            continue
        od, nd = zfo.read(name), zfn.read(name)
        if od == nd:
            equal_entries.append(name)
            continue
        base = os.path.basename(name)
        if base in REGENERATED:
            diff_entries.append((name, 'regenerated page differs'))
            continue
        if base == 'content.opf':
            semantic[name] = _cmp_opf(parse_opf(od), parse_opf(nd))
            continue
        if base == 'toc.ncx':
            semantic[name] = _cmp_ncx(parse_ncx(od), parse_ncx(nd))
            continue
        if base == 'container.xml':
            semantic[name] = _cmp_container(parse_container(od),
                                            parse_container(nd))
            continue
        if re.match(r'^file\d+\.xhtml$', base) and 'OEBPS/' in name:
            op = parse_chapter_file(od.decode('utf-8', 'replace'))
            np_ = parse_chapter_file(nd.decode('utf-8', 'replace'))
            if op and np_:
                o_body = re.sub(r'\s+', '', str(op.get('body', '')))
                n_body = re.sub(r'\s+', '', str(np_.get('body', '')))
                if o_body == n_body and op['title'] == np_['title']:
                    chapter_bodies_equal[name] = True
                    if op.get('hash') and not np_.get('hash'):
                        hash_compare[name] = 'new-missing'
                    elif op.get('hash') and op['hash'] == np_['hash']:
                        hash_compare[name] = 'equal'
                    elif op.get('hash'):
                        hash_compare[name] = 'diff'
                    elif not op.get('hash'):
                        hash_compare[name] = 'orig-missing'
                    continue
                diff_entries.append((name, 'chapter body/title difference'))
                continue
        diff_entries.append((name, 'bytes differ'))

    for name in n_names:
        if name not in o_set and os.path.basename(name) not in \
                ('title_page.xhtml', 'log_page.xhtml', 'toc_page.xhtml',
                 'nav.xhtml'):
            # extra entry (already reported) -- keep deterministic
            pass

    meta_report = []
    for name, result in semantic.items():
        if result[0] == 'diff':
            for msg in result[1]:
                meta_report.append('%s: %s' % (name, msg))

    # mtime
    mtime_report = ''
    if recon and os.path.exists(new_epub):
        got = os.path.getmtime(new_epub)
        want = recon.get('epub_mtime')
        # Some filesystems only keep whole-second mtimes, so tolerate a
        # match of the truncated second as well.
        if not (abs(got - want) <= 1.0 and int(got) == int(want)):
            mtime_report = 'mtime: %.1f != %.1f' % (got, want)

    # verdict: only non-regenerated diffs, missing/extra entries,
    # metadata differences or mtime mismatch fail the round trip.
    hard = [d for d in diff_entries if d[1] != 'regenerated page differs']
    convg = (not missing and not extra and not meta_report
             and not mtime_report and not hard)

    lines = []
    lines.append('=== entry set ===')
    lines.append('missing entries: %d  extra: %d  order%s' % (
        len(missing), len(extra), '' if order_same else ' (DIFFERENT ORDER)'))
    for m in missing:
        lines.append('  missing: %s' % m)
    for e in extra:
        lines.append('  extra:   %s' % e)
    lines.append('=== bytes ===')
    lines.append('byte-identical entries: %d / %d' % (
        len(equal_entries), len(o_names)))
    for name, why in diff_entries:
        lines.append('  DIFF %s: %s' % (name, why))
    if chapter_bodies_equal:
        lines.append('=== chapters ===')
        lines.append('chapter bodies equal: %d' % len(chapter_bodies_equal))
        for name, status in hash_compare.items():
            if status == 'equal':
                continue
            if status == 'orig-missing':
                lines.append('  %s: hash skipped (orig has none)' % name)
                continue
            lines.append('  %s: hash %s' % (name, status))
    if meta_report:
        lines.append('=== metadata ===')
        for r in meta_report:
            lines.append('  %s' % r)
    if mtime_report:
        lines.append('=== mtime ===')
        lines.append('  %s' % mtime_report)

    print('\n'.join(lines))
    verdict = 'PASS' if convg else 'DIFF'
    print('=== verdict: %s ===' % verdict)
    return convg


def _cmp_opf(om, nm):
    '''Semantic comparison of two parsed content.opf dicts.'''
    diffs = []
    for field in ('version', 'unique_id_val'):
        if om.get(field) != nm.get(field):
            diffs.append('%s: %r != %r' % (field, om.get(field),
                                           nm.get(field)))
    ometa, nmeta = om.get('metadata', {}), nm.get('metadata', {})
    for field in ('title', 'creator', 'language', 'description',
                  'datePublication', 'dateCreation', 'dateModification',
                  'publisher', 'source', 'identifier'):
        if ometa.get(field) != nmeta.get(field):
            diffs.append('metadata %s: differ' % field)
    if om.get('subjects') != nm.get('subjects'):
        diffs.append('subjects: %r != %r' % (om.get('subjects'),
                                             nm.get('subjects')))
    ometas, nmetas = om.get('metas', {}), nm.get('metas', {})
    for field in ('calibre:series', 'calibre:series_index',
                  'calibre:timestamp', 'cover'):
        if ometas.get(field) != nmetas.get(field):
            diffs.append('meta %s: %r != %r' % (
                field, ometas.get(field), nmetas.get(field)))
    omani = sorted((i, m['href'], m['type'])
                   for i, m in om.get('manifest', {}).items())
    nmani = sorted((i, m['href'], m['type'])
                   for i, m in nm.get('manifest', {}).items())
    if omani != nmani:
        diffs.append('manifest: differs')
    if om.get('spine') != nm.get('spine'):
        diffs.append('spine order: differs')
    return ('ok', []) if not diffs else ('diff', diffs)


def _cmp_ncx(a, b):
    diffs = []
    if a.get('docTitle') != b.get('docTitle'):
        diffs.append('docTitle differs')
    if a.get('navPoints') != b.get('navPoints'):
        diffs.append('navPoint titles/order differ')
    return ('ok', []) if not diffs else ('diff', diffs)


def _cmp_container(a, b):
    diffs = []
    if a != b:
        diffs.append('%r != %r' % (a, b))
    return ('ok', []) if not diffs else ('diff', diffs)