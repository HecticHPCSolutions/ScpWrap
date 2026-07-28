import datetime
import hashlib
import json
import logging
import os
import posixpath
import requests
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter
import webbrowser
from typing import Callable, Literal, Optional

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox
from typing import Tuple
from urllib.parse import urlencode

from sftp_client import connect_sftp
from stream_archive import create_archive, CancelledError
from verify_archive import compare_stats, dir_stats, remote_dir_stats, tar_stats

VERSION = "v1.7"

class Config:
    localbase: str
    remote_host: str
    remotebase: str
    sshauthz: str
    def __init__(self, localbase: str, remote_host: str, remotebase: str, sshauthz: str):
        self.localbase = localbase
        self.remote_host = remote_host
        self.remotebase = remotebase
        self.sshauthz = sshauthz

    def __str__(self):
        return f"Config(localbase={self.localbase}, remote_host={self.remote_host}, remotebase={self.remotebase}, sshauthz={self.sshauthz})"


def _downloads_directory() -> str:
    """Return the user's Downloads directory in a cross-platform way."""
    home = Path.home()
    downloads = home / "Downloads"
    if downloads.exists():
        return str(downloads)

    if os.name == "nt":
        try:
            import ctypes
            csidl_downloads = 0x374  # CSIDL_DOWNLOADS
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.shell32.SHGetFolderPathW(None, csidl_downloads, None, 0, buf)
            return buf.value or str(downloads)
        except Exception:
            return str(downloads)

    # Fallback for Linux: try xdg-user-dir
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DOWNLOAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        path = result.stdout.strip()
        if path:
            return path
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    return str(downloads)


def mk_ssh_config(workdir: Path, ssh_config: str, config: Config) -> Tuple[str,str,str]:
    # Use ssh-keygen to generate a new key in workdir
    keyname = "id_ed25519"
    keytype = "ed25519"

    sshauthz_host = config.sshauthz.split("?")[0]
    ca = config.sshauthz.split("?")[1].split("=")[1]
    ssh_key_path = str(Path(workdir) / keyname)
    downloads_dir = _downloads_directory()

    # Remove the key if it already exists
    key_path = Path(ssh_key_path)
    if key_path.exists():
        key_path.unlink()
    if key_path.with_suffix(".pub").exists():
        key_path.with_suffix(".pub").unlink()
    cert_path = Path(f"{ssh_key_path}-cert.pub")
    if cert_path.exists():
        cert_path.unlink()
    cert_download_path = os.path.join(downloads_dir, f"{keyname}-cert.pub")
    if os.path.exists(cert_download_path):
        os.remove(cert_download_path)

    subprocess.run(["ssh-keygen", "-t", keytype, "-f", ssh_key_path, "-N", ""])

    # read the public key
    qs = {}
    with open(f"{ssh_key_path}.pub", "r") as f:
        pubkey = f.read().strip()
    qs['saveas'] = f"{keyname}-cert.pub"
    qs['pubkey'] = pubkey
    qs['ca'] = ca
    qs['dologout'] = "true"
    qs['dohackylogout'] = "true"
    uqs = urlencode(qs, safe='/:?&=')  # Use safe to allow special characters in the URL
    url = sshauthz_host + "?" + uqs

    # open the url in a browser
    private_browser(url)

    # Wait for the downloaded cert to arrive
    cert_download_path = os.path.join(downloads_dir, f"{keyname}-cert.pub")
    while not os.path.exists(cert_download_path):
        print('waiting for the cert to arrive')
        time.sleep(1)

    # copy the file to workdir
    print_log("cert found, begin uploading")
    shutil.move(cert_download_path, str(Path(workdir) / f"{keyname}-cert.pub"))

    # use ssh-keygen to query the certificate for the valid principals and add them to the ssh config
    p = subprocess.run(
        ["ssh-keygen", "-L", "-f", str(Path(workdir) / f"{keyname}-cert.pub")],
        check=True,
        capture_output=True,
        text=True,
    )

    # parse the output
    lines = p.stdout.splitlines()
    principals: list[str] = []
    inblock = False
    for line in lines:
        if 'Principals' in line:
            inblock = True
            continue
        if 'Critical Options' in line:
            inblock = False
            continue
        if inblock:
            principals.append(line.strip())

    # create the ssh config file
    cert_file = Path(workdir) / f"{keyname}-cert.pub"
    with open(ssh_config, "w") as f:
        f.write(f"Host {config.remote_host}\n")
        f.write(f"    HostName {config.remote_host}\n")
        f.write(f"    User {principals[0]}\n")
        f.write(f"    IdentityFile {ssh_key_path}\n")
        f.write(f"    CertificateFile {cert_file}\n")
        f.write(f"    IdentitiesOnly yes\n")
        f.write(f"    StrictHostKeyChecking no\n")
        f.write(f"    ControlMaster auto\n")
        f.write(f"    ControlPersist 10m\n")
        f.write(f"    LogLevel ERROR\n")

    return (config.remote_host, principals[0], ssh_key_path)


