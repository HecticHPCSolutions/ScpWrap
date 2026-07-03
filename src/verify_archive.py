import paramiko
from typing import Literal, Union
import argparse
import tarfile
import os
import stat
from dataclasses import dataclass, field
from sftp_client import connect_sftp



@dataclass
class FileStats:
    path: str
    mtime: int
    size: int
    hash: Union[str, None] = None

@dataclass
class StatsSummary:
    """Summary of comparison between two sets of file statistics."""
    files_in_archive_only: list[str] = field(default_factory=list)
    files_in_source_only: list[str] = field(default_factory=list)
    files_with_size_diff: dict[str, tuple[int, int]] = field(default_factory=dict)
    files_with_mtime_diff: dict[str, tuple[int, int]] = field(default_factory=dict)
    files_with_checksum_diff: dict[str, tuple[str, str]] = field(default_factory=dict)
    matching_files: list[str] = field(default_factory=list)


def dir_stats(path: str, hasher=None) -> dict[str, FileStats]:
    """
    Walk a directory and create FileStats objects for each file found.
    
    :param path: Path to directory to walk
    :type path: str
    :param hasher: Optional hasher constructor to compute checksums
    :return: Dictionary with file paths as keys and FileStats objects as values
    :rtype: dict[str, FileStats]
    """
    files = {}
    for root, dirs, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(root, filename)
            relative_path = os.path.relpath(filepath, os.path.normpath(os.path.join(path,'..')))
            stat = os.stat(filepath)
            checksum = None
            
            if hasher is not None:
                h = hasher()
                with open(filepath, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b''):
                        h.update(chunk)
                checksum = h.hexdigest()
            
            files[relative_path] = FileStats(
                path=filename,
                mtime=int(stat.st_mtime),
                size=stat.st_size,
                hash=checksum
            )
    return files


def remote_dir_stats(sftp: paramiko.SFTPClient, path: str, hasher=None) -> dict[str, FileStats]:
    """
    Walk a remote directory over SFTP and create FileStats objects for each file found.

    :param sftp: Connected SFTP client
    :type sftp: paramiko.SFTPClient
    :param path: Remote path to directory to walk
    :type path: str
    :param hasher: Optional hasher constructor to compute checksums
    :return: Dictionary with file paths as keys and FileStats objects as values
    :rtype: dict[str, FileStats]
    """
    files = {}
    base_parent = os.path.normpath(os.path.join(path, '..'))

    def _walk(remote_dir: str):
        for entry in sftp.listdir_attr(remote_dir):
            remote_path = os.path.join(remote_dir, entry.filename)

            if stat.S_ISDIR(entry.st_mode or 0):
                _walk(remote_path)
                continue

            relative_path = os.path.relpath(remote_path, base_parent)
            checksum = None

            if hasher is not None:
                h = hasher()
                with sftp.file(remote_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b''):
                        h.update(chunk)
                checksum = h.hexdigest()

            files[relative_path] = FileStats(
                path=entry.filename,
                mtime=int(entry.st_mtime or 0),
                size=entry.st_size or 0,
                hash=checksum,
            )

    _walk(path)
    return files

from io import BufferedReader
def tar_stats(f: Union[paramiko.SFTPFile, BufferedReader], hasher=None, mode: Literal['r', 'r:gz', 'r:bz2', 'r:xz'] = 'r:gz') -> dict[str, FileStats]:
    """
    Read a tar file and compute a FileStats object for each member found.
    
    :param f: A filehandle to a tar file
    :type f: paramiko.SFTPFile
    :param hasher: Optional hasher constructor to compute checksums
    :param mode: tarfile mode, for reading. May have gz bz2 or xz compressions
    :type mode: Literal['r', 'r:gz', 'r:bz2', 'r:xz']
    :return: Dictionary with tar member paths as keys and FileStats objects as values
    :rtype: dict[str, FileStats]
    """
    files = {}
    with tarfile.open(fileobj=f, mode=mode) as tf:
        for member in tf.getmembers():
            if member.isfile():
                checksum = None
                
                if hasher is not None:
                    h = hasher()
                    member_file = tf.extractfile(member)
                    if member_file is not None:
                        while True:
                            chunk = member_file.read(8192)
                            if not chunk:
                                break
                            h.update(chunk)
                        checksum = h.hexdigest()
                
                files[member.name] = FileStats(
                    path=member.name,
                    mtime=int(member.mtime),
                    size=member.size,
                    hash=checksum
                )
    return files




