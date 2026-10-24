"""Shared command-line options for artist playlist eligibility."""
import argparse


def nonnegative_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    if number < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return number


def positive_int(value):
    number = nonnegative_int(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def add_threshold_arguments(parser):
    parser.add_argument("--min-songs", type=positive_int, default=3,
                        help="Minimum distinct songs credited to an artist (default: 3)")
    parser.add_argument("--min-solo-songs", type=nonnegative_int, default=0,
                        help="Minimum distinct songs with only that artist credited (default: 0, unrestricted)")
