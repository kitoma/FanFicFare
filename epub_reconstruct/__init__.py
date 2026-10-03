# -*- coding: utf-8 -*-

# Deconstruct an FFF-generated epub into its constituent parts, then
# reconstruct it from those parts using FanFicFare's own EpubWriter,
# or merge several epubs / parts dirs of the same book into one more
# complete epub.
#
# This is intentionally NOT part of the FanFicFare plugin -- it's a
# separate module that reuses FFF's writer machinery to prove a book
# can be re-created from itself and to merge epubs of the same book
# that hold different chapter groups.
#
# v0.3.0 (2026-10-03): adapted to hash-free FFF >= v4.62 (PR #1414).
# The v4.62 EpubWriter cannot emit chapterhash/chapterlastcheck metas,
# so reconstruction no longer injects them, merge no longer preserves/
# computes them, and verify treats them as presence-parity only.

__version__ = '0.3.0'