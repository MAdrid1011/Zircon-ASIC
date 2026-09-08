"""Extracted-RC constraints, setup/hold, clock and route checks for unary units."""
import argparse
from check_bf16_physical import check

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory')
    check(parser.parse_args().directory,'unary-checks.json')
