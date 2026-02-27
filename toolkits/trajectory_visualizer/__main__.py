"""CLI entry point: python -m toolkits.trajectory_visualizer"""

import argparse
import os
from pathlib import Path

from .app import build_ui, APP_CSS
from .data import discover_trajectory_files


def main():
    parser = argparse.ArgumentParser(description="Trajectory Profiler & Visualizer")
    parser.add_argument("--port", type=int, default=7860, help="Server port (default: 7860)")
    parser.add_argument("--share", action="store_true", help="Create a public Gradio link")
    parser.add_argument("--trajectory-dir", type=str, default=None,
                        help="Base directory for trajectory files (default: project root)")
    args = parser.parse_args()

    if args.trajectory_dir:
        traj_dir = os.path.abspath(args.trajectory_dir)
    else:
        traj_dir = str(Path(__file__).resolve().parent.parent.parent)

    print(f"Trajectory directory: {traj_dir}")
    files = discover_trajectory_files(traj_dir)
    print(f"Found {len(files)} trajectory file(s)")

    app = build_ui(traj_dir)
    app.launch(
        server_port=args.port,
        share=args.share,
        css=APP_CSS,
    )


if __name__ == "__main__":
    main()
