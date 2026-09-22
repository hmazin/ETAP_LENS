using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Drawing;
using System.Linq;
using System.Threading;
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
        private readonly BindingList<ReportResult> results = new BindingList<ReportResult>();
        private readonly Label status = Ui.Label("Select result files and one or more templates.");
        private CancellationTokenSource cancellation;
        private bool running;

        public BatchForm(IList<ReportTemplate> catalog, BatchReportService batch, string outputDirectory, bool timestamp,
            Dictionary<string, string> headers = null)
        {
            Ui.Style(this);
            Text = "Batch Reports";
            Size = new Size(1100, 750); MinimumSize = new Size(850, 550);
            foreach (var template in catalog) templates.Items.Add(template);
            var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(12), RowCount = 5, ColumnCount = 2 };
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 55)); root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 45));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize)); root.RowStyles.Add(new RowStyle(SizeType.Percent, 35));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize)); root.RowStyles.Add(new RowStyle(SizeType.Percent, 65)); root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            root.Controls.Add(Ui.Label("ETAP result files"), 0, 0); root.Controls.Add(Ui.Label("Templates — every checked template is attempted for each file"), 1, 0);
            root.Controls.Add(files, 0, 1); root.Controls.Add(templates, 1, 1);
            var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill };
            var add = Ui.Button("Add files…", delegate
            {
                using (var dialog = new OpenFileDialog { Filter = Ui.StudyFilter, Multiselect = true })
                    if (dialog.ShowDialog(this) == DialogResult.OK)
                        foreach (string path in dialog.FileNames)
                            if (!files.Items.Cast<string>().Contains(path, StringComparer.OrdinalIgnoreCase)) files.Items.Add(path);
            });
            var remove = Ui.Button("Remove selected", delegate { foreach (var item in files.SelectedItems.Cast<string>().ToArray()) files.Items.Remove(item); });
            var start = Ui.Button("Generate PDFs", null);
            var cancel = Ui.Button("Cancel after current", delegate { if (cancellation != null) { cancellation.Cancel(); status.Text = "Cancelling after the current report finishes…"; } });
            cancel.Enabled = false;
            actions.Controls.AddRange(new Control[] { add, remove, start, cancel });
            root.Controls.Add(actions, 0, 2); root.SetColumnSpan(actions, 2);
            var grid = new DataGridView { Dock = DockStyle.Fill, ReadOnly = true, AllowUserToAddRows = false, AllowUserToDeleteRows = false,
                AutoGenerateColumns = true, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill, DataSource = results, RowHeadersVisible = false };
            grid.CellDoubleClick += delegate(object sender, DataGridViewCellEventArgs e)
            {
                if (e.RowIndex < 0) return;
                var result = (ReportResult)grid.Rows[e.RowIndex].DataBoundItem;
                try { if (result.Status == "Success") Ui.Open(result.Output); else MessageBox.Show(this, result.Message, "Report diagnostics"); }
                catch (Exception ex) { Ui.Error(this, ex); }
            };
            root.Controls.Add(grid, 0, 3); root.SetColumnSpan(grid, 2);
            root.Controls.Add(status, 0, 4); root.SetColumnSpan(status, 2);
            Controls.Add(root);
            start.Click += async delegate
            {
                if (files.Items.Count == 0 || templates.CheckedItems.Count == 0)
                { Ui.Error(this, new ArgumentException("Select at least one file and one template.")); return; }
                var jobs = (from string path in files.Items from ReportTemplate template in templates.CheckedItems
                    select new ReportJob { SourcePath = path, Template = template, OutputDirectory = outputDirectory, Timestamp = timestamp, Headers = headers }).ToArray();
                running = true; cancellation = new CancellationTokenSource(); results.Clear();
                add.Enabled = remove.Enabled = start.Enabled = files.Enabled = templates.Enabled = false; cancel.Enabled = true;
                status.Text = "Generating " + jobs.Length + " reports…";
                // Progress callbacks run on the UI thread. The SDK itself stays on one STA worker.
                var progress = new Progress<ReportResult>(result => { results.Add(result); status.Text = results.Count + " / " + jobs.Length + " — " + result.Status; });
                try
                {
                    await StaTask.Run(delegate { batch.RunBatch(jobs, cancellation.Token, ((IProgress<ReportResult>)progress).Report); return true; });
                    status.Text = (cancellation.IsCancellationRequested ? "Cancelled. " : "Complete. ") + results.Count(x => x.Status == "Success") + " succeeded; " +
                        results.Count(x => x.Status != "Success") + " failed; " + (jobs.Length - results.Count) + " not run. Double-click a row for its PDF or error.";
                }
                catch (Exception ex) { Ui.Error(this, ex); }
                finally
                {
                    running = false; cancellation.Dispose(); cancellation = null;
                    add.Enabled = remove.Enabled = start.Enabled = files.Enabled = templates.Enabled = true; cancel.Enabled = false;
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
