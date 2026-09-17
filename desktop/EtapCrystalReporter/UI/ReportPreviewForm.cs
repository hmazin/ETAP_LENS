using System;
using System.Drawing;
using System.Reflection;
using System.Windows.Forms;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Services;

namespace EtapCrystalReporter.UI
{
    public sealed class PreviewDocument : IDisposable
    {
        public IReportSession Session { get; set; }
        public EtapStudyInfo Study { get; set; }
        public void Dispose() { if (Session != null) Session.Dispose(); }
    }

    public sealed class ReportPreviewForm : Form
    {
        private readonly Control viewer;
        private readonly Func<PreviewDocument> prepare;
        private PreviewDocument current;
        private readonly Label status = Ui.Label("Loading report…");

        public ReportPreviewForm(CrystalRuntime runtime, Func<PreviewDocument> prepare, ExportService exports, ReportJob job, bool openPdf)
        {
            this.prepare = prepare;
            Ui.Style(this);
            Text = "Report Preview — " + job.Template.Name;
            Size = new Size(1100, 800);
            MinimumSize = new Size(700, 450);
            viewer = runtime.CreateViewer();
            viewer.Dock = DockStyle.Fill;
            dynamic native = viewer;
            native.ShowExportButton = false;
            native.ShowRefreshButton = true;
            // Crystal's default refresh can reconnect to the template's stale database.
            // Handle it by rebuilding from a fresh protected snapshot instead.
            EventInfo refreshEvent = viewer.GetType().GetEvent("ReportRefresh");
            if (refreshEvent == null) throw new InvalidOperationException("Installed Crystal viewer lacks ReportRefresh support.");
            refreshEvent.AddEventHandler(viewer, Delegate.CreateDelegate(refreshEvent.EventHandlerType, this,
                GetType().GetMethod("NativeRefresh", BindingFlags.Instance | BindingFlags.NonPublic)));
            var bar = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(8) };
            bar.Controls.Add(Ui.Button("Export PDF", delegate
            {
                if (current == null) return;
                try
                {
                    string path = exports.Export(current.Session, job, current.Study);
                    status.Text = "Saved: " + path;
                    if (openPdf) Ui.Open(path);
                }
                catch (Exception ex) { Ui.Error(this, ex); }
            }));
            bar.Controls.Add(status);
            Controls.Add(viewer); Controls.Add(bar);
            Shown += delegate { Reload(); };
            FormClosed += delegate { native.ReportSource = null; if (current != null) { current.Dispose(); current = null; } };
        }

        private void NativeRefresh(object sender, EventArgs args)
        {
            dynamic nativeArgs = args;
            nativeArgs.Handled = true;
            // Leave the viewer event stack before swapping its ReportDocument.
            BeginInvoke(new Action(Reload));
        }

        private void Reload()
        {
            Cursor = Cursors.WaitCursor;
            status.Text = "Loading selected study and template…";
            status.Refresh();
            PreviewDocument replacement = null;
            try
            {
                replacement = prepare();
                ((dynamic)viewer).ReportSource = replacement.Session.Document;
                var previous = current;
                current = replacement; replacement = null;
                if (previous != null) previous.Dispose();
                status.Text = current.Study.StudyName + " — " + System.IO.Path.GetFileName(current.Study.SourcePath);
            }
            catch (Exception ex) { status.Text = "Report could not be loaded."; Ui.Error(this, ex); }
            finally { if (replacement != null) replacement.Dispose(); Cursor = Cursors.Default; }
        }
    }
}
