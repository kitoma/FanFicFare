# -*- coding: utf-8 -*-

import argparse
import os
import sys

from .deconstruct import deconstruct
from .merge import merge
from .reconstruct import reconstruct
from .verify import verify


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='epub_reconstruct',
        description='Deconstruct an FFF epub and reconstruct it via the '
                    'FanFicFare EpubWriter, then verify the round trip.')
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('deconstruct',
                       help='split an epub into parts + reconstruction.json')
    p.add_argument('epub')
    p.add_argument('parts_dir')

    p = sub.add_parser('reconstruct',
                       help='rebuild an epub from parts (via EpubWriter)')
    p.add_argument('parts_dir')
    p.add_argument('out_epub')

    p = sub.add_parser('verify',
                       help='compare original vs reconstructed epub')
    p.add_argument('orig_epub')
    p.add_argument('new_epub')
    p.add_argument('--parts-dir', default='')

    p = sub.add_parser('merge',
                       help='merge epubs/parts dirs of the same book')
    p.add_argument('sources', nargs='+',
                   help='epubs or parts dirs (base/first = oldest)')
    p.add_argument('--out', default='',
                   help='output epub (runs reconstruct)')
    p.add_argument('--parts-out', default='',
                   help='write merged parts here instead of temp')
    p.add_argument('--no-reconstruct', action='store_true',
                   help='emit parts only, skip epub reconstruction')

    args = parser.parse_args(argv)
    if args.command == 'deconstruct':
        deconstruct(args.epub, args.parts_dir)
    elif args.command == 'reconstruct':
        reconstruct(args.parts_dir, args.out_epub)
    elif args.command == 'verify':
        ok = verify(args.orig_epub, args.new_epub,
                    args.parts_dir or None)
        sys.exit(0 if ok else 1)
    elif args.command == 'merge':
        parts_out = args.parts_out or None
        out_epub = args.out or None
        merge([os.path.abspath(s) for s in args.sources],
              parts_out=parts_out, out_epub=out_epub,
              no_reconstruct=args.no_reconstruct)
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())