def private_browser(url):
    if os.name == 'nt':  # Windows
        try:
            subprocess.run([
                "start",
                "msedge.exe",
                url.replace("&", "^&"),
                "-inprivate"
            ], shell=True, check=True)
            return
        except subprocess.CalledProcessError:
            print_log("MSEdge not found")


        # Default chrome installation path
        chrome_path = 'C:/Program Files/Google/Chrome/Application/chrome.exe %s --incognito'
        web_bool = webbrowser.get(chrome_path).open_new(url)
        if web_bool:
            return
        else:
            print_log("Chrome not found, searching x86")

        chrome_path_x86 = 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe %s --incognito'
        web_bool = webbrowser.get(chrome_path_x86).open_new(url)
        if web_bool:
            return
        else:
            print_log("Chrome not found")

        print_log("Supported web browsers not found, please install either MSEdge or Chrome", "error")
        input("Press ENTER to exit.")
        sys.exit(1)
    elif os.name == 'posix':
        try:
            subprocess.run(['xdg-open',url])
            return
        except subprocess.CalledProcessError:
            print_log('xdg open failed')
    else:
        print_log("ScpWrap currently only supports windows", "error")
        input("Press ENTER to exit.")
        sys.exit(1)

# def get_config() -> Config:
#     root = tkinter.Tk()
#     root.withdraw()  # Hide the main window

#     # Prompt for a directory
#     configfile = filedialog.askopenfilename(
#         title="Select a config", initialdir=os.path.dirname(os.path.realpath(__file__)))
#     if configfile == () or configfile == "": # Detect if user cancels and doesn't pick a config
#         sys.exit(1)

#     # Close UI objects
#     root.destroy()

#     with open(configfile,'r') as f:
#         configdata = yaml.safe_load(f)

#     return Config(**configdata)

def setup(localbase: str, workdir: str, config: Config) -> Tuple[str, str, Tuple[str,str,str], bool]:
    initialdir = str(Path(localbase).expanduser())
    root = tkinter.Tk()
    root.withdraw()  # Hide the main window

    # Prompt for a directory
    directory = filedialog.askdirectory(title="Select a directory", initialdir=initialdir)
    if directory == () or directory == "": # Detect if user cancels and doesn't pick a directory
        sys.exit(1)

    # Close UI objects
    root.destroy()

    # Prompt for delete
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

    # Make SSH Config
    workdir_path = Path(workdir).expanduser()
    ssh_config = str(workdir_path / "ssh.cfg")
    ssh_details = mk_ssh_config(workdir_path, ssh_config, config)

    return (directory, ssh_config, ssh_details, delete_agreement.get())


def _dir_metrics_single_pass(path: Path) -> Tuple[int, int]:
    """
    Return total size in bytes and file count using a single filesystem walk.
    """
    total_size = 0
    total_files = 0
    stack: list[Path] = [Path(path)]

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total_files += 1
                            total_size += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        # Skip entries we cannot stat/read and continue scanning.
                        continue
        except OSError:
            # Skip directories we cannot traverse and continue scanning.
            continue

    return total_size, total_files


def get_size(path: Path) -> Tuple[int, int]:
    """
    Get directory metrics.

    :param path: Path to the directory.
    :return: (total size in bytes, file count)
    """
    return _dir_metrics_single_pass(Path(path))



def _archive_suffix(arctype: str) -> str:
    if arctype == "tar.gz":
        return "tar.gz"
    if arctype == "tar.bz2":
        return "tar.bz2"
    if arctype == "tar.xz":
        return "tar.xz"
    raise ValueError(f"Unsupported archive type: {arctype}")


