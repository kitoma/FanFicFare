# -*- coding: utf-8 -*-

import datetime
import os
import posixpath
import re

from xml.dom.minidom import parseString


CHAPTER_H3_RE = re.compile(r'<h3 class="fff_chapter_title">.*?</h3>\n', re.DOTALL)
BODY_END_RE = re.compile(r'\n</body>\s*</html>\s*$', re.DOTALL)
META_RE = re.compile(r'<meta name="(chapter[a-z]+)" content="([^"]*)"\s*/>')
NOT_CHAPTER = ('cover.xhtml', 'title_page.xhtml', 'toc_page.xhtml',
               'log_page.xhtml', 'nav.xhtml')
DATE_EVENTS = {
    'publication': 'datePublication',
    'creation': 'dateCreation',
    'modification': 'dateModification',
}


class DeconstructError(Exception):
    pass


def read_zip_entry(zf, path):
    try:
        return zf.read(path)
    except KeyError:
        raise DeconstructError('missing zip entry: %s' % path)


def get_opf_path(zf):
    '''Find the package opf path via META-INF/container.xml and the
    root component directory ('' if the opf sits at the zip root).'''
    container = read_zip_entry(zf, 'META-INF/container.xml')
    dom = parseString(container)
    try:
        rootfile = dom.getElementsByTagName('rootfile')[0]
        opf_path = rootfile.getAttribute('full-path')
    except Exception:
        raise DeconstructError('cannot parse META-INF/container.xml')
    finally:
        dom.unlink()
    rootdir = os.path.dirname(opf_path)
    return opf_path, (rootdir if rootdir else '')


def el_text(el, default=''):
    if el is None:
        return default
    texts = [node.data for node in el.childNodes
             if node.nodeType == node.TEXT_NODE]
    return ''.join(texts)


def parse_opf(opf_bytes):
    '''Parse an FFF-generated content.opf into a plain dict.

    Metadata keys are stripped of the 'dc:' prefix; opf:event dates are
    exposed as datePublication/dateCreation/dateModification.  Also
    returns subjects (ordered), metas (name->content), manifest
    {id: {href,type,properties}} and spine (idref order).
    '''
    dom = parseString(opf_bytes)
    try:
        package = dom.documentElement
        version = package.getAttribute('version') or '2.0'
        uniqueid_attr = package.getAttribute('unique-identifier') or \
            'fanficfare-uid'
        unique_id_val = ''
        metadata = {}
        metas = {}
        subjects = []
        manifest = {}
        spine = []
        for child in package.childNodes:
            if child.nodeType != child.ELEMENT_NODE:
                continue
            if child.tagName == 'metadata':
                for el in child.childNodes:
                    if el.nodeType != el.ELEMENT_NODE:
                        continue
                    tag = el.tagName
                    if tag == 'meta':
                        name = el.getAttribute('name')
                        content = el.getAttribute('content')
                        if name:
                            metas[name] = content
                            if el.getAttribute('id') == uniqueid_attr:
                                unique_id_val = content
                        continue
                    if tag.startswith('dc:'):
                        key = tag[3:]
                        if key == 'subject':
                            subjects.append(el_text(el))
                            continue
                        if key == 'date':
                            key = DATE_EVENTS.get(
                                el.getAttribute('opf:event'), 'dateEvent')
                    else:
                        key = tag
                    value = el_text(el)
                    if key in metadata:
                        if not isinstance(metadata[key], list):
                            metadata[key] = [metadata[key]]
                        metadata[key].append(value)
                    else:
                        metadata[key] = value
                    if el.getAttribute('id') == uniqueid_attr:
                        unique_id_val = value
            elif child.tagName == 'manifest':
                for el in child.childNodes:
                    if el.nodeType != el.ELEMENT_NODE:
                        continue
                    manifest[el.getAttribute('id')] = {
                        'href': el.getAttribute('href'),
                        'type': el.getAttribute('media-type'),
                        'properties': el.getAttribute('properties'),
                    }
            elif child.tagName == 'spine':
                for el in child.childNodes:
                    if el.nodeType != el.ELEMENT_NODE:
                        continue
                    spine.append(el.getAttribute('idref'))
        return {
            'version': version,
            'unique_id_val': unique_id_val,
            'metadata': metadata,
            'subjects': subjects,
            'metas': metas,
            'manifest': manifest,
            'spine': spine,
        }
    finally:
        dom.unlink()


def entry_path(rootdir, href):
    '''Full zip entry path for a manifest href.

    FFF writes manifest hrefs that are usually relative to the OEBPS/
    directory but may also already be absolute zip paths.'''
    href = href.replace('\\', '/')
    if rootdir:
        prefix = rootdir.rstrip('/') + '/'
        if href.startswith(prefix) or not href.startswith('OEBPS/'):
            if href == rootdir.rstrip('/'):
                return href
            if not href.startswith(prefix):
                return posixpath.join(rootdir, href).replace('\\', '/')
    return posixpath.normpath(href).replace('\\', '/')


def to_newsrc(href, rootdir):
    '''The epub-relative path FFF uses internally (its href for the
    img_store newsrc and story.cover), 'OEBPS/' prefix stripped.'''
    href = href.replace('\\', '/')
    prefix = (rootdir.rstrip('/') + '/') if rootdir else ''
    for p in (prefix, 'OEBPS/'):
        if p and href.startswith(p):
            return href[len(p):]
    return href


