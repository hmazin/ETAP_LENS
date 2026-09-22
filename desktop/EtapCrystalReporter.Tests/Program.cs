using System;
using System.Collections.Generic;
using System.Data;
using System.Data.SQLite;
using System.IO;
using System.Linq;
using System.Threading;
using System.Web.Script.Serialization;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Services;
using EtapCrystalReporter.Utilities;
using EtapCrystalReporter.UI;

internal static class Tests
{
    private static int failures;
    private static int total;
    private static string root;
    private static Logger log;
    private static EtapDatabaseService databases;

    [STAThread]
    private static int Main(string[] args)
    {
        root = Path.Combine(Path.GetTempPath(), "EtapCrystalTests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        log = new Logger(Path.Combine(root, "Logs")); databases = new EtapDatabaseService(log, root);
        try
        {
            if (args.Length > 0) return Command(args);
            Test("Study types come from metadata regardless of filename", delegate
            {
                foreach (int type in new[] { 1, 3, 4, 5 })
                {
                    string path = Fixture("renamed-" + type + ".SA2S", type);
                    using (var db = databases.Open(path)) { Equal(type, db.Info.StudyType); True(!db.Info.StudyName.StartsWith("Unknown")); Equal(2, db.Info.Tables.Count); }
                }
            });
            Test("Unbalanced load flow is detected from LFSumTotalLF3PH, not ISCStudyCase", delegate
            {
                string path = Path.Combine(root, "flow.UL1S");
                Execute(path, "CREATE TABLE LFSumTotalLF3PH(BusID TEXT, MW REAL); INSERT INTO LFSumTotalLF3PH VALUES ('Bus A', 1.5);");
                using (var db = databases.Open(path)) { Equal(2, db.Info.StudyType); Equal("Unbalanced Load Flow", db.Info.StudyName); }
            });
            Test("Unbalanced load flow with no result rows remains unknown", delegate
            {
                string path = Path.Combine(root, "empty.UL1S");
                Execute(path, "CREATE TABLE LFSumTotalLF3PH(BusID TEXT, MW REAL);");
                using (var db = databases.Open(path)) { Equal(null, db.Info.StudyType); True(db.Info.DetectionNote.Contains("no load flow results")); }
            });
            Test("Source is unchanged and report spacer rows survive", delegate
            {
                string path = Fixture("preserve.SA1S", 1), hash = FileValidator.Sha256(path);
                File.SetAttributes(path, FileAttributes.ReadOnly);
                try
                {
                    using (var db = databases.Open(path))
                    using (var table = db.ReadTable("ibus")) { Equal(2, table.Rows.Count); Equal("###<<<BlankLine>>>###", table.Rows[1]["ID"]); Equal(hash, db.Info.SourceSha256); }
                    Equal(hash, FileValidator.Sha256(path));
                    True(!Directory.EnumerateDirectories(root, "EtapCrystal-*").Any());
                }
                finally { File.SetAttributes(path, FileAttributes.Normal); }
            });
            Test("Invalid SQLite header is rejected and temporary copy removed", delegate
            {
                string path = Path.Combine(root, "invalid.SA2S"); File.WriteAllText(path, "not a database");
                Throws<InvalidDataException>(() => databases.Open(path));
                True(!Directory.EnumerateDirectories(root, "EtapCrystal-*").Any());
            });
            Test("Corrupt SQLite body is rejected", delegate
            {
                string path = Path.Combine(root, "corrupt.SA2S"); File.WriteAllBytes(path, System.Text.Encoding.ASCII.GetBytes("SQLite format 3\0" + new string('x', 1000)));
                Throws<Exception>(() => databases.Open(path));
            });
            Test("Nonempty WAL and rollback journal are never silently ignored", delegate
            {
                foreach (string suffix in new[] { "-wal", "-journal" })
                {
                    string path = Fixture("sidecar" + suffix + ".SA2S", 3); File.WriteAllText(path + suffix, "active");
                    Throws<IOException>(() => databases.Open(path));
                    Equal("active", File.ReadAllText(path + suffix));
                }
            });
            Test("Missing metadata remains unknown", delegate
            {
                string path = Fixture("SC0.5c-missing.SA2S", 3);
                Execute(path, "DROP TABLE ISCStudyCase");
                using (var db = databases.Open(path)) { Equal(null, db.Info.StudyType); True(db.Info.StudyName.StartsWith("Unknown")); }
            });
            Test("Conflicting study rows remain unknown", delegate
            {
                string path = Fixture("conflict.SA2S", 3); Execute(path, "INSERT INTO ISCStudyCase VALUES (4)");
                using (var db = databases.Open(path)) { Equal(null, db.Info.StudyType); True(db.Info.DetectionNote.Contains("conflicting")); }
            });
            Test("Unknown numeric study types are reported without a guess", delegate
            {
                using (var db = databases.Open(Fixture("unknown.SA2S", 99))) { Equal(99, db.Info.StudyType); True(db.Info.StudyName.Contains("99")); }
            });
            Test("Template catalog scans recursively and isolates invalid sidecars", delegate
            {
                string folder = Path.Combine(root, "templates"); Directory.CreateDirectory(Path.Combine(folder, "Momentary"));
                File.WriteAllText(Path.Combine(folder, "Momentary", "Complete.rpt"), "fixture");
                File.WriteAllText(Path.Combine(folder, "Bad.rpt"), "fixture"); File.WriteAllText(Path.Combine(folder, "Bad.rpt.json"), "{");
                var warnings = new List<string>(); var catalog = TemplateCatalog.Scan(folder, warnings.Add);
                Equal(1, catalog.Count); Equal("Momentary / Complete", catalog[0].Name); Equal(1, warnings.Count);
            });
            Test("Explicit study restrictions reject unknown and mismatched types", delegate
            {
                var template = DummyTemplate("limited"); template.Options.StudyTypes = new[] { 3 };
                Throws<InvalidDataException>(() => TemplateCatalog.ValidateStudy(template, new EtapStudyInfo { StudyType = 4 }));
                Throws<InvalidDataException>(() => TemplateCatalog.ValidateStudy(template, new EtapStudyInfo()));
                TemplateCatalog.ValidateStudy(template, new EtapStudyInfo { StudyType = 3 });
            });
            Test("Aliases, qualified locations and scoped mappings resolve exactly", delegate
            {
                var template = DummyTemplate("binding"); template.Options.TableMappings["Sub/Alias"] = "IBus";
                Equal("IBus", TableBinding.Resolve("IBus", "", "", template, new[] { "IBus" }));
                Equal("IBus", TableBinding.Resolve("Alias", "dbo.[IBus]", "", template, new[] { "IBus" }));
                Equal("IBus", TableBinding.Resolve("Alias", "missing", "Sub", template, new[] { "IBus" }));
                Throws<InvalidDataException>(() => TableBinding.Resolve("Command", "Command", "", template, new[] { "IBus" }));
            });
            Test("Table binding preserves nulls and numeric values", delegate
            {
                using (var db = databases.Open(Fixture("shape.SA2S", 3)))
                using (var source = db.ReadTable("IBus"))
                using (var shaped = TableBinding.Shape(source, "Alias", new Dictionary<string, Type> { { "ID", typeof(string) }, { "kV", typeof(double) } }))
                { Equal("Alias", shaped.TableName); Equal(13.8, shaped.Rows[0]["kV"]); Equal(DBNull.Value, shaped.Rows[1]["kV"]); }
            });
            Test("A renamed source column resolves through the template's FieldMappings", delegate
            {
                using (var table = new DataTable("T"))
                {
                    table.Columns.Add("UseChargerOpLoad", typeof(long)); table.Rows.Add(1);
                    var mappings = new Dictionary<string, string> { { "T/Charger", "UseChargerOpLoad" } };
                    using (var shaped = TableBinding.Shape(table, "T", new Dictionary<string, Type> { { "Charger", typeof(int) } }, null, mappings))
                        Equal(1, shaped.Rows[0]["Charger"]);
                    Throws<InvalidDataException>(() => TableBinding.Shape(table, "T", new Dictionary<string, Type> { { "Charger", typeof(int) } }));
                }
            });
            Test("A field listed as optional binds blank when genuinely absent", delegate
            {
                using (var table = new DataTable("T"))
                {
                    table.Columns.Add("Present", typeof(long)); table.Rows.Add(7);
                    var optional = new HashSet<string>(new[] { "T/Reserved3" }, StringComparer.OrdinalIgnoreCase);
                    using (var shaped = TableBinding.Shape(table, "T",
                        new Dictionary<string, Type> { { "Present", typeof(int) }, { "Reserved3", typeof(int) } }, null, null, optional))
                    { Equal(7, shaped.Rows[0]["Present"]); Equal(DBNull.Value, shaped.Rows[0]["Reserved3"]); }
                }
            });
            Test("Missing fields and lossy integer conversion fail explicitly", delegate
            {
                using (var table = new DataTable("T"))
                {
                    table.Columns.Add("value", typeof(double)); table.Rows.Add(1.2);
                    Throws<InvalidDataException>(() => TableBinding.Shape(table, "T", new Dictionary<string, Type> { { "missing", typeof(string) } }));
                    Throws<InvalidDataException>(() => TableBinding.Shape(table, "T", new Dictionary<string, Type> { { "value", typeof(int) } }));
                }
                Equal(true, TableBinding.ConvertValue(1L, typeof(bool))); Throws<FormatException>(() => TableBinding.ConvertValue(2, typeof(bool)));
            });
            Test("Additional source fields remain available to template formulas", delegate
            {
                using (var source = new DataTable("Isummary"))
                {
                    source.Columns.Add("SynGen", typeof(long)); source.Columns.Add("Inverter", typeof(long)); source.Rows.Add(2, 3);
                    using (var shaped = TableBinding.Shape(source, "Isummary", new Dictionary<string, Type> { { "SynGen", typeof(int) } }))
                    { Equal(3L, shaped.Rows[0]["Inverter"]); Equal(2, shaped.Columns.Count); }
                }
            });
            Test("Export collisions never overwrite existing files", delegate
            {
                var job = Job(Fixture("collision.SA2S", 3), DummyTemplate("Momentary / Complete"));
                var exporter = new ExportService(log);
                using (var fake = new FakeSession(false))
                {
                    var info = new EtapStudyInfo { SourcePath = job.SourcePath };
                    string first = exporter.Export(fake, job, info); string hash = FileValidator.Sha256(first);
                    string second = exporter.Export(fake, job, info);
                    True(first != second); True(second.EndsWith("_2.pdf")); Equal(hash, FileValidator.Sha256(first));
                }
            });
            Test("Failed export removes partial files", delegate
            {
                var job = Job(Fixture("export-failure.SA2S", 3), DummyTemplate("failure"));
                using (var fake = new FakeSession(true)) Throws<IOException>(() => new ExportService(log).Export(fake, job, new EtapStudyInfo()));
                True(!Directory.EnumerateFiles(job.OutputDirectory, ".etap-*").Any());
            });
            Test("Batch continues after failure and honors cancellation between jobs", delegate
            {
                var service = new BatchReportService(databases, new FakeReports(), new ExportService(log), log);
                var good = Job(Fixture("batch.SA2S", 3), DummyTemplate("batch"));
                var bad = Job(Path.Combine(root, "missing.SA2S"), good.Template);
                var results = new List<ReportResult>(); service.RunBatch(new[] { bad, good }, CancellationToken.None, results.Add);
                Equal("Failed", results[0].Status); Equal("Success", results[1].Status);
                using (var cancel = new CancellationTokenSource())
                { results.Clear(); service.RunBatch(new[] { good, good }, cancel.Token, r => { results.Add(r); cancel.Cancel(); }); Equal(1, results.Count); }
            });
            Test("Report header changes never mutate the source or numerical study data", delegate
            {
                string path = Fixture("headers.SA1S", 1);
                Execute(path, "CREATE TABLE Headr(SN TEXT, Project TEXT, Date TEXT, PSRev TEXT); INSERT INTO Headr VALUES ('Original SN','Original project','Original date','24.0');");
                string before = FileValidator.Sha256(path);
                using (var db = databases.Open(path))
                {
                    using (var table = db.ReadTable("Headr"))
                    {
                        ReportHeaders.ApplyToTable(table, new Dictionary<string, string> { { "sn", null }, { "project", "Team <A> & {literal}" }, { "date", "" } });
                        Equal("Original SN", table.Rows[0]["SN"]); Equal("Original project", table.Rows[0]["Project"]);
                        Equal("Original date", table.Rows[0]["Date"]); Equal("24.0", table.Rows[0]["PSRev"]);
                    }
                    using (var table = db.ReadTable("Headr")) Equal("Original SN", table.Rows[0]["SN"]);
                    using (var table = db.ReadTable("IBus"))
                    {
                        ReportHeaders.ApplyToTable(table, new Dictionary<string, string> { { "project", "New" } });
                        Equal(13.8, table.Rows[0]["kV"]);
                    }
                }
                Equal(before, FileValidator.Sha256(path));
            });
            Test("Revision and configuration presentation never changes study-case data", delegate
            {
                using (var table = new DataTable("ISCStudyCase"))
                {
                    table.Columns.Add("Revision", typeof(string)); table.Columns.Add("Config", typeof(string)); table.Columns.Add("StudyType", typeof(int));
                    table.Rows.Add("Old", "Old configuration", 1);
                    ReportHeaders.ApplyToTable(table, new Dictionary<string, string> { { "revision", "B" }, { "configuration", "Normal" } });
                    Equal("Old", table.Rows[0]["Revision"]); Equal("Old configuration", table.Rows[0]["Config"]); Equal(1, table.Rows[0]["StudyType"]);
                }
            });
            Test("Header allowlist and text limits fail explicitly", delegate
            {
                Throws<InvalidDataException>(() => ReportHeaders.Validate(new Dictionary<string, string> { { "StudyType", "5" } }));
                Throws<InvalidDataException>(() => ReportHeaders.Validate(new Dictionary<string, string> { { "date", new string('x', 33) } }));
                Throws<InvalidDataException>(() => ReportHeaders.Validate(new Dictionary<string, string> { { "project", "A\nB" } }));
            });
            Console.WriteLine(failures == 0 ? "All " + total + " tests passed." : failures + " of " + total + " test(s) failed.");
            return failures == 0 ? 0 : 1;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.ToString()); return 1; }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }

