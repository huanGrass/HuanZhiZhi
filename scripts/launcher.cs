using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Launcher
{
    [STAThread]
    private static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "python", "pythonw.exe");
        string entry = Path.Combine(root, "src", "huanzhizhi", "__main__.py");
        try
        {
            if (!File.Exists(python) || !File.Exists(entry))
            {
                throw new FileNotFoundException(
                    "缺少运行文件，请完整解压发行包后再启动。\n" +
                    "请将幻之之.exe 与 python、src、tasks 文件夹放在一起。\n\n" +
                    "所在目录：" + root);
            }
            var start = new ProcessStartInfo
            {
                FileName = python,
                Arguments = "-I -X utf8 -m huanzhizhi",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            // The host owns the single-instance lock and its own lifetime.
            // Do not set WindowStyle.Hidden: it also hides the first Qt window.
            using (Process process = Process.Start(start)) { }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "幻之之启动失败", MessageBoxButtons.OK,
                            MessageBoxIcon.Error);
            return 1;
        }
    }
}
