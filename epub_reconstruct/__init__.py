# -*- coding: utf-8 -*-

# Deconstruct an FFF-generated epub into its constituent parts, then
# reconstruct it from those parts using FanFicFare's own EpubWriter.
#
# This is intentionally NOT part of the FanFicFare plugin -- it's a
# separate module that reuses FFF's writer machinery to prove a book
# can be re-created from itself (a step towards merging epubs of the
# same book that hold different chapter groups).

__version__ = '0.1.0'