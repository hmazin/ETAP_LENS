using System;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Services;
using EtapCrystalReporter.UI;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            // Headless single-report mode: BatchReportService.RunIsolated launches this
            // exe with these arguments so one report's native Crystal call runs in its
            // own process. A crash there (see docs/desktop crash notes) only kills this
            // child - the GUI process and the rest of the batch are unaffected.
            if (args.Length >= 3 && args[0] == "--run-job")
                return RunJob(args[1], args[2]);

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try
            {
                var log = new Logger(Path.Combine(AppSettings.DataDirectory, "Logs"));
                log.Write("application.started", new { version = "0.1.0", bitness = IntPtr.Size * 8 });
                AppSettings settings;
                try { settings = AppSettings.Load(); }
                catch (Exception ex)
                {
                    log.Write("settings.invalid", new { error = ex.Message });
                    MessageBox.Show("Settings could not be loaded; defaults will be used. " + ex.Message, "ETAP Crystal Report Generator");
                    settings = new AppSettings();
                }
                Application.Run(new MainForm(settings, log));
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "ETAP Crystal Report Generator", MessageBoxButtons.OK, MessageBoxIcon.Error); }
            return 0;
        }

        private static int RunJob(string requestPath, string resultPath)
        {
            var log = new Logger(Path.Combine(AppSettings.DataDirectory, "Logs"));
            var json = new JavaScriptSerializer();
            ReportResult result;
            try
            {
                var request = json.Deserialize<WorkerRequest>(File.ReadAllText(requestPath));
                var runtime = new CrystalRuntime();
                var batch = new BatchReportService(new EtapDatabaseService(log), new CrystalReportService(runtime, log), new ExportService(log), log);
                var job = new ReportJob { SourcePath = request.SourcePath, Template = TemplateCatalog.Load(request.TemplatePath),
                    OutputDirectory = request.OutputDirectory, Timestamp = request.Timestamp, Headers = request.Headers };
                result = batch.Run(job);
            }
            catch (Exception ex) { result = new ReportResult { Status = "Failed", Message = "Report worker error: " + ex.Message }; }
            File.WriteAllText(resultPath, json.Serialize(result));
            return result.Status == "Success" ? 0 : 1;
        }
    }
}
