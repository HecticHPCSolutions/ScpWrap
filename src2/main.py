import datetime
import logging
import os
import requests
import shutil
import sys
import tempfile
import tkinter

from logging.handlers import TimedRotatingFileHandler
from rclone import Rclone
from ssh import SSH
from tkinter import ttk
from tkinter import filedialog
from typing import Literal
from pathlib import Path

VERSION="v2.0"

def print_log(message: str, level: Literal["debug", "info", "warning", "error", "critical"] = "info") -> None:
    print(message)

    # Get the method from the attributes and call
    getattr(logging, level)(message)


def create_log() -> None:
    # Create a log at the exe level rather than the pycrucible level
    logging_file = str(Path(__file__).resolve().parent.parent / "scpwrap.log")
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[TimedRotatingFileHandler(
            logging_file, when="W0", interval=1, backupCount=5)]
    )


def check_version() -> None:
    # Check version
    resp = requests.get(
        'https://api.github.com/repos/HecticHPCSolutions/ScpWrap/tags')
    cloud_version = resp.json()[0]["name"]

    if cloud_version != VERSION:
        print_log(f"Please update to latest SCPWrap version: {cloud_version}")
        tkinter.messagebox.showwarning(
            "Version mismatch!", f"Local version detected: {VERSION}\nPlease update to latest SCPWrap version: {cloud_version}")


class Config:
    localbase: str
    remote_host: str
    remotebase: str
    transfer_mode: str
    archive_type: str


    def __init__(self, local_base: str, remote_host: str, remote_base: str, transfer_mode: str, archive_type: str):
        self.local_base = local_base
        self.remote_host = remote_host
        self.remote_base = remote_base
        self.transfer_mode = transfer_mode
        self.archive_type = archive_type


    def __str__(self) -> str:
        return f"Config(local_base={self.local_base}, remote_host={self.remote_host}, remote_base={self.remote_base}, transfer_mode={self.transfer_mode}, archive_mode={self.archive_type})"


def parse_config() -> Config:
    return Config(**{
        "local_base": str(Path.home()),
        "remote_base": 'instrument_data',
        "remote_host": os.environ['REMOTE_HOST'],
        "transfer_mode": os.environ.get("TRANSFER_MODE", "files").strip().lower(),
        "archive_type": os.environ.get("ARCHIVE_TYPE", "tar.gz").strip().lower()
    })


def prompt_directory_select(local_base: str) -> str:
    initialdir = str(Path(local_base).expanduser())
    root = tkinter.Tk()
    root.withdraw()  # Hide the main window

    # Prompt for a directory
    directory = filedialog.askdirectory(title="Select a directory", initialdir=initialdir)
    if directory == () or directory == "": # Detect if user cancels and doesn't pick a directory
        sys.exit(1)

    # Close UI objects
    root.destroy()

    print_log(f"User selected {directory}")
    return directory


def prompt_delete_agreement() -> bool:
    root = tkinter.Tk()
    root.title("Delete after transfer?")
    root.geometry("350x100")
    delete_agreement = tkinter.BooleanVar(value=True)

    checkbox = ttk.Checkbutton(root, text='I agree', variable=delete_agreement)
    checkbox.pack(pady=20)

    confirm = ttk.Button(root, text="Confirm", command=root.destroy)
    confirm.pack()

    root.mainloop()

    if delete_agreement.get():
        print_log("User agreed to delete the dataset after upload")
    else:
        print_log("User did not agree to delete the dataset after upload")

    return delete_agreement.get()


def main():
    create_log()

    print_log("============================================================================")
    print_log(f"ScpWrap {VERSION}")
    check_version()

    print_log("Creating local temporary working directory...")
    work_dir = Path(tempfile.mkdtemp())
    work_dir.mkdir(parents=True, exist_ok=True)

    print_log("Parsing config...")
    config = parse_config()
    print_log(config)

    print_log("Prompting user for directory details...")
    directory = prompt_directory_select(config.local_base)
    delete_agreement = prompt_delete_agreement()

    print_log("Creating SSH keys...")
    ssh = SSH(work_dir)

    start_time = datetime.datetime.now()

    print_log("Copying...")
    rclone = Rclone("vault", config.remote_host, ssh.user, str(ssh.key_path), str(work_dir / "rclone.conf"), str(ssh.cert_path))
    rclone.copy(directory, config.remote_base)

    print_log("Final destination files:")
    rclone.lsf(config.remote_base)

    if delete_agreement:
        print_log("Deleting local dataset...")
        rclone.local_delete(directory)
    else:
        print_log("Skipping deleting local dataset...")

    print_log("Cleaning up...")
    rclone.local_delete(work_dir)

    end_time = datetime.datetime.now()
    print_log(f"Process started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\nProcess finished: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")


def main_wrapper(main):
    try:
        main()
        input("Press ENTER to exit.")

    except Exception as e:
        print_log(e, "error")
        input("Press ENTER to exit.")


if __name__ == "__main__":
    main_wrapper(main)