    private static int Command(string[] args)
    {
        var json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
        if (args[0] == "--inspect")
        {
            foreach (string path in Directory.EnumerateFiles(args[1]).Where(x => new[] { ".sa1s", ".sa2s" }.Contains(Path.GetExtension(x).ToLowerInvariant())))
            {
                string before = FileValidator.Sha256(path);
                using (var db = databases.Open(path))
                {
                    var tables = new List<object>();
                    foreach (string name in db.Info.Tables)
                    using (var table = db.ReadTable(name))
                        tables.Add(new { name, rows = table.Rows.Count, columns = table.Columns.Cast<DataColumn>().Select(c => new { name = c.ColumnName, type = c.DataType.Name }).ToArray() });
                    Console.WriteLine(json.Serialize(new { file = Path.GetFileName(path), db.Info.StudyType, db.Info.StudyName, db.Info.DetectionNote, tables }));
                }
                Equal(before, FileValidator.Sha256(path));
            }
            return 0;
        }
        if (args[0] == "--templates")
        {
            var runtime = new CrystalRuntime(); Console.WriteLine(runtime.Version);
            foreach (var template in TemplateCatalog.Scan(args[1], Console.Error.WriteLine))
            {
                dynamic report = runtime.NewDocument();
                try
                {
                    report.Load(template.Path, runtime.EnumValue("OpenReportMethod", "OpenReportByTempCopy"));
                    var sections = new List<object>(); sections.Add(ReportMetadata(report, ""));
                    foreach (dynamic sub in report.Subreports) sections.Add(ReportMetadata(sub, (string)sub.Name));
                    Console.WriteLine(json.Serialize(new { template = template.Name, sections }));
                }
                finally { report.Close(); report.Dispose(); }
            }
            return 0;
        }
        if (args[0] == "--report")
        {
            var runtime = new CrystalRuntime();
            var logger = new Logger(Path.Combine(args[3], "Logs"));
            var service = new BatchReportService(new EtapDatabaseService(logger), new CrystalReportService(runtime, logger), new ExportService(logger), logger);
            var result = service.Run(new ReportJob { SourcePath = args[1], Template = TemplateCatalog.Load(args[2]), OutputDirectory = args[3] });
            Console.WriteLine(json.Serialize(result)); return result.Status == "Success" ? 0 : 1;
        }
        if (args[0] == "--batch")
        {
            var runtime = new CrystalRuntime();
            var logger = new Logger(Path.Combine(args[3], "Logs"));
            var service = new BatchReportService(new EtapDatabaseService(logger), new CrystalReportService(runtime, logger), new ExportService(logger), logger);
            var template = TemplateCatalog.Load(args[2]);
            var jobs = Directory.EnumerateFiles(args[1]).Where(x => new[] { ".sa1s", ".sa2s" }.Contains(Path.GetExtension(x).ToLowerInvariant()))
                .OrderByDescending(x => Path.GetExtension(x).Equals(".sa1s", StringComparison.OrdinalIgnoreCase))
                .Select(x => new ReportJob { SourcePath = x, Template = template, OutputDirectory = args[3] });
            bool success = true;
            return StaTask.Run(delegate
            {
                service.RunBatch(jobs, CancellationToken.None, result => { Console.WriteLine(json.Serialize(result)); if (result.Status != "Success") success = false; });
                return success ? 0 : 1;
            }).GetAwaiter().GetResult();
        }
        if (args[0] == "--ui-smoke")
        {
            System.Windows.Forms.Application.EnableVisualStyles();
            var settings = new AppSettings { TemplateDirectory = args[1], OutputDirectory = root };
            using (var form = new MainForm(settings, log))
            {
                form.StartPosition = System.Windows.Forms.FormStartPosition.Manual;
                form.Location = new System.Drawing.Point(-20000, -20000); form.ShowInTaskbar = false;
                form.Show();
                for (int i = 0; i < 40; i++) { System.Windows.Forms.Application.DoEvents(); Thread.Sleep(50); }
                using (var bitmap = new System.Drawing.Bitmap(form.Width, form.Height))
                { form.DrawToBitmap(bitmap, new System.Drawing.Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(args[2]); }
                form.Close();
            }
            return 0;
        }
        if (args[0] == "--preview-smoke")
        {
            System.Windows.Forms.Application.EnableVisualStyles();
            var runtime = new CrystalRuntime(); var reports = new CrystalReportService(runtime, log);
            var job = new ReportJob { SourcePath = args[1], Template = TemplateCatalog.Load(args[2]), OutputDirectory = root };
            using (var form = new ReportPreviewForm(runtime, delegate
            {
                using (var db = databases.Open(job.SourcePath)) return new PreviewDocument { Session = reports.Prepare(db, job.Template), Study = db.Info };
            }, new ExportService(log), job, false))
            {
                form.StartPosition = System.Windows.Forms.FormStartPosition.Manual;
                form.Location = new System.Drawing.Point(-20000, -20000); form.ShowInTaskbar = false;
                form.Show();
                for (int i = 0; i < 80; i++) { System.Windows.Forms.Application.DoEvents(); Thread.Sleep(50); }
                dynamic viewer = form.Controls.Cast<System.Windows.Forms.Control>().Single(x => x.GetType().FullName == "CrystalDecisions.Windows.Forms.CrystalReportViewer");
                viewer.RefreshReport();
                for (int i = 0; i < 40; i++) { System.Windows.Forms.Application.DoEvents(); Thread.Sleep(50); }
                int reads = File.ReadAllLines(Directory.GetFiles(log.DirectoryPath).Single()).Count(x => x.Contains("database.schema"));
                True(reads >= 2);
                using (var bitmap = new System.Drawing.Bitmap(form.Width, form.Height))
                { form.DrawToBitmap(bitmap, new System.Drawing.Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(args[3]); }
                form.Close();
            }
            return 0;
        }
        throw new ArgumentException("Unknown diagnostic command.");
    }

    private static object ReportMetadata(dynamic report, string scope)
    {
        var tables = new List<object>();
        foreach (dynamic table in report.Database.Tables)
        {
            var fields = new List<object>();
            foreach (dynamic field in table.Fields) fields.Add(new { name = (string)field.Name, type = field.ValueType.ToString() });
            tables.Add(new { name = (string)table.Name, location = (string)table.Location, fields });
        }
        var parameters = new List<object>();
        foreach (dynamic parameter in report.DataDefinition.ParameterFields)
            parameters.Add(new { name = (string)parameter.Name, type = parameter.ValueType.ToString(), current = (int)parameter.CurrentValues.Count, defaults = (int)parameter.DefaultValues.Count });
        return new { scope, tables, parameters };
    }

    private static string Fixture(string name, int type)
    {
        string path = Path.Combine(root, name);
        Execute(path, "CREATE TABLE ISCStudyCase(StudyType INTEGER); INSERT INTO ISCStudyCase VALUES (" + type + ");" +
            "CREATE TABLE IBus(ID TEXT, kV REAL); INSERT INTO IBus VALUES ('BUS-A',13.8); INSERT INTO IBus VALUES ('###<<<BlankLine>>>###',NULL);");
        return path;
    }
    private static void Execute(string path, string sql)
    {
        using (var connection = new SQLiteConnection(new SQLiteConnectionStringBuilder { DataSource = path, Pooling = false }.ConnectionString))
        { connection.Open(); using (var command = connection.CreateCommand()) { command.CommandText = sql; command.ExecuteNonQuery(); } }
    }
    private static ReportTemplate DummyTemplate(string name)
    {
        string path = Path.Combine(root, ExportService.SafeName(name) + ".rpt"); File.WriteAllText(path, "test fixture, not a Crystal template");
        return TemplateCatalog.Load(path, name);
    }
    private static ReportJob Job(string path, ReportTemplate template)
    { return new ReportJob { SourcePath = path, Template = template, OutputDirectory = Path.Combine(root, "Output") }; }
    private static void Test(string name, Action test)
    { total++; try { test(); Console.WriteLine("PASS " + name); } catch (Exception ex) { failures++; Console.WriteLine("FAIL " + name + ": " + ex); } }
    private static void True(bool value) { if (!value) throw new Exception("Assertion failed."); }
    private static void Equal(object expected, object actual)
    { if (!object.Equals(expected, actual)) throw new Exception("Expected " + expected + "; got " + actual); }
    private static void Throws<T>(Action action) where T : Exception
    { try { action(); } catch (T) { return; } throw new Exception("Expected " + typeof(T).Name); }
    private sealed class FakeReports : IReportService
    { public IReportSession Prepare(DatabaseSnapshot database, ReportTemplate template) { TemplateCatalog.ValidateStudy(template, database.Info); return new FakeSession(false); } }
    private sealed class FakeSession : IReportSession
    {
        private readonly bool fail;
        public FakeSession(bool fail) { this.fail = fail; }
        public object Document { get { return null; } }
        public void ExportPdf(string path)
        { File.WriteAllText(path, "%PDF-1.4\nTEST DOUBLE ONLY - NOT A REAL REPORT"); if (fail) throw new IOException("Simulated export failure"); }
        public void Dispose() { }
    }
}
