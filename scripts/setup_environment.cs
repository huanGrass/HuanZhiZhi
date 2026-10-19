using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

internal sealed class EnvironmentSetup : Form
{
    internal const string Probe = "import ctypes, multiprocessing, sqlite3, ssl, pip, numpy, cv2, PIL, windows_capture; from PySide6 import QtCore, QtGui, QtWidgets";
    private readonly string root;
    private readonly Label status = new Label();
    private readonly ProgressBar progress = new ProgressBar();
    private readonly TextBox log = new TextBox();
    private readonly Button retry = new Button();
    private readonly Button cancel = new Button();
    private WebClient download;
    private Process process;
    private bool running, cancelled;

    internal EnvironmentSetup(string directory)
    {
        root = directory;
        Text = "幻之之 · 安装运行环境";
        Font = new Font("Microsoft YaHei UI", 10);
        ClientSize = new Size(640, 390);
        MinimumSize = Size;
        StartPosition = FormStartPosition.CenterScreen;
        status.SetBounds(20, 18, 600, 48);
        status.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        progress.SetBounds(20, 70, 600, 20);
        progress.Anchor = status.Anchor;
        log.SetBounds(20, 105, 600, 220);
        log.Multiline = true;
        log.ReadOnly = true;
        log.ScrollBars = ScrollBars.Vertical;
        log.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
        retry.Text = "重试";
        retry.SetBounds(420, 343, 95, 30);
        retry.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
        retry.Visible = false;
        cancel.Text = "取消";
        cancel.SetBounds(525, 343, 95, 30);
        cancel.Anchor = retry.Anchor;
        Controls.AddRange(new Control[] { status, progress, log, retry, cancel });
        retry.Click += async delegate { await Install(); };
        cancel.Click += delegate { Close(); };
        FormClosing += delegate(object sender, FormClosingEventArgs e)
        {
            if (!running) return;
            e.Cancel = true;
            cancelled = true;
            cancel.Enabled = false;
            status.Text = "正在取消，请稍候…";
            if (download != null) download.CancelAsync();
            if (process != null)
                try { if (!process.HasExited) process.Kill(); }
                catch (InvalidOperationException) { }
        };
        Shown += async delegate { await Install(); };
    }

    internal static ProcessStartInfo PythonStart(string directory, string arguments)
    {
        var start = new ProcessStartInfo(Path.Combine(directory, "python.exe"), "-I -X utf8 " + arguments);
        start.WorkingDirectory = directory;
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.EnvironmentVariables.Remove("PYTHONHOME");
        start.EnvironmentVariables.Remove("PYTHONPATH");
        return start;
    }

    internal static bool IsReady(string directory)
    {
        if (!File.Exists(Path.Combine(directory, "pythonw.exe")) ||
            !File.Exists(Path.Combine(directory, "python.exe"))) return false;
        try
        {
            using (var child = Process.Start(PythonStart(directory, "-c \"" + Probe + "\"")))
            {
                if (!child.WaitForExit(30000)) { child.Kill(); child.WaitForExit(); return false; }
                return child.ExitCode == 0;
            }
        }
        catch (Exception) { return false; }
    }

    private void CheckCancelled()
    {
        if (cancelled) throw new OperationCanceledException();
    }

    private void WriteLog(string value)
    {
        if (value == null) return;
        if (InvokeRequired) { BeginInvoke(new Action<string>(WriteLog), value); return; }
        log.AppendText(value + Environment.NewLine);
    }

    private void Step(string text)
    {
        CheckCancelled();
        status.Text = text;
        progress.Style = ProgressBarStyle.Marquee;
        WriteLog(text);
    }

    private async Task Download(string url, string file, string expected)
    {
        CheckCancelled();
        using (download = new WebClient())
        {
            download.DownloadProgressChanged += delegate(object sender, DownloadProgressChangedEventArgs e)
            {
                progress.Style = e.TotalBytesToReceive > 0 ? ProgressBarStyle.Continuous : ProgressBarStyle.Marquee;
                progress.Value = e.ProgressPercentage;
                status.Text = "正在下载 " + Path.GetFileName(file) + "：" +
                    (e.BytesReceived / 1048576.0).ToString("F1") + " MB" +
                    (e.TotalBytesToReceive > 0 ? " / " + (e.TotalBytesToReceive / 1048576.0).ToString("F1") + " MB" : "");
            };
            try { await download.DownloadFileTaskAsync(new Uri(url), file); }
            finally { download = null; }
        }
        CheckCancelled();
        using (var hash = SHA256.Create())
        using (var input = File.OpenRead(file))
            if (BitConverter.ToString(hash.ComputeHash(input)).Replace("-", "").ToLowerInvariant() != expected)
                throw new InvalidDataException("下载文件校验失败，请重试。");
    }

    private async Task RunPython(string directory, string arguments)
    {
        CheckCancelled();
        var start = PythonStart(directory, arguments);
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        start.StandardOutputEncoding = Encoding.UTF8;
        start.StandardErrorEncoding = Encoding.UTF8;
        using (var child = new Process { StartInfo = start })
        {
            child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { WriteLog(e.Data); };
            child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { WriteLog(e.Data); };
            child.Start();
            process = child;
            child.BeginOutputReadLine();
            child.BeginErrorReadLine();
            try
            {
                await Task.Run(() => child.WaitForExit());
                CheckCancelled();
                if (child.ExitCode != 0) throw new InvalidOperationException("安装命令失败，请查看上方日志，检查网络后重试。");
            }
            finally { process = null; }
        }
    }

