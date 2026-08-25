"""Manage Flask and Huey."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime


class RippleManager:
    """Launch Flask and Huey in separate terminals and manage their lifecycles."""

    def __init__(self, args):
        if args.command == "start":
            self.flask = {
                "flask_app": args.flask_app,
                "flask_debug": args.flask_debug,
                "flask_port": args.flask_port,
                "flask_host": args.flask_host,
                "hide_flask_shell": args.hide_flask_shell,
            }
            self.huey = {
                "thread_count": args.thread_count,
                "hide_huey_shell": args.hide_huey_shell,
            }
            # self.logs_dir = args.logs
        self.processes = []

    def print_config(self):
        """Pring configuration settings for the Ripple Manager."""
        print("Flask API Configuration:", self.flask)
        print("Huey Consumer Configuration:", self.huey)
        print("Logs Directory:", os.getcwd())

    def _handle_remove_readonly(self, func, path, exc_info):
        """Handle the read-only file deletion error."""
        import stat

        os.chmod(path, stat.S_IWRITE)
        func(path)

    def start(self):
        """Start the Ripple API and Huey consumer in separate terminals."""
        # TODO: Add check for running instance
        if sys.platform != "win32":
            raise SystemError("API can only be run from a windows system")

        huey_consumer_path = os.path.join(os.path.dirname(sys.executable), "huey_consumer.py")

        if not huey_consumer_path:
            print("Error: huey consumer script was not discoverable.")
            exit(1)

        python_executable = sys.executable

        print("Starting ripple1d-huey")
        huey_command = [
            python_executable,
            "-u",
            huey_consumer_path,
            "ripple1d.api.tasks.huey",
            "-w",
            str(self.huey["thread_count"]),
            "--flush-locks",
            "--no-periodic",
        ]
        if self.huey["hide_huey_shell"]:
            subprocess.Popen(["start", "cmd", "/k", " ".join(huey_command)], shell=False)

        else:
            subprocess.Popen(["start", "cmd", "/k", " ".join(huey_command)], shell=True)

        os.environ["FLASK_APP"] = self.flask["flask_app"]

        print("Starting ripple1d-flask")
        flask_command = [
            python_executable,
            "-m",
            "flask",
            "run",
            "--host",
            self.flask["flask_host"],
            "--port",
            str(self.flask["flask_port"]),
        ]

        if self.flask["flask_debug"]:
            print("Flask debug mode enabled")
            flask_command.append("--debug")

        if self.flask["hide_flask_shell"]:
            flask_result = subprocess.Popen(["start", "cmd", "/k", " ".join(flask_command)], shell=False)
        else:
            flask_result = subprocess.Popen(["start", "cmd", "/k", " ".join(flask_command)], shell=True)

        # Check if Flask started successfully
        try:
            flask_result.wait(timeout=5)
            if flask_result.returncode != 0:
                raise subprocess.CalledProcessError(flask_result.returncode, flask_command, stderr=flask_result.stderr)
            self.processes.append((flask_result, "flask"))
            print("Success!")
        except subprocess.TimeoutExpired:
            self.processes.append((flask_result, "flask"))
        except subprocess.CalledProcessError as e:
            error_message = e.stderr.read().decode() if e.stderr else "No error message captured."
            print(f"Error: Failed to start Flask. Error message: {error_message}")
            self.processes.append((None, "flask"))


def main():
    """Run Ripple in API mode."""
    parser = argparse.ArgumentParser(description="Ripple Manager Configuration")
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Define the 'start' subcommand
    start_parser = subparsers.add_parser("start", help="Start the Ripple Manager")
    start_parser.add_argument(
        "--hide_flask_shell", action="store_true", help="Launch terminal for Flask API (default: False)"
    )

    start_parser.add_argument("--flask_debug", action="store_true", help="Debug mode for Flask API (default: False)")
    start_parser.add_argument("--flask_port", type=int, default=80, help="Port for Flask API (default: 80)")
    start_parser.add_argument("--flask_host", type=str, default="0.0.0.0", help="Host for Flask API (default: 0.0.0.0)")
    start_parser.add_argument(
        "--flask_app", type=str, default="ripple1d.api.app", help="Flask App (default: ripple1d.api.app)"
    )
    start_parser.add_argument("--thread_count", type=int, default=1, help="Thread count for Huey Consumer (default: 1)")
    start_parser.add_argument(
        "--hide_huey_shell", action="store_true", help="Launch terminal for Huey Consumer (default: False)"
    )
    # start_parser.add_argument(
    #     "--logs",
    #     type=str,
    #     default=os.path.join(os.getcwd(), "logs"),
    #     help="Logs directory (default: current directory/logs)",
    # )

    args = parser.parse_args()

    if args.command == "start":
        manager = RippleManager(args)
        manager.print_config()
        manager.start()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
