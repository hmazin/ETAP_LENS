using System;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;
using EtapCrystalReporter.Services;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter.Worker
{
    // One STA process per report: a timeout or native Crystal failure cannot
    // poison the next job. The Python supervisor owns the lease and transport.
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            var json = new JavaScriptSerializer();
            try
            {
                var runtime = new CrystalRuntime();
                runtime.CheckExecution();
                if (args.Length == 1 && args[0] == "--probe")
                {
                    Console.WriteLine(json.Serialize(new { ok = true, runtime = runtime.Version, bitness = IntPtr.Size * 8 }));
                    return 0;
                }
                if (args.Length != 1) throw new ArgumentException("Pass a report request JSON file or --probe.");
                var request = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(args[0]));
                Func<string, string> text = name => Convert.ToString(request[name]);
                string root = Path.GetFullPath(text("template_root")).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                string templatePath = Path.GetFullPath(Path.Combine(root, text("template_path").Replace('/', Path.DirectorySeparatorChar)));
                if (!templatePath.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Template path escapes the catalog root.");
                if (FileValidator.Sha256(templatePath) != text("template_sha256")) throw new InvalidDataException("The template changed after this job was queued.");
                string optionsHash = File.Exists(templatePath + ".json") ? FileValidator.Sha256(templatePath + ".json") : "";
                if (optionsHash != text("options_sha256")) throw new InvalidDataException("Template parameters changed after this job was queued.");
                var log = new Logger(text("log_directory"));
                using (var database = new EtapDatabaseService(log, text("work_directory")).Open(text("source_path")))
                {
                    if (database.Info.SourceSha256 != text("source_sha256")) throw new InvalidDataException("Original study checksum does not match.");
                    if (database.Info.StudyType != Convert.ToInt32(request["study_type"])) throw new InvalidDataException("The detected study type does not match the job.");
                    var template = TemplateCatalog.Load(templatePath, text("template_name"));
                    using (var report = new CrystalReportService(runtime, log).Prepare(database, template))
                        report.ExportPdf(text("output_path"));
                }
                using (var stream = File.OpenRead(text("output_path")))
                {
                    byte[] header = new byte[5];
                    if (stream.Read(header, 0, 5) != 5 || System.Text.Encoding.ASCII.GetString(header) != "%PDF-")
                        throw new InvalidDataException("Crystal did not export a PDF.");
                }
                Console.WriteLine(json.Serialize(new { ok = true, sha256 = FileValidator.Sha256(text("output_path")) }));
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex.ToString());
                return 1;
            }
        }
    }
}
