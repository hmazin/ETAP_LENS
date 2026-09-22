using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Services;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter.UI
{
    public sealed class MainForm : Form
    {
        private readonly TextBox source = new TextBox { Dock = DockStyle.Fill };
        private readonly TextBox output = new TextBox { Dock = DockStyle.Fill };
        private readonly ComboBox template = new ComboBox { Dock = DockStyle.Fill, DropDownStyle = ComboBoxStyle.DropDownList };
        private readonly TextBox study = new TextBox { Dock = DockStyle.Fill, ReadOnly = true, Multiline = true, ScrollBars = ScrollBars.Vertical };
        private readonly CheckBox openPdf = new CheckBox { Text = "Open PDF after generation", AutoSize = true };
        private readonly CheckBox timestamp = new CheckBox { Text = "Include timestamp in filename", AutoSize = true };
        private readonly CheckBox hideSerialNumber = new CheckBox { Text = "Hide serial number", AutoSize = true };
        private readonly ToolStripStatusLabel status = new ToolStripStatusLabel { Text = "Ready" };
        private readonly TableLayoutPanel layout;
        private readonly AppSettings settings;
        private readonly Logger log;
        private readonly EtapDatabaseService databases;
        private readonly CrystalRuntime runtime;
        private readonly CrystalReportService reports;
        private readonly ExportService exports;
        private readonly BatchReportService batch;
        private bool busy;
        private int inspection;
        private int templateScan;

        public MainForm(AppSettings settings, Logger log)
        {
            this.settings = settings; this.log = log;
            databases = new EtapDatabaseService(log); runtime = new CrystalRuntime();
            reports = new CrystalReportService(runtime, log); exports = new ExportService(log);
            batch = new BatchReportService(databases, reports, exports, log);
            Ui.Style(this);
            Text = "ETAP Crystal Report Generator";
            ClientSize = new Size(920, 550); MinimumSize = new Size(800, 540); StartPosition = FormStartPosition.CenterScreen;
            layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(20), ColumnCount = 3, RowCount = 9 };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 155));
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            for (int i = 2; i < 9; i++) layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            AddRow("ETAP Result File", source, Ui.Button("Browse…", async delegate
            {
                using (var dialog = new OpenFileDialog { Filter = Ui.StudyFilter })
                    if (dialog.ShowDialog(this) == DialogResult.OK) { source.Text = dialog.FileName; await Inspect(); }
            }), 0);
            AddRow("Detected Study", study, Ui.Button("Inspect", async delegate { await Inspect(); }), 1);
            AddRow("Crystal Template", template, Ui.Button("Browse .rpt…", delegate
            {
                using (var dialog = new OpenFileDialog { Filter = "Crystal Reports (*.rpt)|*.rpt" })
                    if (dialog.ShowDialog(this) == DialogResult.OK)
                        try { var item = TemplateCatalog.Load(dialog.FileName); template.Items.Add(item); template.SelectedItem = item; }
                        catch (Exception ex) { Ui.Error(this, ex); }
            }), 2);
            AddRow("Output Folder", output, Ui.Button("Browse…", delegate { output.Text = Ui.Folder(this, output.Text); }), 3);
            output.Text = settings.OutputDirectory; openPdf.Checked = settings.OpenPdf; timestamp.Checked = settings.Timestamp;
            hideSerialNumber.Checked = settings.HideSerialNumber;
            var options = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, Padding = new Padding(0, 8, 0, 8) };
            options.Controls.Add(openPdf); options.Controls.Add(timestamp); options.Controls.Add(hideSerialNumber);
            layout.Controls.Add(options, 1, 4); layout.SetColumnSpan(options, 2);
            var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill };
            actions.Controls.Add(Ui.Button("Preview Report", Preview));
            actions.Controls.Add(Ui.Button("Generate PDF", Generate));
            actions.Controls.Add(Ui.Button("Batch Reports…", delegate
            {
                try
                {
                    SaveSettings();
                    using (var form = new BatchForm(template.Items.Cast<ReportTemplate>().ToList(), batch, output.Text, timestamp.Checked, Headers())) form.ShowDialog(this);
                }
                catch (Exception ex) { Ui.Error(this, ex); }
            }));
            layout.Controls.Add(actions, 1, 5); layout.SetColumnSpan(actions, 2);
            var tools = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill };
            tools.Controls.Add(Ui.Button("Template settings…", delegate { using (var form = new SettingsForm(settings)) if (form.ShowDialog(this) == DialogResult.OK) ReloadTemplates(); }));
            tools.Controls.Add(Ui.Button("Reload templates", delegate { ReloadTemplates(); }));
            tools.Controls.Add(Ui.Button("Open logs", delegate { try { Ui.Open(log.DirectoryPath); } catch (Exception ex) { Ui.Error(this, ex); } }));
            layout.Controls.Add(tools, 1, 6); layout.SetColumnSpan(tools, 2);
            var note = Ui.Label("Original result files are read without modification. Report layout comes from your Crystal template.");
            layout.Controls.Add(note, 0, 7); layout.SetColumnSpan(note, 3);
            var version = Ui.Label("");
            try { runtime.CheckExecution(); version.Text = "Crystal runtime available (" + (Environment.Is64BitProcess ? "64-bit" : "32-bit") + ")."; }
            catch (Exception ex) { version.Text = "Crystal runtime not available. Database inspection still works. See the setup guide."; log.Write("runtime.unavailable", new { error = ex.Message }); }
            layout.Controls.Add(version, 0, 8); layout.SetColumnSpan(version, 3);
            var strip = new StatusStrip(); strip.Items.Add(status);
            Controls.Add(layout); Controls.Add(strip);
            source.TextChanged += delegate { inspection++; study.Text = "Click Inspect to validate this file."; };
            FormClosing += delegate(object sender, FormClosingEventArgs e)
            { if (busy) { e.Cancel = true; status.Text = "Wait for the current operation to finish."; } };
            Shown += delegate { ReloadTemplates(); };
        }

        private void AddRow(string label, Control value, Control action, int row)
        { layout.Controls.Add(Ui.Label(label), 0, row); layout.Controls.Add(value, 1, row); layout.Controls.Add(action, 2, row); }

        private async void ReloadTemplates()
        {
            int request = ++templateScan;
            string directory = settings.TemplateDirectory;
            try
            {
                string selected = template.SelectedItem == null ? null : ((ReportTemplate)template.SelectedItem).Path;
                template.Enabled = false;
                template.Items.Clear();
                status.Text = "Scanning template folders…";
                var warnings = new List<string>();
                var catalog = await Task.Run(() => TemplateCatalog.Scan(directory, warnings.Add));
                if (IsDisposed || request != templateScan) return;
                template.Items.AddRange(catalog.ToArray());
                if (catalog.Count > 0) template.SelectedItem = catalog.FirstOrDefault(x => x.Path == selected) ?? catalog[0];
                status.Text = catalog.Count + " templates found." + (catalog.Count == 0 ? " Add .rpt files using Template settings or Browse .rpt." : "");
                if (warnings.Count > 0) { study.Text = string.Join(Environment.NewLine, warnings); log.Write("templates.warning", warnings); }
            }
            catch (Exception ex) { if (!IsDisposed && request == templateScan) Ui.Error(this, ex); }
            finally { if (!IsDisposed && request == templateScan) template.Enabled = true; }
        }

        private async Task Inspect()
        {
            string path = source.Text.Trim(); int request = ++inspection;
            status.Text = "Inspecting database…";
            try
            {
                var info = await Task.Run(delegate { using (var db = databases.Open(path)) return db.Info; });
                if (IsDisposed || request != inspection) return;
                study.Text = info.StudyName + Environment.NewLine + "Valid SQLite database — " + info.Tables.Count + " tables" + Environment.NewLine +
                    info.DetectionNote + Environment.NewLine + Environment.NewLine + "Tables: " + string.Join(", ", info.Tables);
                status.Text = "Database validated.";
            }
            catch (Exception ex)
            {
                if (IsDisposed || request != inspection) return;
                study.Text = "Validation failed: " + ex.Message; status.Text = "Invalid or unavailable result file.";
            }
        }

        private void SaveSettings()
        {
            if (string.IsNullOrWhiteSpace(output.Text)) throw new ArgumentException("Select an output folder.");
            settings.OutputDirectory = Path.GetFullPath(output.Text.Trim());
            settings.OpenPdf = openPdf.Checked; settings.Timestamp = timestamp.Checked;
            settings.HideSerialNumber = hideSerialNumber.Checked; settings.Save();
        }

        private ReportJob Job()
        {
            SaveSettings();
            if (string.IsNullOrWhiteSpace(source.Text)) throw new ArgumentException("Select an ETAP result file.");
            var selected = template.SelectedItem as ReportTemplate;
            if (selected == null) throw new ArgumentException("Select a Crystal .rpt template.");
            return new ReportJob { SourcePath = Path.GetFullPath(source.Text.Trim()), Template = TemplateCatalog.Load(selected.Path, selected.Name),
                OutputDirectory = settings.OutputDirectory, Timestamp = timestamp.Checked, Headers = Headers() };
        }

        private Dictionary<string, string> Headers()
        { return hideSerialNumber.Checked ? new Dictionary<string, string> { { "sn", null } } : null; }

        private async void Generate(object sender, EventArgs args)
        {
            try
            {
                ReportJob job = Job();
                SetBusy(true, "Generating PDF…");
                ReportResult result = await StaTask.Run(() => batch.Run(job));
                status.Text = result.Status == "Success" ? "Saved: " + result.Output : "Report failed.";
                if (result.Status != "Success") throw new InvalidOperationException(result.Message);
                if (openPdf.Checked) Ui.Open(result.Output);
            }
            catch (Exception ex) { Ui.Error(this, ex); }
            finally { SetBusy(false, null); }
        }

        private void Preview(object sender, EventArgs args)
        {
            try
            {
                ReportJob job = Job();
                SetBusy(true, "Opening Crystal preview…");
                using (var form = new ReportPreviewForm(runtime, delegate
                {
                    using (var db = databases.Open(job.SourcePath))
                    {
                        job.Template = TemplateCatalog.Load(job.Template.Path, job.Template.Name);
                        return new PreviewDocument { Session = reports.Prepare(db, job.Template, job.Headers), Study = db.Info };
                    }
                }, exports, job, openPdf.Checked)) form.ShowDialog(this);
                status.Text = "Ready";
            }
            catch (Exception ex) { Ui.Error(this, ex); }
            finally { SetBusy(false, null); }
        }

        private void SetBusy(bool value, string message)
        { busy = value; layout.Enabled = !value; Cursor = value ? Cursors.WaitCursor : Cursors.Default; if (message != null) status.Text = message; }
    }
}
