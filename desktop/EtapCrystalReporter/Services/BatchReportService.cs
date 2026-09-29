using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Web.Script.Serialization;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter.Services
{
    public sealed class BatchReportService
    {
        private readonly EtapDatabaseService databases;
        private readonly IReportService reports;
        private readonly ExportService exports;
        private readonly Logger log;
        public BatchReportService(EtapDatabaseService databases, IReportService reports, ExportService exports, Logger log)
        { this.databases = databases; this.reports = reports; this.exports = exports; this.log = log; }

        // Used to auto-match files to compatible templates before a batch runs,
        // so a mixed folder of result files doesn't need every template tried
        // against every file. Null (unknown/unreadable) matches nothing specific;
        // callers still pair it with templates that declare no StudyTypes restriction.
        public int? DetectStudyType(string path)
        {
            try { using (var database = databases.Open(path)) return database.Info.StudyType; }
            catch { return null; }
        }

        // Pairs each file with only the templates whose StudyTypes include its detected
        // study type (a template declaring none always applies). A file whose type can't
        // be detected is paired with every template, so it still fails per-template with
        // a clear reason instead of silently producing no jobs at all.
        public ReportJob[] MatchJobs(IEnumerable<string> paths, IEnumerable<ReportTemplate> templates,
            string outputDirectory, bool timestamp, Dictionary<string, string> headers = null)
        {
            var candidates = templates as IList<ReportTemplate> ?? templates.ToList();
            var jobs = new List<ReportJob>();
            foreach (string path in paths)
            {
                int? type = DetectStudyType(path);
                var matching = type.HasValue
                    ? candidates.Where(t => t.Options.StudyTypes.Length == 0 || t.Options.StudyTypes.Contains(type.Value))
                    : candidates;
                foreach (var template in matching)
                    jobs.Add(new ReportJob { SourcePath = path, Template = template, OutputDirectory = outputDirectory, Timestamp = timestamp, Headers = headers });
            }
            return jobs.ToArray();
        }

        public ReportResult Run(ReportJob job)
        {
            var result = new ReportResult { File = job.SourcePath, Template = job.Template.Name, Study = "Unknown", Status = "Failed", Output = "" };
            try
            {
                using (var database = databases.Open(job.SourcePath))
                {
                    result.Study = database.Info.StudyName;
                    // Reload sidecar at execution time, so UI selections cannot keep stale settings.
                    job.Template = TemplateCatalog.Load(job.Template.Path, job.Template.Name);
                    using (var report = reports.Prepare(database, job.Template, job.Headers))
                        result.Output = exports.Export(report, job, database.Info);
                    result.Status = "Success";
                    result.Message = database.Info.DetectionNote;
                }
            }
            catch (Exception ex)
            {
                result.Status = "Failed";
                result.Message = ex.Message;
                try { log.Write("report.failed", new { source = job.SourcePath, template = job.Template.Path, error = ex.ToString() }); }
                catch (Exception logError) { result.Message += " Diagnostic logging also failed: " + logError.Message; }
            }
            return result;
        }

        public void RunBatch(IEnumerable<ReportJob> jobs, CancellationToken cancellation, Action<ReportResult> progress, Action<ReportJob> starting = null)
        {
            foreach (var job in jobs)
            {
                if (cancellation.IsCancellationRequested) break;
                if (starting != null) starting(job);
                progress(Run(job));
            }
        }

        // Runs one job in a separate child process (workerExecutablePath --run-job ...,
        // handled by Program.RunJob) instead of in-process. Some studies crash the native
        // Crystal engine during export with no catchable .NET exception - binding
        // completes, PDF export never returns, the whole process just dies. Isolating each
        // report this way means that crash only kills its own child; the caller and the
        // rest of the batch are unaffected. The cost is a fresh Crystal engine cold start
        // per report (tens of seconds) instead of once for the whole batch.
        public ReportResult RunIsolated(ReportJob job, string workerExecutablePath, TimeSpan timeout)
        {
            var result = new ReportResult { File = job.SourcePath, Template = job.Template.Name, Study = "Unknown", Status = "Failed", Output = "" };
            string requestPath = Path.GetTempFileName();
            string resultPath = Path.GetTempFileName();
            try
            {
                var request = new WorkerRequest { SourcePath = job.SourcePath, TemplatePath = job.Template.Path,
                    OutputDirectory = job.OutputDirectory, Timestamp = job.Timestamp, Headers = job.Headers };
                File.WriteAllText(requestPath, new JavaScriptSerializer().Serialize(request));
                var info = new ProcessStartInfo(workerExecutablePath, "--run-job \"" + requestPath + "\" \"" + resultPath + "\"")
                { UseShellExecute = false, CreateNoWindow = true };
                using (var process = Process.Start(info))
                {
                    bool exited = process.WaitForExit((int)timeout.TotalMilliseconds);
                    if (!exited)
                    {
                        try { process.Kill(); } catch { }
                        result.Message = "The report generator did not finish within " + (int)timeout.TotalMinutes + " minute(s) and was stopped.";
                        log.Write("report.worker_timeout", new { source = job.SourcePath, template = job.Template.Path });
                        return result;
                    }
                    if (!File.Exists(resultPath) || new FileInfo(resultPath).Length == 0)
                    {
                        result.Message = "The report generator process stopped unexpectedly while rendering this report (exit code " + process.ExitCode +
                            "). This is a native Crystal Reports engine failure for this specific study, not an application error.";
                        log.Write("report.worker_crashed", new { source = job.SourcePath, template = job.Template.Path, exitCode = process.ExitCode });
                        return result;
                    }
                    return new JavaScriptSerializer().Deserialize<ReportResult>(File.ReadAllText(resultPath));
                }
            }
            catch (Exception ex)
            {
                result.Message = "Could not start the isolated report worker: " + ex.Message;
                return result;
            }
            finally
            {
                try { if (File.Exists(requestPath)) File.Delete(requestPath); } catch { }
                try { if (File.Exists(resultPath)) File.Delete(resultPath); } catch { }
            }
        }

        public void RunBatchIsolated(IEnumerable<ReportJob> jobs, CancellationToken cancellation, Action<ReportResult> progress,
            string workerExecutablePath, TimeSpan timeout, Action<ReportJob> starting = null)
        {
            foreach (var job in jobs)
            {
                if (cancellation.IsCancellationRequested) break;
                if (starting != null) starting(job);
                progress(RunIsolated(job, workerExecutablePath, timeout));
            }
        }
    }
}
