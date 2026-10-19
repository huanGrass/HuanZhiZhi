using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Launcher
{
    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string root = AppDomain.CurrentDomain.BaseDirectory;
        try
        {
            string key;
            using (var hash = SHA256.Create())
                key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Replace("-", "");
            bool acquired;
            using (var gate = new Mutex(true, @"Local\HuanZhiZhiSetup-" + key, out acquired))
            {
                if (!acquired) return 0;
                try
                {
                    if (!File.Exists(Path.Combine(root, "src", "huanzhizhi", "__main__.py")))
                        throw new FileNotFoundException("缺少运行文件，请完整解压发行包或源码仓库后再启动。\n请勿单独移动幻之之.exe。\n\n所在目录：" + root);
                    string runtime = Path.Combine(root, "python");
                    if (!EnvironmentSetup.IsReady(runtime))
                    {
                        if (MessageBox.Show("尚未安装运行环境，或环境不完整。是否现在安装？\n\n将联网下载 Python 和项目依赖，保存在当前项目的 python 文件夹中。安装期间会显示进度，完成后自动启动。\n\n已有的不完整环境将保留为备份。",
                            "幻之之 · 安装运行环境", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                            return 0;
                        using (var setup = new EnvironmentSetup(root))
                            if (setup.ShowDialog() != DialogResult.OK) return 1;
                    }
                    var start = new ProcessStartInfo
                    {
                        FileName = Path.Combine(runtime, "pythonw.exe"),
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
                finally { gate.ReleaseMutex(); }
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "幻之之启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
