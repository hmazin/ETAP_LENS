using System;
using System.Collections.Generic;
using System.Data;

namespace EtapCrystalReporter.Models
{
    public sealed class EtapStudyInfo
    {
        public string SourcePath { get; set; }
        public string SourceSha256 { get; set; }
        public int? StudyType { get; set; }
        public string StudyName { get; set; }
        public string DetectionNote { get; set; }
        public List<string> Tables { get; set; }
    }

    public sealed class TemplateOptions
    {
        // Empty means no study restriction. The schema is still checked by CrystalReportService.
        public int[] StudyTypes { get; set; }
        public Dictionary<string, string> TableMappings { get; set; }
        // Keyed "TableAlias/CrystalFieldName" (optionally "Subreport.rpt/TableAlias/CrystalFieldName"),
        // for when a template's saved field name no longer matches the source column - e.g. an ETAP
        // schema rename. Unmapped fields still resolve by name as before.
        public Dictionary<string, string> FieldMappings { get; set; }
        // Same key convention as FieldMappings. A field listed here binds as an all-blank column
        // (instead of failing the report) when genuinely absent from the source - e.g. a template's
        // reserved/placeholder field never populated in this ETAP schema version. Still checked
        // against FieldMappings first, so a real rename takes precedence over treating it as blank.
        public string[] OptionalFields { get; set; }
        public Dictionary<string, object> Parameters { get; set; }
        public TemplateOptions()
        {
            StudyTypes = new int[0];
            TableMappings = new Dictionary<string, string>();
            FieldMappings = new Dictionary<string, string>();
            OptionalFields = new string[0];
            Parameters = new Dictionary<string, object>();
        }
    }

    public sealed class ReportTemplate
    {
        public string Path { get; set; }
        public string Name { get; set; }
        public TemplateOptions Options { get; set; }
        public override string ToString() { return Name; }
    }

    public sealed class ReportJob
    {
        public string SourcePath { get; set; }
        public ReportTemplate Template { get; set; }
        public string OutputDirectory { get; set; }
        public bool Timestamp { get; set; }
        // A field mapped to null hides both its label and value on the report
        // (see ReportHeaders.ApplyPresentation). Null means no header overrides.
        public Dictionary<string, string> Headers { get; set; }
    }

    public sealed class ReportResult
    {
        public string File { get; set; }
        public string Study { get; set; }
        public string Template { get; set; }
        public string Status { get; set; }
        public string Output { get; set; }
        public string Message { get; set; }
    }

    public interface IReportSession : IDisposable
    {
        object Document { get; }
        void ExportPdf(string path);
    }

    public interface IReportService
    {
        IReportSession Prepare(DatabaseSnapshot database, ReportTemplate template, IDictionary<string, string> headers = null);
    }

    public sealed class DatabaseSnapshot : IDisposable
    {
        private readonly Action cleanup;
        private readonly Func<string, DataTable> readTable;
        public EtapStudyInfo Info { get; private set; }
        public DatabaseSnapshot(EtapStudyInfo info, Func<string, DataTable> readTable, Action cleanup)
        { Info = info; this.readTable = readTable; this.cleanup = cleanup; }
        public DataTable ReadTable(string name) { return readTable(name); }
        public void Dispose() { cleanup(); }
    }
}