def _archive_read_mode(arctype: str) -> Literal['r', 'r:gz', 'r:bz2', 'r:xz']:
    if arctype == "tar.gz":
        return "r:gz"
    if arctype == "tar.bz2":
        return "r:bz2"
    if arctype == "tar.xz":
        return "r:xz"
    raise ValueError(f"Unsupported archive type: {arctype}")


def _write_stats_artifacts(output_dir: Path, base_name: str, local_stats: dict, remote_stats: dict, summary):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    source_stats_file = output_path / f"{base_name}.source.stats.json"
    remote_stats_file = output_path / f"{base_name}.remote.stats.json"
    summary_file = output_path / f"{base_name}.summary.json"

    with open(source_stats_file, "w", encoding="utf-8") as f:
        json.dump({path: asdict(file_stats) for path, file_stats in local_stats.items()}, f, indent=2, sort_keys=True)
    with open(remote_stats_file, "w", encoding="utf-8") as f:
        json.dump({path: asdict(file_stats) for path, file_stats in remote_stats.items()}, f, indent=2, sort_keys=True)
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(asdict(summary), f, indent=2, sort_keys=True)

    print_log(f"Wrote source file stats with checksums to {source_stats_file}")
    print_log(f"Wrote remote file stats with checksums to {remote_stats_file}")
    print_log(f"Wrote archive verification summary to {summary_file}")


def _archive(
    srcdir: Path,
    remote_dir: str,
    remote_host: str,
    sshconfig: str,
    archive_type: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[threading.Event] = None,
):
    suffix = _archive_suffix(archive_type)
    remote_archive = posixpath.join(
        remote_dir.replace(os.sep, '/').replace('\\', '/'),
        f"{srcdir.name}.{suffix}",
    )

    create_archive(
        config=sshconfig,
        host=remote_host,
        archive=remote_archive,
        source=str(srcdir),
        arctype=archive_type,
        progress_callback=progress_callback,
        cancelled=cancelled,
    )
    return remote_archive


def _archive_stats_from_remote(
    remote_archive: str,
    remote_host: str,
    sshconfig: str,
    archive_type: str,
    progress_callback: Optional[Callable[[int], None]] = None,
):
    hasher = hashlib.md5
    sftp = connect_sftp(sshconfig, remote_host)
    try:
        with sftp.file(remote_archive, "r") as remote_file:
            archive_stats = tar_stats(
                remote_file,
                hasher=hasher,
                mode=_archive_read_mode(archive_type),
                progress_callback=progress_callback,
            )
    finally:
        sftp.close()
    return archive_stats


def _remote_dir_with_stats(
    srcdir: Path,
    remote_dir: str,
    remote_host: str,
    sshconfig: str,
    progress_callback: Optional[Callable[[int], None]] = None,
):
    remote_uploaded_dir = posixpath.join(
        remote_dir.replace(os.sep, '/').replace('\\', '/'),
        srcdir.name,
    )
    hasher = hashlib.md5

    sftp = connect_sftp(sshconfig, remote_host)
    try:
        stats = remote_dir_stats(
            sftp,
            remote_uploaded_dir,
            hasher=hasher,
            progress_callback=progress_callback,
        )
    finally:
        sftp.close()

    return remote_uploaded_dir, stats


