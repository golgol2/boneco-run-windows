using System.Diagnostics;
using System.Drawing;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new InstallerForm(args));
    }
}

internal sealed class InstallerForm : Form
{
    private const string AppName = "BONECO RUN Windows";
    private readonly string[] args;
    private readonly TextBox termsBox = new();
    private readonly TextBox logBox = new();
    private readonly CheckBox acceptTerms = new();
    private readonly Button installButton = new();
    private readonly Button closeButton = new();
    private readonly ProgressBar progressBar = new();
    private readonly Label statusLabel = new();
    private readonly Label installPathLabel = new();
    private string tempRoot = "";

    public InstallerForm(string[] args)
    {
        this.args = args;
        BuildUi();
        Load += (_, _) => PrepareInstaller();
        FormClosed += (_, _) => TryDeleteDirectory(tempRoot);
    }

    private void BuildUi()
    {
        Text = AppName + " - Instalador";
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(760, 620);
        Size = new Size(860, 680);
        Font = new Font("Segoe UI", 10F, FontStyle.Regular, GraphicsUnit.Point);
        BackColor = Color.FromArgb(16, 20, 24);
        ForeColor = Color.FromArgb(238, 244, 247);

        var appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        if (appIcon is not null)
        {
            Icon = appIcon;
        }

        var root = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            RowCount = 4,
            ColumnCount = 1,
            Padding = new Padding(18),
            BackColor = BackColor,
        };
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 92));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 138));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 106));
        Controls.Add(root);

        var header = new Panel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(20, 28, 36) };
        var iconBox = new PictureBox
        {
            Width = 64,
            Height = 64,
            Left = 14,
            Top = 14,
            SizeMode = PictureBoxSizeMode.Zoom,
            Image = appIcon?.ToBitmap(),
        };
        var title = new Label
        {
            AutoSize = false,
            Left = 94,
            Top = 14,
            Width = 620,
            Height = 32,
            Text = "Instalar BONECO RUN Windows",
            Font = new Font(Font, FontStyle.Bold),
            ForeColor = Color.White,
        };
        var subtitle = new Label
        {
            AutoSize = false,
            Left = 94,
            Top = 48,
            Width = 700,
            Height = 26,
            Text = "Instalador grafico independente, com aceite dos termos e icone oficial.",
            ForeColor = Color.FromArgb(154, 168, 181),
        };
        header.Controls.Add(iconBox);
        header.Controls.Add(title);
        header.Controls.Add(subtitle);
        root.Controls.Add(header, 0, 0);

        termsBox.Dock = DockStyle.Fill;
        termsBox.Multiline = true;
        termsBox.ReadOnly = true;
        termsBox.ScrollBars = ScrollBars.Vertical;
        termsBox.BackColor = Color.FromArgb(13, 19, 24);
        termsBox.ForeColor = Color.FromArgb(238, 244, 247);
        termsBox.BorderStyle = BorderStyle.FixedSingle;
        termsBox.Text = "Carregando termos...";
        root.Controls.Add(termsBox, 0, 1);

        var controls = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            RowCount = 4,
            ColumnCount = 1,
            Padding = new Padding(0, 12, 0, 0),
        };
        controls.RowStyles.Add(new RowStyle(SizeType.Absolute, 30));
        controls.RowStyles.Add(new RowStyle(SizeType.Absolute, 34));
        controls.RowStyles.Add(new RowStyle(SizeType.Absolute, 30));
        controls.RowStyles.Add(new RowStyle(SizeType.Absolute, 34));
        root.Controls.Add(controls, 0, 2);

        installPathLabel.Dock = DockStyle.Fill;
        installPathLabel.ForeColor = Color.FromArgb(154, 168, 181);
        controls.Controls.Add(installPathLabel, 0, 0);

        acceptTerms.Text = "Li e aceito os termos de uso, licenca e protecao de dados.";
        acceptTerms.Dock = DockStyle.Fill;
        acceptTerms.ForeColor = ForeColor;
        acceptTerms.CheckedChanged += (_, _) => installButton.Enabled = acceptTerms.Checked && !string.IsNullOrWhiteSpace(tempRoot);
        controls.Controls.Add(acceptTerms, 0, 1);

        statusLabel.Dock = DockStyle.Fill;
        statusLabel.ForeColor = Color.FromArgb(154, 168, 181);
        statusLabel.Text = "Preparando instalador...";
        controls.Controls.Add(statusLabel, 0, 2);

        var bottomLine = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1 };
        bottomLine.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        bottomLine.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 130));
        bottomLine.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 130));
        controls.Controls.Add(bottomLine, 0, 3);

        progressBar.Dock = DockStyle.Fill;
        progressBar.Style = ProgressBarStyle.Continuous;
        bottomLine.Controls.Add(progressBar, 0, 0);

        installButton.Text = "Instalar";
        installButton.Dock = DockStyle.Fill;
        installButton.Enabled = false;
        installButton.BackColor = Color.FromArgb(33, 90, 153);
        installButton.ForeColor = Color.White;
        installButton.FlatStyle = FlatStyle.Flat;
        installButton.Click += async (_, _) => await InstallAsync();
        bottomLine.Controls.Add(installButton, 1, 0);

        closeButton.Text = "Cancelar";
        closeButton.Dock = DockStyle.Fill;
        closeButton.BackColor = Color.FromArgb(32, 40, 50);
        closeButton.ForeColor = Color.White;
        closeButton.FlatStyle = FlatStyle.Flat;
        closeButton.Click += (_, _) => Close();
        bottomLine.Controls.Add(closeButton, 2, 0);

        logBox.Dock = DockStyle.Fill;
        logBox.Multiline = true;
        logBox.ReadOnly = true;
        logBox.ScrollBars = ScrollBars.Vertical;
        logBox.BackColor = Color.FromArgb(12, 18, 22);
        logBox.ForeColor = Color.FromArgb(154, 168, 181);
        logBox.BorderStyle = BorderStyle.FixedSingle;
        root.Controls.Add(logBox, 0, 3);
    }

    private void PrepareInstaller()
    {
        try
        {
            tempRoot = Path.Combine(Path.GetTempPath(), "BonecoRunWindowsInstaller-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempRoot);
            ExtractPayload(tempRoot);

            var termsPath = Path.Combine(tempRoot, "TERMOS-PT-BR.txt");
            if (!File.Exists(termsPath))
            {
                throw new FileNotFoundException("Arquivo de termos nao encontrado.", termsPath);
            }

            termsBox.Text = File.ReadAllText(termsPath, Encoding.UTF8);
            installPathLabel.Text = "Destino: " + InstallDirectory();
            SetStatus("Pronto para instalar.", 8);
            installButton.Enabled = acceptTerms.Checked;
        }
        catch (Exception ex)
        {
            SetStatus("Falha ao preparar instalador.", 0);
            AppendLog(ex.ToString());
            MessageBox.Show(this, ex.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private async Task InstallAsync()
    {
        if (!acceptTerms.Checked)
        {
            MessageBox.Show(this, "Aceite os termos para continuar.", AppName, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        ToggleInstalling(true);
        try
        {
            await Task.Run(InstallCore);
            SetStatus("Instalacao concluida.", 100);
            closeButton.Text = "Fechar";
            MessageBox.Show(this, "BONECO RUN Windows foi instalado com sucesso.", AppName, MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        catch (Exception ex)
        {
            SetStatus("Instalacao falhou.", progressBar.Value);
            AppendLog(ex.ToString());
            MessageBox.Show(this, ex.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            ToggleInstalling(false);
        }
    }

    private void InstallCore()
    {
        var installDir = InstallDirectory();

        SetStatus("Encerrando versoes antigas...", 16);
        var preinstall = Path.Combine(tempRoot, "setup-preinstall.ps1");
        if (File.Exists(preinstall))
        {
            RunProcess(
                "powershell.exe",
                new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", preinstall, "-InstallDir", installDir },
                tempRoot,
                allowFailure: true
            );
        }

        SetStatus("Copiando arquivos...", 42);
        Directory.CreateDirectory(installDir);
        CopyPayload(tempRoot, installDir);

        SetStatus("Registrando servico local, bandeja e atalhos...", 72);
        var installScript = Path.Combine(installDir, "install.ps1");
        if (!File.Exists(installScript))
        {
            throw new FileNotFoundException("install.ps1 nao encontrado apos a copia.", installScript);
        }

        RunProcess(
            "powershell.exe",
            new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", installScript, "-AcceptTerms" },
            installDir,
            allowFailure: false
        );

        SetStatus("Validando instalacao...", 94);
        foreach (var required in new[] { "app\\boneco_windows_daemon.py", "Start-BonecoRunWindows.vbs", "tray\\boneco_tray.ps1" })
        {
            var path = Path.Combine(installDir, required);
            if (!File.Exists(path))
            {
                throw new FileNotFoundException("Arquivo obrigatorio nao encontrado.", path);
            }
        }
    }

    private void RunProcess(string fileName, IEnumerable<string> arguments, string workingDirectory, bool allowFailure)
    {
        using var process = new Process();
        process.StartInfo.FileName = fileName;
        process.StartInfo.WorkingDirectory = workingDirectory;
        process.StartInfo.UseShellExecute = false;
        process.StartInfo.CreateNoWindow = true;
        process.StartInfo.WindowStyle = ProcessWindowStyle.Hidden;
        process.StartInfo.RedirectStandardOutput = true;
        process.StartInfo.RedirectStandardError = true;

        foreach (var argument in arguments)
        {
            process.StartInfo.ArgumentList.Add(argument);
        }

        process.OutputDataReceived += (_, e) => AppendLog(e.Data);
        process.ErrorDataReceived += (_, e) => AppendLog(e.Data);

        if (!process.Start())
        {
            throw new InvalidOperationException("Nao foi possivel iniciar: " + fileName);
        }

        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        process.WaitForExit();

        if (process.ExitCode != 0 && !allowFailure)
        {
            throw new InvalidOperationException($"{fileName} terminou com codigo {process.ExitCode}.");
        }
    }

    private static void ExtractPayload(string destination)
    {
        var root = Path.GetFullPath(destination);
        var assembly = Assembly.GetExecutingAssembly();
        using var payload = assembly.GetManifestResourceStream("payload.zip")
            ?? throw new InvalidOperationException("Payload do instalador nao encontrado.");
        using var archive = new ZipArchive(payload, ZipArchiveMode.Read, leaveOpen: false);

        foreach (var entry in archive.Entries)
        {
            var targetPath = Path.GetFullPath(Path.Combine(destination, entry.FullName));
            if (!targetPath.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Entrada insegura no pacote: " + entry.FullName);
            }

            if (string.IsNullOrEmpty(entry.Name))
            {
                Directory.CreateDirectory(targetPath);
                continue;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(targetPath)!);
            entry.ExtractToFile(targetPath, overwrite: true);
        }
    }

    private static void CopyPayload(string source, string destination)
    {
        var sourceRoot = Path.GetFullPath(source);
        foreach (var directory in Directory.EnumerateDirectories(sourceRoot, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(sourceRoot, directory);
            if (ShouldSkip(relative, isDirectory: true))
            {
                continue;
            }

            Directory.CreateDirectory(Path.Combine(destination, relative));
        }

        foreach (var file in Directory.EnumerateFiles(sourceRoot, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(sourceRoot, file);
            if (ShouldSkip(relative, isDirectory: false))
            {
                continue;
            }

            var target = Path.Combine(destination, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
    }

    private static bool ShouldSkip(string relativePath, bool isDirectory)
    {
        var normalized = relativePath.Replace('\\', '/');
        return normalized.Equals("runtime/state", StringComparison.OrdinalIgnoreCase)
            || normalized.StartsWith("runtime/state/", StringComparison.OrdinalIgnoreCase)
            || normalized.Contains("/__pycache__/", StringComparison.OrdinalIgnoreCase)
            || normalized.EndsWith("/__pycache__", StringComparison.OrdinalIgnoreCase)
            || normalized.EndsWith(".pyc", StringComparison.OrdinalIgnoreCase)
            || normalized.Equals("installer", StringComparison.OrdinalIgnoreCase)
            || normalized.StartsWith("installer/", StringComparison.OrdinalIgnoreCase)
            || normalized.EndsWith(".7z", StringComparison.OrdinalIgnoreCase)
            || normalized.EndsWith("_INSTALLER.exe", StringComparison.OrdinalIgnoreCase);
    }

    private static string InstallDirectory()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (string.IsNullOrWhiteSpace(localAppData))
        {
            localAppData = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                "AppData",
                "Local"
            );
        }

        return Path.Combine(localAppData, "BonecoRunWindows");
    }

    private void SetStatus(string message, int progress)
    {
        SafeUi(() =>
        {
            statusLabel.Text = message;
            progressBar.Value = Math.Max(progressBar.Minimum, Math.Min(progressBar.Maximum, progress));
        });
        AppendLog(message);
    }

    private void AppendLog(string? message)
    {
        if (string.IsNullOrWhiteSpace(message))
        {
            return;
        }

        SafeUi(() =>
        {
            logBox.AppendText(DateTime.Now.ToString("HH:mm:ss") + "  " + message + Environment.NewLine);
        });
    }

    private void ToggleInstalling(bool installing)
    {
        SafeUi(() =>
        {
            installButton.Enabled = !installing && acceptTerms.Checked;
            acceptTerms.Enabled = !installing;
            closeButton.Text = installing ? "Aguarde" : "Cancelar";
            closeButton.Enabled = !installing;
        });
    }

    private void SafeUi(Action action)
    {
        if (InvokeRequired)
        {
            BeginInvoke(action);
            return;
        }

        action();
    }

    private static void TryDeleteDirectory(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
        {
            return;
        }

        try
        {
            Directory.Delete(path, recursive: true);
        }
        catch
        {
        }
    }
}
