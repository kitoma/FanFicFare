"""Offline tests for the update-check (edit detection) half of the
incremental-update feature: re-downloading recently-got chapters to
detect author edits, restoring locally-edited chapters, and the
recheck_active() gating used by cli/jobs/fff_plugin.

Everything is served by the shared staged adapters in update_harness so
no network fetching takes place.
"""
import io
import re
import zipfile

import pytest

from tests.adapters.update_harness import (CH, STORY_URL,
                                           StagedSiteAdapter, chapter_hash,
                                           read_chapters, staged_config,
                                           staged_download, staged_update)


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


def test_recheck_active_predicate(tmp_path):
    # No recheck window / age threshold set: edit detection is off and
    # the caller gating (cli.py / jobs.py / fff_plugin.py) must not
    # treat the update as needing re-downloads.
    config = staged_config(tmp_path, recent='0')
    assert StagedSiteAdapter(config, STORY_URL).recheck_active() is False

    # A recent-chapters window activates edit detection...
    config = staged_config(tmp_path, recent='5')
    assert StagedSiteAdapter(config, STORY_URL).recheck_active() is True

    # ...and so does an age threshold on its own.
    config = staged_config(tmp_path, recent='0', age='30')
    assert StagedSiteAdapter(config, STORY_URL).recheck_active() is True


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
    # Explicit recent window (staged_config defaults to recent='0' to
    # match defaults.ini): this combined test needs edit-checks active.
    config = staged_config(tmp_path, recent='5')

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