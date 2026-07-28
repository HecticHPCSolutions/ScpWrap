#!/usr/bin/env python3
"""SFTP archive streaming utility."""

import argparse
import os
import sys
import json
import stat
import threading
from dataclasses import asdict
from typing import Callable

import paramiko
import tarfile
from typing import Literal, Optional, Union
from verify_archive import tar_stats
from sftp_client import connect_sftp


class CancelledError(Exception):
    """Raised when a transfer is cancelled by the user."""
    pass


def iter_files(path):
    for dirpath, dirs, files in os.walk(path, topdown=True, followlinks=False):
        for dirname in list(dirs):
            dir_full_path = os.path.join(dirpath, dirname)
            if os.path.islink(dir_full_path):
                # Keep the symlink entry in the archive, but do not descend into it.
                yield dir_full_path
                dirs.remove(dirname)

        if not dirs and not files:
            yield dirpath  # Preserve empty directories

        for f in files:
            yield os.path.join(dirpath, f)


def write_tar(
    f: paramiko.SFTPFile,
    localpath: str,
    mode: Literal['w', 'w:gz', 'w:bz2', 'w:xz'] = 'w:gz',
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[threading.Event] = None,
):
    """
    Create a tar archive from localpath and write it directly to the file handle.
    
    Args:
        f: paramiko.SFTPFile object to write to
        localpath: Path to local directory to archive
        mode: Tar file mode for compression. Options:
              'w' - no compression
              'w:gz' - gzip compression (default)
              'w:bz2' - bzip2 compression
              'w:xz' - xz compression
    """
    print(f'Creating a tar archive from {localpath} with mode {mode}')
    total_bytes_written = 0
    total_files_written = 0
    
    with tarfile.open(fileobj=f, mode=mode) as tf:
        # Keep localpath as a native filesystem path for os.walk/iter_files.
        # Tar archive member names always use POSIX-style separators, so arcname
        # is converted to forward slashes separately.
        localpath = os.path.normpath(localpath)
        toplevel = os.path.basename(localpath)
        for file_path in iter_files(localpath):
            if cancelled is not None and cancelled.is_set():
                raise CancelledError("Transfer cancelled by user")
            file_size = 0
            file_mode = os.lstat(file_path).st_mode
            if stat.S_ISREG(file_mode):
                file_size = os.path.getsize(file_path)

            # Use the basename of the path to set the arcname
            arcname = os.path.join(toplevel, os.path.relpath(file_path, localpath))
            arcname = arcname.replace(os.sep, '/').replace('\\', '/')
            tf.add(file_path, arcname=arcname, recursive=False)

            total_bytes_written += file_size
            if stat.S_ISREG(file_mode):
                total_files_written += 1
            if progress_callback is not None:
                progress_callback(total_bytes_written, total_files_written)


def infer_arctype_from_archive(archive: str) -> str:
    archive_lower = archive.lower()
    if archive_lower.endswith('.tar.gz') or archive_lower.endswith('.tgz'):
        return 'tar.gz'
    if archive_lower.endswith('.tar.bz2') or archive_lower.endswith('.tbz2'):
        return 'tar.bz2'
    if archive_lower.endswith('.tar.xz') or archive_lower.endswith('.txz'):
        return 'tar.xz'
    raise ValueError(
        f"Could not infer archive type from extension: {archive}. "
        "Provide --arctype explicitly (tar.gz, tar.bz2, tar.xz)."
    )


def create_archive(
    config,
    host,
    archive,
    source,
    arctype,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[threading.Event] = None,
):
    sftp = connect_sftp(config, host)
    try:
        resolved_arctype = arctype or infer_arctype_from_archive(archive)
        with sftp.file(archive,'w') as f:
            if resolved_arctype == "tar.gz":
                write_tar(f,source, mode='w:gz', progress_callback=progress_callback, cancelled=cancelled)
            elif resolved_arctype == "tar.bz2":
                write_tar(f,source, mode='w:bz2', progress_callback=progress_callback, cancelled=cancelled)
            elif resolved_arctype == "tar.xz":
                write_tar(f,source, mode='w:xz', progress_callback=progress_callback, cancelled=cancelled)
            else:
                raise ValueError(
                    f"Unsupported arctype: {resolved_arctype}. "
                    "Supported values are: tar.gz, tar.bz2, tar.xz"
                )
    except FileNotFoundError as e:
        print(f"Tried to open the sftp file object path {archive} but failed.")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"SSH Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        sftp.close()

