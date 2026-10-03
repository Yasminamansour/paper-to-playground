"""Paper to Playground agent (Stage 0 stub)."""
import argparse
import sys


def main(argv=None):
    p = argparse.ArgumentParser(description="Turn a paper excerpt into an interactive playground page.")
    p.add_argument("--input", required=True, help="path to case.json")
    p.add_argument("--output", required=True, help="output directory")
    p.add_argument("--model", required=True, help="OpenRouter MODEL_ID")
    p.parse_args(argv)
    print("not implemented")
    return 2


if __name__ == "__main__":
    sys.exit(main())