    private async Task InstallDependencies(string stage)
    {
        string command = "-u -m pip --isolated --disable-pip-version-check install --no-cache-dir --no-warn-script-location --progress-bar off --only-binary=:all: --timeout 15 --retries 1";
        string requirements = " -r \"" + Path.Combine(root, "requirements.txt") + "\"";
        Step("3/4 安装项目依赖（清华镜像）…");
        try
        {
            await RunPython(stage, command + " --index-url https://pypi.tuna.tsinghua.edu.cn/simple" + requirements);
            return;
        }
        catch (InvalidOperationException)
        {
            CheckCancelled();
            Step("清华镜像安装失败，正在改用 PyPI 官方源重试…");
        }
        await RunPython(stage, command + " --index-url https://pypi.org/simple" + requirements);
    }

    private async Task Install()
    {
        running = true;
        cancelled = false;
        retry.Visible = false;
        cancel.Enabled = true;
        cancel.Text = "取消";
        string temporary = Path.Combine(root, ".setup-" + Guid.NewGuid().ToString("N"));
        string stage = Path.Combine(temporary, "python");
        bool success = false;
        try
        {
            if (!File.Exists(Path.Combine(root, "requirements.txt")))
                throw new FileNotFoundException("缺少 requirements.txt，请完整下载并解压仓库。");
            Directory.CreateDirectory(temporary);
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            Step("1/4 下载 Python 运行环境…");
            string archive = Path.Combine(temporary, "python.zip");
            await Download("https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
                archive, "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b");
            Step("正在解压运行环境…");
            await Task.Run(() =>
            {
                ZipFile.ExtractToDirectory(archive, stage);
                ZipFile.ExtractToDirectory(Path.Combine(stage, "python311.zip"), Path.Combine(stage, "Lib"));
                Directory.CreateDirectory(Path.Combine(stage, "DLLs"));
                // pip removes the working directory from sys.path. Match the regular
                // Python layout so extension modules remain importable during install.
                foreach (string module in Directory.GetFiles(stage, "*.pyd"))
                    File.Move(module, Path.Combine(stage, "DLLs", Path.GetFileName(module)));
                foreach (string name in new[] { "libcrypto-3.dll", "libssl-3.dll", "sqlite3.dll" })
                    File.Move(Path.Combine(stage, name), Path.Combine(stage, "DLLs", name));
                Directory.CreateDirectory(Path.Combine(stage, "Lib", "site-packages"));
                File.WriteAllText(Path.Combine(stage, "python311._pth"),
                    ".\nLib\nDLLs\nLib/site-packages\n../src\n..\nimport site\n", new UTF8Encoding(false));
            });
            Step("2/4 下载依赖安装工具…");
            string wheel = Path.Combine(temporary, "pip-24.0-py3-none-any.whl");
            await Download("https://files.pythonhosted.org/packages/8a/6a/19e9fe04fca059ccf770861c7d5721ab4c2aebc539889e97c7977528a53b/pip-24.0-py3-none-any.whl",
                wheel, "ba0d021a166865d2265246961bec0152ff124de910c5cc39f1156ce3fa7c69dc");
            await Task.Run(() => ZipFile.ExtractToDirectory(wheel, Path.Combine(stage, "Lib", "site-packages")));
            await InstallDependencies(stage);
            Step("4/4 验证运行环境…");
            await RunPython(stage, "-m pip --isolated check");
            await RunPython(stage, "-c \"" + Probe + "; import json, sys; from pathlib import Path; Path('runtime-info.json').write_text(json.dumps({'version': sys.version.split()[0], 'pip': pip.__version__}))\"");
            CheckCancelled();
            string destination = Path.Combine(root, "python");
            string backup = null;
            if (Directory.Exists(destination))
            {
                backup = Path.Combine(root, "python.backup-" + Guid.NewGuid().ToString("N"));
                Directory.Move(destination, backup);
            }
            try { Directory.Move(stage, destination); }
            catch
            {
                if (backup != null) Directory.Move(backup, destination);
                throw;
            }
            success = true;
        }
        catch (Exception error)
        {
            status.Text = cancelled ? "已取消安装。" : "安装未完成。检查网络和目录写入权限后可重试。";
            WriteLog(cancelled ? "已取消安装。" : error.Message);
        }
        finally
        {
            // This unique directory is owned by this attempt; never delete an existing runtime.
            try { if (Directory.Exists(temporary)) Directory.Delete(temporary, true); }
            catch (Exception error) { WriteLog("临时文件未能清理：" + error.Message); }
            running = false;
        }
        if (success) { DialogResult = DialogResult.OK; Close(); }
        else if (cancelled) { DialogResult = DialogResult.Cancel; Close(); }
        else
        {
            progress.Style = ProgressBarStyle.Continuous;
            progress.Value = 0;
            retry.Visible = true;
            cancel.Text = "关闭";
            cancel.Enabled = true;
        }
    }
}
