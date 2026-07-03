import datetime
import hashlib
import json
import logging
import os
import posixpath
import requests
import shutil
import speedtest
import subprocess
import sys
import tempfile
import time
import tkinter
import webbrowser
import yaml
from typing import Literal

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from tkinter import ttk
from tkinter import filedialog
from typing import Tuple
from urllib.parse import urlencode

from sftp_client import connect_sftp
from stream_archive import create_archive
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
    

def mk_ssh_config(workdir: Path, ssh_config: str, config: Config) -> Tuple[str,str,str]:
    # Use ssh-keygen to generate a new key in workdir
    keyname = "id_ed25519"
    keytype = "ed25519"

    sshauthz_host = config.sshauthz.split("?")[0]
    ca = config.sshauthz.split("?")[1].split("=")[1]
    ssh_key_path = os.path.join(workdir, keyname)
    
    # Remove the key if it already exists
    if os.path.exists(ssh_key_path):
        os.remove(ssh_key_path)
    if os.path.exists(ssh_key_path + ".pub"):
        os.remove(ssh_key_path + ".pub")
    if os.path.exists(ssh_key_path + "-cert.pub"):
        os.remove(ssh_key_path + "-cert.pub")
    if os.path.exists(os.path.expanduser(f'~/Downloads/{keyname}-cert.pub')):
        os.remove(os.path.expanduser(f'~/Downloads/{keyname}-cert.pub'))

    subprocess.run(["ssh-keygen", "-t",keytype, "-f", ssh_key_path, "-N", ""])

    # read the public key
    qs = {}
    with open(ssh_key_path + ".pub", "r") as f:
        pubkey =f.read().strip()
    qs['saveas'] = f"{keyname}-cert.pub"
    qs['pubkey'] = pubkey
    qs['ca'] = ca
    qs['dologout'] = "true"
    qs['dohackylogout'] = "true"
    uqs = urlencode(qs, safe='/:?&=')  # Use safe to allow special characters in the URL
    url = sshauthz_host + "?" + uqs

    # open the url in a browser
    private_browser(url)

    # Wait for the file ~/Downloads/{keyname}-cert.pub to be created
    while not os.path.exists(os.path.join(os.path.expanduser("~"),"Downloads",f"{keyname}-cert.pub")):
        print('waiting for the cert to arrive')
        time.sleep(1)

    # copy the file to workdir
    print_log("cert found, begin uploading")
    shutil.move(os.path.join(os.path.expanduser("~"),"Downloads",f"{keyname}-cert.pub"), os.path.join(workdir,f"{keyname}-cert.pub"))

    # use ssh-keygen to query the certificate for the valid principals and add them to the ssh config
    p = subprocess.run(["ssh-keygen", "-L", "-f", os.path.join(workdir,f"{keyname}-cert.pub")], check=True, capture_output=True, text=True)

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
    with open(ssh_config, "w") as f:    
        f.write(f"Host {config.remote_host}\n")
        f.write(f"    HostName {config.remote_host}\n")
        f.write(f"    User {principals[0]}\n")
        f.write(f"    IdentityFile {ssh_key_path}\n")
        f.write(f"    CertificateFile {os.path.join(workdir,f'{keyname}-cert.pub')}\n")
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
    initialdir = os.path.expanduser(localbase)
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
    ssh_config = os.path.join(os.path.expanduser(workdir), "ssh.cfg")
    ssh_details = mk_ssh_config(Path(os.path.expanduser(workdir)), ssh_config, config)
    
    return (directory, ssh_config, ssh_details, delete_agreement.get())


def get_dir_size_windows(path):
    cmd = [
        "powershell",
        "-Command",
        f"(Get-ChildItem '{path}' -Recurse | Measure-Object -Property Length -Sum).Sum"
    ]
    result = subprocess.check_output(cmd, universal_newlines=True)
    return int(result.strip()) if result.strip().isdigit() else 0