def compare_stats(a: dict[str, FileStats], b: dict[str, FileStats]) -> StatsSummary:
    """
    Compare two sets of file statistics and return a summary of differences.
    
    :param a: Dictionary of FileStats from archive
    :type a: dict[str, FileStats]
    :param b: Dictionary of FileStats from source
    :type b: dict[str, FileStats]
    :return: Summary of comparison results
    :rtype: StatsSummary
    """
    summary = StatsSummary()
    
    # Find files in archive but not in source
    for filepath in a:
        if filepath not in b:
            summary.files_in_archive_only.append(filepath)
    
    # Find files in source but not in archive
    for filepath in b:
        if filepath not in a:
            summary.files_in_source_only.append(filepath)
    
    # Compare files that exist in both
    for filepath in a:
        if filepath in b:
            archive_file = a[filepath]
            source_file = b[filepath]
            
            has_diff = False
            
            # Check size differences
            if archive_file.size != source_file.size:
                summary.files_with_size_diff[filepath] = (archive_file.size, source_file.size)
                has_diff = True
            
            # Check mtime differences
            if archive_file.mtime != source_file.mtime:
                summary.files_with_mtime_diff[filepath] = (archive_file.mtime, source_file.mtime)
                has_diff = True
            
            # Check checksum differences
            if archive_file.hash is not None and source_file.hash is not None:
                if archive_file.hash != source_file.hash:
                    summary.files_with_checksum_diff[filepath] = (archive_file.hash, source_file.hash)
                    has_diff = True
            
            # If no differences found, add to matching files
            if not has_diff:
                summary.matching_files.append(filepath)
    
    return summary


def print_summary(summary: StatsSummary) -> str:
    """
    Print a formatted summary of the comparison results.
    
    :param summary: The StatsSummary object containing comparison results
    :type summary: StatsSummary
    """
    print("\n" + "="*60)
    print("ARCHIVE VERIFICATION SUMMARY")
    print("="*60)
    
    # Matching files
    print(f"\n✓ Matching Files: {len(summary.matching_files)}")
    
    # Files only in archive
    if summary.files_in_archive_only:
        print(f"\n⚠ Files in Archive Only: {len(summary.files_in_archive_only)}")
        for filepath in summary.files_in_archive_only:
            print(f"  - {filepath}")
    
    # Files only in source
    if summary.files_in_source_only:
        print(f"\n⚠ Files in Source Only: {len(summary.files_in_source_only)}")
        for filepath in summary.files_in_source_only:
            print(f"  - {filepath}")
    
    # Size differences
    if summary.files_with_size_diff:
        print(f"\n✗ Files with Size Differences: {len(summary.files_with_size_diff)}")
        for filepath, (archive_size, source_size) in summary.files_with_size_diff.items():
            print(f"  - {filepath}: archive={archive_size}, source={source_size}")
    
    # Mtime differences
    if summary.files_with_mtime_diff:
        print(f"\n✗ Files with Modification Time Differences: {len(summary.files_with_mtime_diff)}")
        for filepath, (archive_mtime, source_mtime) in summary.files_with_mtime_diff.items():
            print(f"  - {filepath}: archive={archive_mtime}, source={source_mtime}")
    
    # Checksum differences
    if summary.files_with_checksum_diff:
        print(f"\n✗ Files with Checksum Differences: {len(summary.files_with_checksum_diff)}")
        for filepath, (archive_checksum, source_checksum) in summary.files_with_checksum_diff.items():
            print(f"  - {filepath}")
            print(f"    archive: {archive_checksum}")
            print(f"    source:  {source_checksum}")
    
    print("\n" + "="*60 + "\n")
    if summary.files_in_archive_only or summary.files_in_source_only or summary.files_with_checksum_diff or summary.files_with_mtime_diff or summary.files_with_size_diff:
        return "different"
    else:
        return "identical"


def verify_archive(config, host, archive, source, arctype, hash: Union[Literal['xxhash','md5'],None]=None):
    if hash is None:
        hasher = None
    if hash == 'md5':
        import hashlib
        hasher = hashlib.md5
    if hash == 'xxhash':
        import xxhash
        hasher = xxhash.xxh64
    sftp = connect_sftp(config, host)
    with sftp.file(archive,'r') as f:
        if arctype == "tar.gz":
            archive_stats = tar_stats(f,hasher=hasher, mode='r:gz')
        if arctype == "tar.bz2":
            archive_stats = tar_stats(f,hasher=hasher, mode='r:bz2')
    source_stats = dir_stats(source, hasher=hasher)
    summary = compare_stats(archive_stats,source_stats)
    #print_summary(summary)
    return summary


def main():
    """Parse arguments and handle SFTP operations."""
    parser = argparse.ArgumentParser(
        description="SFTP archive streaming utility"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=os.path.expanduser("~/.ssh/config"),
        help="Path to SSH config file (default: ~/.ssh/config)",
    )
    parser.add_argument(
        "--host",
        type=str,
        required=True,
        help="Host alias from SSH config to connect to",
    )

    parser.add_argument(
        "--archive",
        type=str,
        required=True,
        help='Path to the archive (.tar.gz or .tar.bz2) to create on the remote server'
    )
    parser.add_argument(
        "--source",
        type=str,
        required=True,
        help="Source directory to archive"
    )
    parser.add_argument(
        "--arctype",
        type=str,
        required=False,
        default="tar.gz",
        help="The type of archive to create"
    )
    
    args = parser.parse_args()

    verify_archive(args.config,args.host,args.archive,args.source,args.arctype)



if __name__ == "__main__":
    main()
