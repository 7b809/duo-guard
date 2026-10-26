# update.py
import os
import subprocess
import sys
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

START_SCRIPT = "start.sh"
STOP_SCRIPT = "stop.sh"

LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
META_DATA_DIR = os.path.join(PROJECT_DIR, "meta_data")

TIMEZONE = "Asia/Kolkata"


def run_command(
    command: list[str],
    step_name: str,
    check: bool = True,
) -> None:
    print()
    print(f"> {' '.join(command)}")

    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_DIR,
        )

        if result.returncode != 0:
            error_message = f"{step_name} failed.\n" f"Exit code: {result.returncode}"

            print(f"\nERROR: {error_message}")

            if check:
                sys.exit(result.returncode)

            return

    except Exception as error:
        error_message = (
            f"{step_name} encountered an exception.\n"
            f"Error: {type(error).__name__}: {error}"
        )

        print(f"\nERROR: {error_message}")

        if check:
            sys.exit(1)


def backup_logs() -> None:
    print("\n==========================================")
    print("  Creating Logs Backup")
    print("==========================================")

    if not os.path.exists(LOGS_DIR):
        message = (
            f"Logs directory not found.\n"
            f"Path: {LOGS_DIR}\n"
            f"Skipping logs backup."
        )

        print(f"\nWARNING: {message}")
        return

    try:
        os.makedirs(META_DATA_DIR, exist_ok=True)

        current_datetime = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y%m%d_%H%M%S")

        archive_base_name = os.path.join(
            META_DATA_DIR,
            f"logs_{current_datetime}",
        )

        archive_path = shutil.make_archive(
            base_name=archive_base_name,
            format="zip",
            root_dir=PROJECT_DIR,
            base_dir="logs",
        )

        print("\nLogs backup created successfully:")
        print(archive_path)

        shutil.rmtree(LOGS_DIR)

    except Exception as error:
        error_message = (
            "Failed to create logs backup.\n" f"Error: {type(error).__name__}: {error}"
        )

        print(f"\nERROR: {error_message}")

        sys.exit(1)


def main() -> None:
    print("==========================================")
    print("  Market Data Foundation")
    print("  Project Update")
    print("==========================================")

    print("\nProject directory:")
    print(PROJECT_DIR)

    try:
        # 1. Backup logs
        backup_logs()

        # 2. Stop application
        stop_script = os.path.join(
            PROJECT_DIR,
            STOP_SCRIPT,
        )

        if os.path.exists(stop_script):
            print("\nStopping application...")

            run_command(
                ["bash", STOP_SCRIPT],
                step_name="Application Stop",
                check=False,
            )

        else:
            message = f"{STOP_SCRIPT} not found. " "Skipping application stop."

            print(f"\nWARNING: {message}")

        # 3. Fetch latest Git information
        print("\nFetching latest Git information...")

        run_command(
            ["git", "fetch", "--all"],
            step_name="Git Fetch",
        )

        # 4. Reset tracked changes
        print("\nResetting all local changes...")

        run_command(
            ["git", "reset", "--hard", "HEAD"],
            step_name="Git Reset",
        )

        # 5. Remove untracked files and directories
        # Exclude meta_data so that log backups are preserved
        print("\nRemoving untracked files and directories " "except meta_data...")

        run_command(
            [
                "git",
                "clean",
                "-fd",
                "-e",
                "meta_data/",
            ],
            step_name="Git Clean",
        )

        # 6. Pull latest code
        print("\nPulling latest code...")

        run_command(
            ["git", "pull"],
            step_name="Git Pull",
        )

        # 7. Set script permissions
        print("\nSetting script permissions...")

        start_script = os.path.join(
            PROJECT_DIR,
            START_SCRIPT,
        )

        stop_script = os.path.join(
            PROJECT_DIR,
            STOP_SCRIPT,
        )

        if os.path.exists(start_script):
            run_command(
                ["chmod", "+x", START_SCRIPT],
                step_name="Start Script Permission Update",
            )

        else:
            message = f"{START_SCRIPT} not found."

            print(f"WARNING: {message}")

        if os.path.exists(stop_script):
            run_command(
                ["chmod", "+x", STOP_SCRIPT],
                step_name="Stop Script Permission Update",
            )

        else:
            message = f"{STOP_SCRIPT} not found."

            print(f"WARNING: {message}")

        # 8. Show final Git status
        print("\nFinal Git status:")

        run_command(
            ["git", "status"],
            step_name="Final Git Status",
        )

        # 9. Start application
        if os.path.exists(start_script):
            print("\nStarting application...")

            run_command(
                ["bash", START_SCRIPT],
                step_name="Application Start",
            )

        else:
            message = f"{START_SCRIPT} not found. " "Application was not started."

            print(f"\nWARNING: {message}")

        print()
        print("==========================================")
        print("  Project update completed")
        print("==========================================")

    except KeyboardInterrupt:
        message = "Project update interrupted by user."

        print(f"\nWARNING: {message}")

        sys.exit(130)

    except Exception as error:
        error_message = (
            "Unexpected exception during project update.\n"
            f"Error: {type(error).__name__}: {error}"
        )

        print(f"\nERROR: {error_message}")

        sys.exit(1)


if __name__ == "__main__":
    main()