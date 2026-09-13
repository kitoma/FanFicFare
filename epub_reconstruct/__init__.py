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

__version__ = '0.2.0'