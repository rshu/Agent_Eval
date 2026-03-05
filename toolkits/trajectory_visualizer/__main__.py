"""CLI entry point: python -m toolkits.trajectory_visualizer"""

import argparse

from .app import build_ui, APP_CSS


def main():
    parser = argparse.ArgumentParser(description="Trajectory Insight Finder")
    parser.add_argument("--port", type=int, default=7860, help="Server port (default: 7860)")
    parser.add_argument("--share", action="store_true", help="Create a public Gradio link")
    args = parser.parse_args()

    app = build_ui()
    app.launch(
        server_port=args.port,
        share=args.share,
        css=APP_CSS,
    )


if __name__ == "__main__":
    main()
