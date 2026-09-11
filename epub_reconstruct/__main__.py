# -*- coding: utf-8 -*-

import argparse
import sys

from .deconstruct import deconstruct
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

    args = parser.parse_args(argv)
    if args.command == 'deconstruct':
        deconstruct(args.epub, args.parts_dir)
    elif args.command == 'reconstruct':
        reconstruct(args.parts_dir, args.out_epub)
    elif args.command == 'verify':
        ok = verify(args.orig_epub, args.new_epub,
                    args.parts_dir or None)
        sys.exit(0 if ok else 1)
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())