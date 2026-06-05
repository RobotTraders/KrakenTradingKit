import argparse

from .run_assistant import run_assistant


def main() -> None:
    parser = argparse.ArgumentParser(prog="ai_assistant")
    parser.add_argument("config", help="Configuration name under ai_assistant/configs")
    args = parser.parse_args()
    run_assistant(args.config)


if __name__ == "__main__":
    main()