def slice_body(html_text):
    '''Return the FFF chapter body: everything between the closing
    '</h3>' newline of the fff_chapter_title header and the final
    '</body>'.  Returns None if the markers are absent.'''
    m = CHAPTER_H3_RE.search(html_text)
    if not m:
        return None
    start = m.end()
    endm = BODY_END_RE.search(html_text)
    if not endm:
        return None
    return html_text[start:endm.start()]


def parse_chapter_file(html_text):
    '''Extract chapter head-metas and body from a chapter xhtml file.

    Returns a dict with the metas ('' when absent) and 'body' (the
    exact sliced body) -- or None if this isn't a chapter file.'''
    metas = dict(META_RE.findall(html_text))
    body = slice_body(html_text)
    if body is None and not metas:
        return None
    title = metas.get('chaptertitle') or metas.get('chapterorigtitle') \
        or metas.get('chaptertoctitle') or ''
    return {
        'url': metas.get('chapterurl', ''),
        'title': title,
        'origtitle': metas.get('chapterorigtitle', title),
        'toctitle': metas.get('chaptertoctitle', title),
        'hash': metas.get('chapterhash', ''),
        'lastcheck': metas.get('chapterlastcheck', ''),
        'body': body if body is not None else '',
    }


def looks_like_chapter(name):
    '''Basename check mirroring FFF's chapter-name heuristics.'''
    base = os.path.basename(name)
    if base in NOT_CHAPTER:
        return False
    if re.match(r'^(file|chapter)\d+\.(xhtml|html)$', base):
        return True
    return False


def _parse_date(value):
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


def recover_from_uniqueid(unique_id_val):
    '''Parse 'fanficfare-uid:<site>-u<authorId>-s<storyId>'.

    Returns (site, authorId, storyId) with '' for missing pieces.'''
    site = authorid = storyid = ''
    if unique_id_val:
        body = unique_id_val.split(':', 1)[1] if ':' in unique_id_val \
            else unique_id_val
        m = re.match(
            r'^(?P<site>[^-]+)-u(?P<author>\d+)-s(?P<story>\d+)$', body)
        if m:
            site = m.group('site')
            authorid = m.group('author')
            storyid = m.group('story')
    return site, authorid, storyid


def recover_site_from_url(story_url):
    try:
        from urllib.parse import urlparse
        host = urlparse(story_url).netloc
        if host:
            return host
    except Exception:
        pass
    return ''


def parse_title_page(html_text, reverse_labels=None):
    '''Recover the ordered title-page entries and per-entry values.

    Returns dict with:
      entries: ordered list of entry-keys (or raw label strings when a
               label isn't a known metadata key)
      values:  {entry_key: rendered value string}
      storyUrl / authorUrls / title  ('' when absent)
      use_table: whether the page uses the table layout
    '''
    use_table = '<table class="full">' in html_text
    reverse_labels = reverse_labels or {}
    items = list(re.finditer(r'<b>(.*?):</b>', html_text))
    entries = []
    values = {}
    for i, m in enumerate(items):
        label = m.group(1)
        entry = reverse_labels.get(label)
        if entry is None:
            entry = label
        entries.append(entry)
        end = items[i + 1].start() if i + 1 < len(items) else len(html_text)
        value = html_text[m.end():end]
        value = value.replace('<br />', '').replace('<br/>', '')
        # last entry runs to the end of the file -- drop page tail.
        value = re.sub(r'</div>\s*</body>\s*</html>\s*$', '', value)
        value = value.strip()
        if value:
            values[entry] = value

    storyurl = authorurl = title = ''
    murl = re.search(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                     html_text, re.DOTALL)
    if murl:
        storyurl = murl.group(1)
        title = murl.group(2).strip()
    authorurls = re.findall(
        r'<a[^>]+class=["\']authorlink["\'][^>]+href=["\']([^"\']+)["\']',
        html_text)
    return {
        'entries': entries,
        'values': values,
        'storyUrl': storyurl,
        'authorUrls': authorurls,
        'title': title,
        'use_table': use_table,
    }


def parse_ncx(ncx_bytes):
    '''Return ordered navPoint text values and the docTitle, for
    semantic comparison (ncx XML escaping/whitespace may differ
    between writer vintages).'''
    dom = parseString(ncx_bytes)
    try:
        titles = []
        for np in dom.getElementsByTagName('navPoint'):
            texts = np.getElementsByTagName('text')
            if texts:
                titles.append(el_text(texts[0]))
        docelem = dom.getElementsByTagName('docTitle')
        doctitle = el_text(docelem[0].getElementsByTagName('text')[0]) \
            if docelem and docelem[0].getElementsByTagName('text') else ''
        return {'docTitle': doctitle, 'navPoints': titles}
    finally:
        dom.unlink()


def parse_container(container_bytes):
    dom = parseString(container_bytes)
    try:
        rootfiles = dom.getElementsByTagName('rootfile')
        if not rootfiles:
            return {}
        rf = rootfiles[0]
        return {'full-path': rf.getAttribute('full-path'),
                'media-type': rf.getAttribute('media-type')}
    finally:
        dom.unlink()


def sha256_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()