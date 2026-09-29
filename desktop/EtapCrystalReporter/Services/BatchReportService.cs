using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
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

        public void RunBatch(IEnumerable<ReportJob> jobs, CancellationToken cancellation, Action<ReportResult> progress)
        {
            foreach (var job in jobs)
            {
                if (cancellation.IsCancellationRequested) break;
                progress(Run(job));
            }
        }
    }
}