def stat_archive(
    config: Union[str, None],
    host: Union[str, None],
    archive: str,
    checksum: bool = False,
    output: Union[str, None] = None,
    progress: bool = False,
):
    hasher = None
    if checksum:
        import hashlib
        hasher = hashlib.md5

    arctype = infer_arctype_from_archive(archive)
    if not arctype.startswith('tar'):
        raise ValueError("stat only supports tar archives: .tar.gz/.tgz, .tar.bz2/.tbz2, .tar.xz/.txz")

    mode: Literal['r', 'r:gz', 'r:bz2', 'r:xz']
    if arctype == 'tar.gz':
        mode = 'r:gz'
    elif arctype == 'tar.bz2':
        mode = 'r:bz2'
    else:
        mode = 'r:xz'

    def _progress_callback(processed_files: int) -> None:
        if progress:
            print(f"\rProcessed files: {processed_files}", end="", file=sys.stderr, flush=True)

    try:

        if config is None and host is None:
            with open(archive, 'rb') as f:
                stats = tar_stats(f, hasher=hasher, mode=mode, progress_callback=_progress_callback if progress else None)
        elif config is not None and host is not None:
            sftp = connect_sftp(config, host)
            with sftp.file(archive, 'r') as f:
                stats = tar_stats(f, hasher=hasher, mode=mode, progress_callback=_progress_callback if progress else None)
            sftp.close()

        if progress:
            print(file=sys.stderr)

        payload = {path: asdict(file_stats) for path, file_stats in stats.items()}
        text = json.dumps(payload, indent=2, sort_keys=True)

        if output:
            with open(output, 'w', encoding='utf-8') as out_f:
                out_f.write(text)
            print(f"Wrote archive stats to {output}")
        else:
            print(text)

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"SSH Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Parse arguments and handle SFTP operations."""
    parser = argparse.ArgumentParser(
        description="SFTP archive streaming utility"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create an archive on remote host")
    create_parser.add_argument(
        "--config",
        type=str,
        default=os.path.expanduser("~/.ssh/config"),
        help="Path to SSH config file (default: ~/.ssh/config)",
    )
    create_parser.add_argument(
        "--host",
        type=str,
        required=True,
        help="Host alias from SSH config to connect to",
    )

    create_parser.add_argument(
        "--archive",
        type=str,
        required=True,
        help='Path to the archive (.tar.gz, .tar.bz2, or .tar.xz) to create on the remote server'
    )
    create_parser.add_argument(
        "--source",
        type=str,
        required=True,
        help="Source directory to archive"
    )
    create_parser.add_argument(
        "--arctype",
        type=str,
        required=False,
        default=None,
        help="The type of archive to create"
    )

    stat_parser = subparsers.add_parser("stat", help="Read remote tar archive stats")
    stat_parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to SSH config file (required with --host; omit both for local archive)",
    )
    stat_parser.add_argument(
        "--host",
        type=str,
        required=False,
        help="Host alias from SSH config to connect to (required with --config)",
    )
    stat_parser.add_argument(
        "--archive",
        type=str,
        required=True,
        help="Path to the tar archive to inspect (remote path with SFTP mode, local path otherwise)",
    )
    stat_parser.add_argument(
        "--checksum",
        action="store_true",
        help="Include md5 checksum for each file entry",
    )
    stat_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write stats JSON output to this local file",
    )
    stat_parser.add_argument(
        "--progress",
        action="store_true",
        help="Print the number of files processed while reading archive stats",
    )
    
    args = parser.parse_args()

    if args.command == "create":
        create_archive(args.config,args.host,args.archive,args.source,args.arctype)
    elif args.command == "stat":
        if (args.config is None) != (args.host is None):
            print("Error: --config and --host must be provided together, or both omitted for local archive stat", file=sys.stderr)
            sys.exit(1)

        if args.config is not None:
            args.config = os.path.expanduser(args.config)
        
        stat_archive(args.config, args.host, args.archive, args.checksum, args.output, args.progress)




if __name__ == "__main__":
    main()