def copy(dir: str, config: Config, sshconfig: str, transfer_mode: str = "files", archive_type: str = "tar.gz"):
    # Get relative path from srcdir
    srcdir = Path(dir).expanduser()
    if not srcdir.is_dir():
        raise ValueError(f"Directory {srcdir} does not exist.")
    # rel_path = os.path.relpath(dir, config.localbase)
    # print("Relative path:", rel_path)

    # Get remote path from config
    remote_dir = config.remotebase
    #remote_dir = Path(os.path.join(config.remotebase, rel_path))

    #dest = ScpDestType(config.remote_host, remote_dir)
    #output_manifest = Path(os.path.join(os.path.expanduser(workdir),"manifest.yaml"))
    #hash_algorithm = "sha256"

    def _fmt_bytes(num_bytes: float) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(max(num_bytes, 0))
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.2f} {unit}"
            value /= 1024
        return f"{value:.2f} TB"

    def _fmt_duration(seconds: Optional[float]) -> str:
        if seconds is None:
            return "N/A"
        total_seconds = max(int(seconds), 0)
        mins, secs = divmod(total_seconds, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            return f"{hours} hr {mins} min {secs} sec"
        return f"{mins} min {secs} sec"

    root = tkinter.Tk()
    root.withdraw()
    # Fast-start: compute directory totals in the background while transfer begins immediately.
    metrics_executor = ThreadPoolExecutor(max_workers=1)
    metrics_future = metrics_executor.submit(get_size, Path(dir))

    start_time = datetime.datetime.now()
    start_monotonic = time.monotonic()

    total_size: Optional[int] = None
    total_files: Optional[int] = None
    metrics_ready = False

    progress_label_text = tkinter.StringVar()
    result = tkinter.Toplevel(root)
    result.title("Transfer Progress")
    label = tkinter.Label(result, textvariable=progress_label_text, justify="left", anchor="w", width=90)
    label.pack(padx=20, pady=20)

    # Cancellation support
    cancelled = threading.Event()

    def on_cancel():
        if messagebox.askyesno("Cancel Transfer", "Are you sure you want to cancel this transfer?\n\nThis will stop the transfer immediately and any partially transferred files may be left on the remote server."):
            cancelled.set()
            result.destroy()
            print_log("Transfer cancelled by user.", "warning")
            # Clean up metrics executor
            metrics_executor.shutdown(wait=False)

    cancel_button = ttk.Button(result, text="Cancel", command=on_cancel)
    cancel_button.pack(pady=10)

    last_sample_time = start_monotonic
    last_sample_bytes = 0

    def _hydrate_totals() -> None:
        nonlocal total_size, total_files, metrics_ready
        if metrics_ready:
            return
        if not metrics_future.done():
            return
        try:
            total_size, total_files = metrics_future.result()
            metrics_ready = True
        except Exception as exc:
            print_log(f"Failed to calculate directory metrics in background: {exc}", "error")
            total_size, total_files = None, None
            metrics_ready = True

    def _build_progress_text(
        bytes_written: int,
        files_transferred: int,
        avg_speed_bps: Optional[float],
        inst_speed_bps: Optional[float],
        elapsed_seconds: float,
        eta_seconds: Optional[float],
        completion_dt: Optional[datetime.datetime],
    ) -> str:
        if total_size and total_size > 0:
            pct = min((bytes_written / total_size) * 100, 100.0)
            size_line = f"Transferred: {_fmt_bytes(bytes_written)} / {_fmt_bytes(total_size)} ({pct:.1f}%)"
        else:
            size_line = f"Transferred: {_fmt_bytes(bytes_written)}"

        total_size_text = _fmt_bytes(total_size) if total_size is not None else "Calculating..."
        total_files_text = str(total_files) if total_files is not None else "Calculating..."
        completion_text = completion_dt.strftime("%Y-%m-%d %H:%M:%S") if completion_dt else "N/A"

        return (
            f"Path: {dir}\n"
            f"Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Total size: {total_size_text}\n"
            f"Total files: {total_files_text}\n"
            f"\n"
            f"{size_line}\n"
            f"Files transferred: {files_transferred}\n"
            f"Current speed: {_fmt_bytes(avg_speed_bps) + '/s' if avg_speed_bps else 'N/A'}"
            f" (instant: {_fmt_bytes(inst_speed_bps) + '/s' if inst_speed_bps else 'N/A'})\n"
            f"Elapsed duration: {_fmt_duration(elapsed_seconds)}\n"
            f"Projected completion: {completion_text}\n"
            f"Remaining duration: {_fmt_duration(eta_seconds)}"
        )

    # Track if transfer was cancelled for proper return handling
    transfer_cancelled = False
    local_stats = None
    remote_stats = None

    initial_msg = _build_progress_text(
        bytes_written=0,
        files_transferred=0,
        avg_speed_bps=None,
        inst_speed_bps=None,
        elapsed_seconds=0,
        eta_seconds=None,
        completion_dt=None,
    )
    progress_label_text.set(initial_msg)
    print_log(initial_msg)
    result.update_idletasks()
    locked_width = result.winfo_width()
    locked_height = result.winfo_height()
    result.geometry(f"{locked_width}x{locked_height}")
    result.minsize(locked_width, locked_height)
    result.maxsize(locked_width, locked_height)
    result.resizable(False, False)
    result.update()

    def transfer_progress_callback(total_bytes_written: int, files_transferred: int) -> None:
        nonlocal last_sample_time, last_sample_bytes
        _hydrate_totals()
        now = time.monotonic()
        elapsed_seconds = max(now - start_monotonic, 0.000001)

        delta_time = max(now - last_sample_time, 0.000001)
        delta_bytes = max(total_bytes_written - last_sample_bytes, 0)
        inst_speed_bps = delta_bytes / delta_time if delta_bytes > 0 else None

        avg_speed_bps = total_bytes_written / elapsed_seconds if total_bytes_written > 0 else None
        if total_size and avg_speed_bps and avg_speed_bps > 0:
            eta_seconds = max((total_size - total_bytes_written) / avg_speed_bps, 0)
            completion_dt = datetime.datetime.now() + datetime.timedelta(seconds=eta_seconds)
        else:
            eta_seconds = None
            completion_dt = None

        progress_label_text.set(
            _build_progress_text(
                bytes_written=total_bytes_written,
                files_transferred=files_transferred,
                avg_speed_bps=avg_speed_bps,
                inst_speed_bps=inst_speed_bps,
                elapsed_seconds=elapsed_seconds,
                eta_seconds=eta_seconds,
                completion_dt=completion_dt,
            )
        )
        result.update_idletasks()
        result.update()

        last_sample_time = now
        last_sample_bytes = total_bytes_written

    def set_verifying_status() -> None:
        nonlocal verify_start_time
        if verify_start_time is None:
            verify_start_time = datetime.datetime.now()
        _hydrate_totals()
        progress_label_text.set(
            "Copy complete, verifying ...\n"
            f"Verification start time: {verify_start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        result.update_idletasks()
        result.update()

    verify_start_time: Optional[datetime.datetime] = None
    verification_progress_lock = threading.Lock()
    local_files_verified = 0
    remote_files_verified = 0

    def local_verify_progress_callback(processed_files: int) -> None:
        nonlocal local_files_verified
        with verification_progress_lock:
            local_files_verified = processed_files

    def remote_verify_progress_callback(processed_files: int) -> None:
        nonlocal remote_files_verified
        with verification_progress_lock:
            remote_files_verified = processed_files

    def _update_verification_progress_until_done(local_future, remote_future) -> None:
        while not (local_future.done() and remote_future.done()):
            with verification_progress_lock:
                local_count = local_files_verified
                remote_count = remote_files_verified

            _hydrate_totals()
            total_files_text = str(total_files) if total_files is not None else "Calculating..."
            if verify_start_time is None:
                set_verifying_status()

            progress_label_text.set(
                "Copy complete, verifying ...\n"
                f"Verification start time: {verify_start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Expected files: {total_files_text}\n"
                f"Local files processed: {local_count}\n"
                f"Remote files processed: {remote_count}"
            )
            result.update_idletasks()
            result.update()
            time.sleep(0.1)

    #scp_with_manifest(
    #    input_directory=srcdir,
    #    destination=dest,
    #    ssh_config=sshconfig,
    #    output_manifest=output_manifest,
    #    hash_algorithm=hash_algorithm,
    #)
    # Close the progress dialog
    try:
        if transfer_mode == "files":
            use_sftp(
                srcdir,
                Path(remote_dir),
                config.remote_host,
                sshconfig,
                progress_callback=transfer_progress_callback,
                cancelled=cancelled,
            )
            set_verifying_status()
            with ThreadPoolExecutor(max_workers=2) as executor:
                local_stats_future = executor.submit(
                    dir_stats,
                    str(srcdir),
                    hashlib.md5,
                    local_verify_progress_callback,
                )
                remote_stats_future = executor.submit(
                    _remote_dir_with_stats,
                    srcdir,
                    remote_dir,
                    config.remote_host,
                    sshconfig,
                    remote_verify_progress_callback,
                )
                _update_verification_progress_until_done(local_stats_future, remote_stats_future)
                local_stats = local_stats_future.result()
                remote_uploaded_dir, remote_stats = remote_stats_future.result()
            print_log(f"Directory uploaded at {remote_uploaded_dir}")
        elif transfer_mode == "archive":
            archive_path = _archive(
                srcdir=srcdir,
                remote_dir=remote_dir,
                remote_host=config.remote_host,
                sshconfig=sshconfig,
                archive_type=archive_type,
                progress_callback=transfer_progress_callback,
                cancelled=cancelled,
            )
            set_verifying_status()
            with ThreadPoolExecutor(max_workers=2) as executor:
                local_stats_future = executor.submit(
                    dir_stats,
                    str(srcdir),
                    hashlib.md5,
                    local_verify_progress_callback,
                )
                remote_stats_future = executor.submit(
                    _archive_stats_from_remote,
                    archive_path,
                    config.remote_host,
                    sshconfig,
                    archive_type,
                    remote_verify_progress_callback,
                )
                _update_verification_progress_until_done(local_stats_future, remote_stats_future)
                local_stats = local_stats_future.result()
                remote_stats = remote_stats_future.result()
            print_log(f"Archive created at {archive_path}")
        else:
            raise ValueError("transfer_mode must be either 'files' or 'archive'")
    except CancelledError:
        transfer_cancelled = True
        print_log("Transfer was cancelled.", "warning")

    metrics_executor.shutdown(wait=False)
    if transfer_cancelled:
        return None
    result.destroy()
    root.destroy()
    return start_time, local_stats, remote_stats

def _ensure_remote_dir(sftp, remote_path: str):
    normalized = posixpath.normpath(remote_path)
    if normalized in ("", "."):
        return

    parts = normalized.strip("/").split("/")
    if normalized.startswith("/"):
        current = "/"
    else:
        current = ""

    for part in parts:
        if not part:
            continue
        if current in ("", "/"):
            next_path = f"/{part}" if current == "/" else part
        else:
            next_path = posixpath.join(current, part)
        try:
            sftp.mkdir(next_path)
        except OSError:
            # Directory may already exist.
            pass
        current = next_path


def use_sftp(
    srcdir: Path,
    remote_dir: Path,
    remote_host: str,
    sshconfig: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[threading.Event] = None,
):
    remote_root = posixpath.join(
        str(remote_dir).replace(os.sep, '/').replace('\\', '/'),
        srcdir.name,
    )
    total_bytes_written = 0
    total_files_transferred = 0
    chunk_size = 1024 * 1024

    sftp = connect_sftp(sshconfig, remote_host)
    try:
        _ensure_remote_dir(sftp, remote_root)

        for root, dirs, files in os.walk(srcdir):
            rel_root = os.path.relpath(root, srcdir)
            if rel_root == ".":
                remote_current = remote_root
            else:
                remote_current = posixpath.join(
                    remote_root, rel_root.replace(os.sep, '/').replace('\\', '/')
                )

            _ensure_remote_dir(sftp, remote_current)

            for d in dirs:
                remote_subdir = posixpath.join(remote_current, d)
                _ensure_remote_dir(sftp, remote_subdir)

            for filename in files:
                if cancelled is not None and cancelled.is_set():
                    raise CancelledError("Transfer cancelled by user")
                local_file = Path(root) / filename
                remote_file = posixpath.join(remote_current, filename)

                with local_file.open("rb") as src_f:
                    with sftp.file(remote_file, "wb") as dst_f:
                        while True:
                            chunk = src_f.read(chunk_size)
                            if not chunk:
                                break
                            dst_f.write(chunk)
                            total_bytes_written += len(chunk)
                            if progress_callback is not None:
                                progress_callback(total_bytes_written, total_files_transferred)

                total_files_transferred += 1
                if progress_callback is not None:
                    progress_callback(total_bytes_written, total_files_transferred)
    finally:
        sftp.close()

def verify(remote_stats: dict, local_stats: dict, dir: str, delete_agreement_bool: bool, artifact_label: str):
    print_log("begin verifying")
    summary = compare_stats(remote_stats, local_stats)

    artifact_dir = Path(__file__).resolve().parent.parent / "archive_stats"
    _write_stats_artifacts(artifact_dir, artifact_label, local_stats, remote_stats, summary)

    has_differences = (
        bool(summary.files_in_archive_only)
        or bool(summary.files_in_source_only)
        or bool(summary.files_with_size_diff)
        or bool(summary.files_with_mtime_diff)
        or bool(summary.files_with_checksum_diff)
    )

    if has_differences:
        print_log("Verification detected differences between local source and remote transfer content", "error")
        return False

    print_log("Verification passed: local source and remote transfer content match")

    if delete_agreement_bool:
        for root, dirs, files in os.walk(dir, topdown=False):
            for f in files:
                path = Path(root) / f
                path.unlink()
                print_log(f"deleted {path}")

            for d in dirs:
                path = Path(root) / d
                try:
                    path.rmdir()
                    print_log(f"Deleted {path}")
                except OSError:
                    print_log(f"{path} is not empty", "error")

    return True

def cleanup(workdir: str):
    # Remove the temporary directory
    workdir_path = Path(workdir)
    if workdir_path.exists():
        shutil.rmtree(workdir_path)
        print_log(f"Removed temporary directory: {workdir}")
    else:
        print_log(f"Directory {workdir} does not exist.", "error")
    return

def print_log(message, level="info"):
    print(message)

    # Get the method from the attributes and call
    getattr(logging, level)(message)

def check_version():
    # Check version
    resp = requests.get(
        'https://api.github.com/repos/HecticHPCSolutions/ScpWrap/tags')
    cloud_version = resp.json()[0]["name"]

    if cloud_version != VERSION:
        print_log(f"Please update to latest SCPWrap version: {cloud_version}")
        tkinter.messagebox.showwarning("Version mismatch!", f"Local version detected: {VERSION}\nPlease update to latest SCPWrap version: {cloud_version}")

def main():
    # try:
        # Create a log at the exe level rather than the pycrucible level
        logging_file = str(Path(__file__).resolve().parent.parent / "scpwrap.log")
        logging.basicConfig(
            format="%(asctime)s - %(levelname)s - %(message)s",
            level=logging.INFO,
            handlers=[TimedRotatingFileHandler(logging_file, when="W0", interval=1, backupCount=5)]
            )

        print_log("============================================================================")
        print_log(f"ScpWrap {VERSION}")

        # Check version
        check_version()

        # Make workdir
        workdir = tempfile.mkdtemp()
        Path(workdir).mkdir(parents=True, exist_ok=True)

        # Load config
        # try:
        #     configfile="config.yml"
        #     with open(configfile,'r') as f:
        #         configdata = yaml.safe_load(f)

        # except FileNotFoundError:
        #     configdata = {
        #         'localbase': os.path.expanduser('~'),
        #         'remote_host': 'm3-dtn.massive.org.au',
        #         'remotebase': os.path.expanduser('~/instrumentdata'),
        #         'sshauthz': 'https://sshauthz.m3-desktop.erc.monash.edu/?ca=m3'
        #     }

        # config = Config(**configdata)
        # config = get_config()
        config = Config(**{
            'localbase': str(Path.home()),
            'remotebase': 'instrument_data',
            'remote_host': os.environ['REMOTE_HOST'],
            'sshauthz': os.environ['SSHAUTHZ']
        })

        # Setup ssh config
        (dir, ssh_config, _, delete_agreement_bool) = setup(config.localbase, workdir, config)

        transfer_mode = os.environ.get("TRANSFER_MODE", "files").strip().lower()
        archive_type = os.environ.get("ARCHIVE_TYPE", "tar.gz").strip().lower()

        # Copy files or create remote archive depending on transfer mode
        copy_result = copy(
            dir,
            config=config,
            sshconfig=ssh_config,
            transfer_mode=transfer_mode,
            archive_type=archive_type,
        )

        if copy_result is None:
            # Transfer was cancelled
            cleanup(workdir)
            print_log("Transfer cancelled. Exiting.", "warning")
            input("Press ENTER to exit.")
            return

        assert copy_result is not None
        start_time, local_stats, remote_stats = copy_result
        verify(remote_stats, local_stats, dir, delete_agreement_bool, Path(dir).name)

        cleanup(workdir)

        end_time = datetime.datetime.now()
        print_log(f"Process started: {end_time.strftime('%Y-%m-%d %H:%M:%S')}\nProcess finished: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")

        input("Press ENTER to exit.")

    # except Exception as e:
    #     print_log(e, "error")
    #     input("Press ENTER to exit.")



if __name__ == "__main__":
    main()
