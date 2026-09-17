using System;
using System.Collections.Generic;
using System.Data;
using System.IO;
using System.Linq;
using System.Threading;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter.Services
{
    public sealed class CrystalReportService : IReportService
    {
        private readonly CrystalRuntime runtime;
        private readonly Logger log;
        public CrystalReportService(CrystalRuntime runtime, Logger log) { this.runtime = runtime; this.log = log; }

        public IReportSession Prepare(DatabaseSnapshot database, ReportTemplate template)
        {
            if (Thread.CurrentThread.GetApartmentState() != ApartmentState.STA)
                throw new InvalidOperationException("Crystal Reports must run on an STA thread.");
            TemplateCatalog.ValidateStudy(template, database.Info);
            dynamic document = runtime.NewDocument();
            var data = new List<DataTable>();
            try
            {
                document.Load(template.Path, runtime.EnumValue("OpenReportMethod", "OpenReportByTempCopy"));
                document.ReportOptions.EnableSaveDataWithReport = false;
                // Bind every main/subreport table. Never refresh against the template's original connection.
                int bound = Bind(document, "", database, template, data);
                foreach (dynamic subreport in document.Subreports)
                    bound += Bind(subreport, (string)subreport.Name, database, template, data);
                if (bound == 0) throw new InvalidDataException("The template has no database tables; it cannot be verified against the selected study.");
                ApplyParameters(document, template);
                log.Write("report.bound", new { source = database.Info.SourcePath, sourceSha256 = database.Info.SourceSha256,
                    template = template.Path, templateSha256 = FileValidator.Sha256(template.Path), boundTables = bound, runtime = runtime.Version });
                return new CrystalSession(document, data, runtime);
            }
            catch
            {
                try { document.Close(); } finally { document.Dispose(); foreach (var table in data) table.Dispose(); }
                throw;
            }
        }

        private int Bind(dynamic report, string scope, DatabaseSnapshot database, ReportTemplate template, List<DataTable> retained)
        {
            int count = 0;
            foreach (dynamic table in report.Database.Tables)
            {
                string alias = (string)table.Name;
                string actual = TableBinding.Resolve(alias, (string)table.Location, scope, template, database.Info.Tables);
                var fields = new Dictionary<string, Type>(StringComparer.OrdinalIgnoreCase);
                foreach (dynamic field in table.Fields)
                    fields.Add((string)field.Name, TableBinding.FieldType(field.ValueType.ToString()));
                if (fields.Count == 0) throw new InvalidDataException("Crystal table '" + alias + "' has no fields.");
                using (DataTable source = database.ReadTable(actual))
                {
                    DataTable shaped = TableBinding.Shape(source, alias, fields);
                    retained.Add(shaped);
                    table.SetDataSource(shaped);
                    log.Write("report.table", new { scope, alias, sourceTable = actual, rows = source.Rows.Count, columns = fields.Keys.ToArray() });
                }
                count++;
            }
            return count;
        }

        private static void ApplyParameters(dynamic document, ReportTemplate template)
        {
            // The optional sidecar supplies typed JSON parameter values for unattended export.
            // Main parameters: Name. Subreport parameters: SubreportName/Name.
            foreach (var pair in template.Options.Parameters)
            {
                int slash = pair.Key.IndexOf('/');
                dynamic target = document;
                string name = pair.Key;
                if (slash >= 0)
                {
                    target = document.OpenSubreport(pair.Key.Substring(0, slash));
                    name = pair.Key.Substring(slash + 1);
                }
                dynamic definition = target.DataDefinition.ParameterFields[name];
                Type type = TableBinding.FieldType(definition.ValueType.ToString());
                object value = TableBinding.ConvertValue(pair.Value, type);
                if (slash >= 0) document.SetParameterValue(name, value, pair.Key.Substring(0, slash));
                else document.SetParameterValue(name, value);
            }
        }

        private sealed class CrystalSession : IReportSession
        {
            private dynamic document;
            private readonly List<DataTable> data;
            private readonly CrystalRuntime runtime;
            public CrystalSession(object document, List<DataTable> data, CrystalRuntime runtime)
            { this.document = document; this.data = data; this.runtime = runtime; }
            public object Document { get { return document; } }
            public void ExportPdf(string path)
            {
                try { document.ExportToDisk(runtime.EnumValue("ExportFormatType", "PortableDocFormat"), path); }
                catch (Exception ex)
                {
                    throw new InvalidOperationException("Crystal PDF export failed. Check required report parameters, table mappings and installed Crystal database drivers. Use Preview for parameter prompts, or configure Parameters in the template's .rpt.json file. " + ex.Message, ex);
                }
            }
            public void Dispose()
            {
                if (document == null) return;
                try { document.Close(); }
                finally { document.Dispose(); document = null; foreach (var table in data) table.Dispose(); data.Clear(); }
            }
        }
    }
}