def get_dir_size_linux(path: Path):
    cmd: list[str] = [
        "du",
        "-sb",
        f"{path}"
    ]
    result = subprocess.check_output(cmd, universal_newlines=True)
    return int(result.split()[0]) if result.strip().isdigit() else 0

def get_size(path: Path) -> int:
    """
    Get the size of a directory in bytes.
    :param path: Path to the directory.
    :return: Size in bytes.
    """
    if os.name == 'nt':  # Windows
        return get_dir_size_windows(path)
    else:  # Linux or other OS
        return get_dir_size_linux(path)


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


def _archive_with_stats(srcdir: Path, remote_dir: str, remote_host: str, sshconfig: str, archive_type: str):
    suffix = _archive_suffix(archive_type)
    remote_archive = posixpath.join(remote_dir, f"{srcdir.name}.{suffix}")

    create_archive(
        config=sshconfig,
        host=remote_host,
        archive=remote_archive,
        source=str(srcdir),
        arctype=archive_type,
    )
    return remote_archive


def _archive_stats_from_remote(remote_archive: str, remote_host: str, sshconfig: str, archive_type: str):
    hasher = hashlib.md5
    sftp = connect_sftp(sshconfig, remote_host)
    try:
        with sftp.file(remote_archive, "r") as remote_file:
            archive_stats = tar_stats(remote_file, hasher=hasher, mode=_archive_read_mode(archive_type))
    finally:
        sftp.close()
    return archive_stats


def _remote_dir_with_stats(srcdir: Path, remote_dir: str, remote_host: str, sshconfig: str):
    remote_uploaded_dir = posixpath.join(remote_dir, srcdir.name)
    hasher = hashlib.md5

    sftp = connect_sftp(sshconfig, remote_host)
    try:
        stats = remote_dir_stats(sftp, remote_uploaded_dir, hasher=hasher)
    finally:
        sftp.close()

    return remote_uploaded_dir, stats


