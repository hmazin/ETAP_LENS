using System;
using System.Collections.Generic;
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
                    using (var report = reports.Prepare(database, job.Template))
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
