"""Build the small Windows GUI launcher using the system .NET Framework compiler."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib

from PIL import Image


def build_launcher(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        raise RuntimeError(f"找不到 Windows .NET Framework C# 编译器：{compiler}")
    if output.exists():
        raise FileExistsError(f"不覆盖已有启动器：{output}")
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="launcher-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        icon = temporary / "app.ico"
        with Image.open(root / "src/huanzhizhi/assets/app_icon.png") as image:
            image.save(icon, format="ICO", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
        metadata = temporary / "assembly.cs"
        metadata.write_text(
            'using System.Reflection;\n'
            '[assembly: AssemblyTitle("幻之之")]\n'
            '[assembly: AssemblyProduct("HuanZhiZhi")]\n'
            '[assembly: AssemblyDescription("幻之之桌宠启动器")]\n'
            f'[assembly: AssemblyVersion("{version}")]\n'
            f'[assembly: AssemblyFileVersion("{version}")]\n', encoding="utf-8",
        )
        executable = temporary / output.name
        result = subprocess.run(
            [str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
             "/debug-", "/utf8output", "/reference:System.Windows.Forms.dll",
             "/reference:System.Drawing.dll", "/reference:System.IO.Compression.FileSystem.dll",
             f"/out:{executable}", f"/win32icon:{icon}",
             str(root / "scripts/launcher.cs"), str(root / "scripts/setup_environment.cs"), str(metadata)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError(f"启动器编译失败：\n{result.stdout}{result.stderr}")
        executable.replace(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build_launcher(Path(__file__).resolve().parents[1], args.output))