def copy(dir: str, config: Config, sshconfig: str, transfer_mode: str = "files", archive_type: str = "tar.gz"):
    # Get relative path from srcdir
    srcdir = Path(os.path.expanduser(dir))
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

    # Prompt user with estimated time to completion
    root = tkinter.Tk()
    root.withdraw()
    progress = tkinter.Toplevel(root)
    progress.title("Estimating Time")
    label = tkinter.Label(progress, text="Estimating transfer time, please wait...")
    label.pack(padx=20, pady=20)
    progress.update()

    # Estimate speed
    st = speedtest.Speedtest()
    estimated_speed = st.upload() # bits/s

    # Calculate directory size
    total_size = get_size(Path(dir))
    total_size_bits = total_size * 8  # convert bytes to bits
    estimated_time_sec = total_size_bits / estimated_speed if estimated_speed else 0

    # Close UI objects
    progress.destroy()
    root.destroy()

    # Calculate completion time
    start_time = datetime.datetime.now()
    completion_time = start_time + datetime.timedelta(seconds=estimated_time_sec)

    # Show dialog with start and estimated completion time
    root = tkinter.Tk()
    root.withdraw()
    result = tkinter.Toplevel(root)
    result.title("Estimated Transfer Time")
    if total_size:
        msg = (
            f"Path: {dir}\n"
            f"Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Total size: {(total_size / 1024):.2f} KB\n"
            f"Estimated speed: {(estimated_speed / 1024 / 8):.2f} KB/s\n"
            f"Estimated completion: {completion_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Estimated duration: {int(estimated_time_sec // 60)} min {int(estimated_time_sec % 60)} sec"
        )
    else:
        msg = (
            f"Path: {dir}\n"
            f"Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Total size: N/A KB\n"
            f"Estimated speed: {(estimated_speed / 1024 / 8):.2f} KB/s\n"
            f"Estimated completion: N/A\n"
            f"Estimated duration: N/A"
        )
    label = tkinter.Label(result, text=msg)
    print_log(msg)
    label.pack(padx=20, pady=20)
    result.update()

    #scp_with_manifest(
    #    input_directory=srcdir,
    #    destination=dest,
    #    ssh_config=sshconfig,
    #    output_manifest=output_manifest,
    #    hash_algorithm=hash_algorithm,
    #)
    # Close the progress dialog
    if transfer_mode == "files":
        use_sftp(srcdir, Path(remote_dir), config.remote_host, sshconfig)
        with ThreadPoolExecutor(max_workers=2) as executor:
            local_stats_future = executor.submit(dir_stats, str(srcdir), hashlib.md5)
            remote_stats_future = executor.submit(
                _remote_dir_with_stats,
                srcdir,
                remote_dir,
                config.remote_host,
                sshconfig,
            )
            local_stats = local_stats_future.result()
            remote_uploaded_dir, remote_stats = remote_stats_future.result()
        print_log(f"Directory uploaded at {remote_uploaded_dir}")
    elif transfer_mode == "archive":
        archive_path = _archive_with_stats(
            srcdir=srcdir,
            remote_dir=remote_dir,
            remote_host=config.remote_host,
            sshconfig=sshconfig,
            archive_type=archive_type,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            local_stats_future = executor.submit(dir_stats, str(srcdir), hashlib.md5)
            remote_stats_future = executor.submit(
                _archive_stats_from_remote,
                archive_path,
                config.remote_host,
                sshconfig,
                archive_type,
            )
            local_stats = local_stats_future.result()
            remote_stats = remote_stats_future.result()
        print_log(f"Archive created at {archive_path}")
    else:
        raise ValueError("transfer_mode must be either 'files' or 'archive'")

    root.destroy()
    return start_time, local_stats, remote_stats

def use_sftp(srcdir: Path, remote_dir: Path, remote_host: str, sshconfig: str):
    script = f'cd {remote_dir}\nmkdir "{srcdir}"\ncd "{srcdir}"\nput -rp .\nexit\n'
    with subprocess.Popen(
        ["sftp",  "-b", "-", "-F", f"{sshconfig}", remote_host],
        stdin=subprocess.PIPE,
        text=True,
        cwd=srcdir
    ) as proc:
        if proc.stdin is None:
            raise ValueError("Failed to open stdin for SFTP process.")
        proc.stdin.write(script)
        proc.stdin.close()
        proc.wait()
    
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
                path = os.path.join(root, f)
                os.remove(path)
                print_log(f"deleted {path}")

            for d in dirs:
                path = os.path.join(root, d)
                try:
                    os.rmdir(path)
                    print_log(f"Deleted {path}")
                except OSError:
                    print_log(f"{path} is not empty", "error")

    return True

def cleanup(workdir: str):
    # Remove the temporary directory
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
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
    try:
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
        if not os.path.exists(workdir):
            os.makedirs(workdir)

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
            'localbase': os.path.expanduser('~'),
            'remotebase': 'instrument_data',
            'remote_host': os.environ['REMOTE_HOST'],
            'sshauthz': os.environ['SSHAUTHZ']
        })

        # Setup ssh config
        (dir, ssh_config, _, delete_agreement_bool) = setup(config.localbase, workdir, config)

        transfer_mode = os.environ.get("TRANSFER_MODE", "files").strip().lower()
        archive_type = os.environ.get("ARCHIVE_TYPE", "tar.gz").strip().lower()

        # Copy files or create remote archive depending on transfer mode
        start_time, local_stats, remote_stats = copy(
            dir,
            config=config,
            sshconfig=ssh_config,
            transfer_mode=transfer_mode,
            archive_type=archive_type,
        )

        verify(remote_stats, local_stats, dir, delete_agreement_bool, Path(dir).name)

        cleanup(workdir)

        end_time = datetime.datetime.now()
        print_log(f"Process started: {end_time.strftime('%Y-%m-%d %H:%M:%S')}\nProcess finished: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")

        input("Press ENTER to exit.")

    except Exception as e:
        print_log(e, "error")
        input("Press ENTER to exit.")



if __name__ == "__main__":
    main()
