using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Services;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter.UI
{
    public sealed class BatchForm : Form
    {
        private readonly ListBox files = new ListBox { Dock = DockStyle.Fill, SelectionMode = SelectionMode.MultiExtended, HorizontalScrollbar = true };
        private readonly CheckedListBox templates = new CheckedListBox { Dock = DockStyle.Fill, CheckOnClick = true, HorizontalScrollbar = true };
        private readonly TextBox folderPath = new TextBox { Dock = DockStyle.Fill };
        private readonly CheckBox hideSerialNumber = new CheckBox { Text = "Hide serial number", AutoSize = true };
        private readonly ProgressBar progressBar = new ProgressBar { Dock = DockStyle.Fill, Style = ProgressBarStyle.Continuous };
        private readonly BindingList<ReportResult> results = new BindingList<ReportResult>();
        private readonly Label status = Ui.Label("Select result files and one or more templates.");
        private CancellationTokenSource cancellation;
        private bool running;

        private Dictionary<string, string> Headers()
        { return hideSerialNumber.Checked ? new Dictionary<string, string> { { "sn", null } } : null; }

        public BatchForm(IList<ReportTemplate> catalog, BatchReportService batch, string outputDirectory, bool timestamp,
            bool hideSerialNumberDefault = false, string preferredKind = "summary")
        {
            hideSerialNumber.Checked = hideSerialNumberDefault;
            Ui.Style(this);
            Text = "Batch Reports";
            Size = new Size(1100, 750); MinimumSize = new Size(850, 550);
            // Default to just the one overview report per family (matching MainForm's
            // "Prefer Summary/Complete" setting), not every specialized variant - the
            // user can still check more manually.
            foreach (var template in catalog)
                templates.Items.Add(template, TemplateCatalog.MatchesKind(template, preferredKind));
            var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(12), RowCount = 8, ColumnCount = 2 };
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 55)); root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 45));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize)); root.RowStyles.Add(new RowStyle(SizeType.AutoSize)); root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 35)); root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 65)); root.RowStyles.Add(new RowStyle(SizeType.AutoSize)); root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            Action<string> addFolderPath = delegate(string folder)
            {
                folder = (folder ?? "").Trim().Trim('"');
                if (folder.Length == 0) return;
                if (!Directory.Exists(folder)) { status.Text = "That folder doesn't exist: " + folder; return; }
                var found = Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories)
                    .Where(p => FileValidator.StudyExtensions.Contains(Path.GetExtension(p), StringComparer.OrdinalIgnoreCase))
                    .OrderBy(p => p, StringComparer.OrdinalIgnoreCase).ToArray();
                foreach (string path in found)
                    if (!files.Items.Cast<string>().Contains(path, StringComparer.OrdinalIgnoreCase)) files.Items.Add(path);
                status.Text = found.Length == 0 ? "No .SA1S/.SA2S/.UL1S files found under that folder." :
                    "Added " + found.Length + " result file(s) from " + folder + ".";
            };
            var pathRow = new TableLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, ColumnCount = 2 };
            pathRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); pathRow.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            var addPasted = Ui.Button("Add", delegate { addFolderPath(folderPath.Text); folderPath.Clear(); });
            folderPath.KeyDown += delegate(object sender, KeyEventArgs e) { if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; addPasted.PerformClick(); } };
            pathRow.Controls.Add(folderPath, 0, 0); pathRow.Controls.Add(addPasted, 1, 0);
            var pathLabel = Ui.Label("Paste a folder path (searched recursively):");
            root.Controls.Add(pathLabel, 0, 0); root.SetColumnSpan(pathLabel, 2);
            root.Controls.Add(pathRow, 0, 1); root.SetColumnSpan(pathRow, 2);
            root.Controls.Add(Ui.Label("ETAP result files"), 0, 2);
            root.Controls.Add(Ui.Label("Templates — only the ones matching each file's detected study type are used"), 1, 2);
            root.Controls.Add(files, 0, 3); root.Controls.Add(templates, 1, 3);
            var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill };
            var add = Ui.Button("Add files…", delegate
            {
                using (var dialog = new OpenFileDialog { Filter = Ui.StudyFilter, Multiselect = true })
                    if (dialog.ShowDialog(this) == DialogResult.OK)
                        foreach (string path in dialog.FileNames)
                            if (!files.Items.Cast<string>().Contains(path, StringComparer.OrdinalIgnoreCase)) files.Items.Add(path);
            });
            var addFolder = Ui.Button("Browse for folder…", delegate { addFolderPath(Ui.Folder(this, "")); });
            var remove = Ui.Button("Remove selected", delegate { foreach (var item in files.SelectedItems.Cast<string>().ToArray()) files.Items.Remove(item); });
            var start = Ui.Button("Generate PDFs", null);
            var cancel = Ui.Button("Cancel after current", delegate { if (cancellation != null) { cancellation.Cancel(); status.Text = "Cancelling after the current report finishes…"; } });
            cancel.Enabled = false;
            actions.Controls.AddRange(new Control[] { add, addFolder, remove, hideSerialNumber, start, cancel });
            root.Controls.Add(actions, 0, 4); root.SetColumnSpan(actions, 2);
            var grid = new DataGridView { Dock = DockStyle.Fill, ReadOnly = true, AllowUserToAddRows = false, AllowUserToDeleteRows = false,
                AutoGenerateColumns = true, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill, DataSource = results, RowHeadersVisible = false };
            grid.CellDoubleClick += delegate(object sender, DataGridViewCellEventArgs e)
            {
                if (e.RowIndex < 0) return;
                var result = (ReportResult)grid.Rows[e.RowIndex].DataBoundItem;
                try { if (result.Status == "Success") Ui.Open(result.Output); else MessageBox.Show(this, result.Message, "Report diagnostics"); }
                catch (Exception ex) { Ui.Error(this, ex); }
            };
            root.Controls.Add(grid, 0, 5); root.SetColumnSpan(grid, 2);
            root.Controls.Add(progressBar, 0, 6); root.SetColumnSpan(progressBar, 2);
            root.Controls.Add(status, 0, 7); root.SetColumnSpan(status, 2);
            Controls.Add(root);
            start.Click += async delegate
            {
                if (files.Items.Count == 0 || templates.CheckedItems.Count == 0)
                { Ui.Error(this, new ArgumentException("Select at least one file and one template.")); return; }
                running = true; cancellation = new CancellationTokenSource(); results.Clear();
                add.Enabled = addFolder.Enabled = addPasted.Enabled = folderPath.Enabled = remove.Enabled = start.Enabled = files.Enabled = templates.Enabled = hideSerialNumber.Enabled = false; cancel.Enabled = true;
                status.Text = "Matching " + files.Items.Count + " file(s) to compatible templates…";
                var paths = files.Items.Cast<string>().ToArray();
                var checkedTemplates = templates.CheckedItems.Cast<ReportTemplate>().ToArray();
                var headers = Headers();
                var jobs = await Task.Run(() => batch.MatchJobs(paths, checkedTemplates, outputDirectory, timestamp, headers));
                if (jobs.Length == 0)
                {
                    status.Text = "No checked template matches any selected file's detected study type.";
                    running = false; add.Enabled = addFolder.Enabled = addPasted.Enabled = folderPath.Enabled = remove.Enabled = start.Enabled = files.Enabled = templates.Enabled = hideSerialNumber.Enabled = true; cancel.Enabled = false;
                    return;
                }
                progressBar.Minimum = 0; progressBar.Maximum = jobs.Length; progressBar.Value = 0;
                status.Text = "Generating " + jobs.Length + " reports…";
                // Both callbacks run on the UI thread (via Progress<T>); the SDK itself stays on one STA worker.
                var starting = new Progress<ReportJob>(job => status.Text = "Processing " + (progressBar.Value + 1) + " / " + jobs.Length +
                    ": " + Path.GetFileName(job.SourcePath) + " → " + job.Template.Name + " …");
                var progress = new Progress<ReportResult>(result =>
                {
                    results.Add(result); progressBar.Value = results.Count;
                    status.Text = results.Count + " / " + jobs.Length + " — " + result.Status + ": " + Path.GetFileName(result.File) + " → " + result.Template;
                });
                try
                {
                    await StaTask.Run(delegate
                    {
                        // Isolated, not in-process: some studies crash the native Crystal
                        // engine during export with nothing catchable - binding completes,
                        // export never returns. Running each report in its own child
                        // process means that crash only loses that one report.
                        batch.RunBatchIsolated(jobs, cancellation.Token, ((IProgress<ReportResult>)progress).Report,
                            Application.ExecutablePath, TimeSpan.FromMinutes(5), ((IProgress<ReportJob>)starting).Report);
                        return true;
                    });
                    status.Text = (cancellation.IsCancellationRequested ? "Cancelled. " : "Complete. ") + results.Count(x => x.Status == "Success") + " succeeded; " +
                        results.Count(x => x.Status != "Success") + " failed; " + (jobs.Length - results.Count) + " not run. Double-click a row for its PDF or error.";
                }
                catch (Exception ex) { Ui.Error(this, ex); }
                finally
                {
                    running = false; cancellation.Dispose(); cancellation = null;
                    add.Enabled = addFolder.Enabled = addPasted.Enabled = folderPath.Enabled = remove.Enabled = start.Enabled = files.Enabled = templates.Enabled = hideSerialNumber.Enabled = true; cancel.Enabled = false;
                }
            };
            FormClosing += delegate(object sender, FormClosingEventArgs e)
            {
                if (!running) return;
                e.Cancel = true; cancellation.Cancel(); status.Text = "Waiting for the current report to finish before closing. You can close this window when it finishes.";
            };
        }
    }
}
