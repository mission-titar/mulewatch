import shutil
from pathlib import Path

from p2pwatch.adapters.disk_space_shutil import ShutilDiskSpace
from p2pwatch.ports.disk_space import DiskSpace


def test_reports_the_free_bytes_of_the_filesystem_holding_the_path(tmp_path: Path) -> None:
    disk: DiskSpace = ShutilDiskSpace(tmp_path)
    assert disk.free_bytes() == shutil.disk_usage(tmp_path).free
