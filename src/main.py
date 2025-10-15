import datetime
import tkinter
import yaml
import os
import paramiko
import posixpath
import shutil
import speedtest
import subprocess
import sys
import tempfile
import time
import webbrowser
from os import PathLike
from pathlib import Path
from tkinter import ttk
from tkinter import filedialog
from typing import Tuple
from urllib.parse import urlencode

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
    

def mk_ssh_config(workdir: Path, ssh_config: str, config: Config):
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
    print("cert found, begin uploading")
    shutil.move(os.path.join(os.path.expanduser("~"),"Downloads",f"{keyname}-cert.pub"), os.path.join(workdir,f"{keyname}-cert.pub"))

    # use ssh-keygen to query the certificate for the valid principals and add them to the ssh config
    p = subprocess.run(["ssh-keygen", "-L", "-f", os.path.join(workdir,f"{keyname}-cert.pub")], check=True, capture_output=True, text=True)

    # parse the output
    lines = p.stdout.splitlines()
    principals = []
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
            ], shell=True)
            return
        except:
            print("MS Edge not found")

    default = webbrowser.get()
    if "firefox" in default.name.lower():
       webbrowser.get(f"{default.name} -private-window %s").open_new(url)
       return
    if "chrome" in default.name.lower():
       webbrowser.get(f"{default.name} --incognito %s").open_new(url)
       return
    else:
        raise ValueError(f"Browser {default} is not supported. SCPWrap supports MSEdge, Firefox and Chrome.")

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

def setup(localbase: str, workdir: str, config: Config) -> Tuple[str, str]:
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

    # Make SSH Config
    ssh_config = os.path.join(os.path.expanduser(workdir), "ssh.cfg")
    ssh_details = mk_ssh_config(Path(os.path.expanduser(workdir)), ssh_config, config)
    
    return (directory, ssh_config, ssh_details, delete_agreement)


def get_dir_size_windows(path):
    cmd = [
        "powershell",
        "-Command",
        f"(Get-ChildItem '{path}' -Recurse | Measure-Object -Property Length -Sum).Sum"
    ]
    result = subprocess.check_output(cmd, universal_newlines=True)
    return int(result.strip()) if result.strip().isdigit() else 0

def get_dir_size_linux(path):
    cmd = [
        "du",
        "-sb",
        path
    ]
    result = subprocess.check_output(cmd, universal_newlines=True)
    return int(result.split()[0]) if result.strip().isdigit() else 0

def get_size(path: PathLike) -> int:
    """
    Get the size of a directory in bytes.
    :param path: Path to the directory.
    :return: Size in bytes.
    """
    if os.name == 'nt':  # Windows
        return get_dir_size_windows(path)
    else:  # Linux or other OS
        return get_dir_size_linux(path)


def copy(dir: str, config: Config, workdir: str, sshconfig: str):
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
    # estimated_speed = 50000  # in KB/s
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
    msg = (
        f"Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Total size: {(total_size / 1024):.2f} KB\n"
        f"Estimated speed: {(estimated_speed / 1024 / 8):.2f} KB/s\n"
        f"Estimated completion: {completion_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Estimated duration: {int(estimated_time_sec // 60)} min {int(estimated_time_sec % 60)} sec"
    )
    label = tkinter.Label(result, text=msg)
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
    use_sftp(srcdir, Path(remote_dir), config.remote_host, sshconfig)
    root.destroy()
    return

def use_sftp(srcdir: PathLike, remote_dir: PathLike, remote_host: str, sshconfig: str):
    script = f'cd {remote_dir}\nmkdir "{srcdir.name}"\ncd "{srcdir.name}"\nput -rp .\nexit\n'
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
    
def verify(remote_host, username, key_filename, dir, remotebase, delete_agreement):
    print("begin verifying")
    # Connect to the remote
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy()) # no known_hosts error
    client.connect(remote_host, username=username, key_filename=key_filename)

    sftp = paramiko.SFTPClient.from_transport(client.get_transport())
    
    # Iterate through local directory
    for root, dirs, files in os.walk(dir, topdown=False):
        for f in files:
            path = f"{root}/{f}"
            stat_local = os.stat(path)
            relpath = Path(os.path.relpath(path, dir)).as_posix()
            # Check size and mtimes match
            try:
                stat_remote = sftp.stat(f"{remotebase}/{Path(dir).name}/{relpath}")
                assert abs(stat_local.st_mtime - stat_remote.st_mtime) < 1
                assert stat_local.st_size == stat_remote.st_size
                print(f"verified {path}")

                if delete_agreement:
                    os.remove(path)
                    print(f"deleted {path}")
                
            except AssertionError:
                print(f"{path} mtime or size does not match")
            except FileNotFoundError:
                print(f"{path} not found")
        
        for d in dirs:
            if delete_agreement:
                try:
                    path = f"{root}/{d}"
                    os.rmdir(path)
                    print(f"Deleted {path}")
                except OSError:
                    print(f"{path} is not empty")

def cleanup(workdir: str):
    # Remove the temporary directory
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
        print(f"Removed temporary directory: {workdir}")
    else:
        print(f"Directory {workdir} does not exist.")
    return

def main():
    try:
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
        (dir, ssh_config, ssh_details, delete_agreement) = setup(config.localbase, workdir, config)

        # Copy files
        copy(dir, config=config, workdir=workdir, sshconfig=ssh_config)
        verify(*ssh_details, dir, config.remotebase, delete_agreement)

        cleanup(workdir)

        input("Press ENTER to exit.")

    except Exception as e:
        print(e)
        input("Press ENTER to exit.")



if __name__ == "__main__":
    main()
