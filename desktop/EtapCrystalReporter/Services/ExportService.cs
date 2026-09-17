using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter.Services
{
    public sealed class ExportService
    {
        private readonly Logger log;
        public ExportService(Logger log) { this.log = log; }

        public string Export(IReportSession session, ReportJob job, EtapStudyInfo study)
        {
            string directory = Path.GetFullPath(job.OutputDirectory);
            Directory.CreateDirectory(directory);
            string stem = SafeName(Path.GetFileNameWithoutExtension(job.SourcePath)) + "_" + SafeName(job.Template.Name);
            if (job.Timestamp) stem += "_" + DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
            string temporary = Path.Combine(directory, ".etap-" + Guid.NewGuid().ToString("N") + ".tmp.pdf");
            string output = null;
            try
            {
                session.ExportPdf(temporary);
                using (var stream = File.OpenRead(temporary))
                {
                    byte[] header = new byte[5];
                    if (stream.Read(header, 0, 5) != 5 || Encoding.ASCII.GetString(header) != "%PDF-")
                        throw new InvalidDataException("Crystal did not produce a valid PDF header.");
                }
                for (int attempt = 0; attempt < 10000; attempt++)
                {
                    string candidate = Path.Combine(directory, stem + (attempt == 0 ? "" : "_" + (attempt + 1)) + ".pdf");
                    try { File.Move(temporary, candidate); output = candidate; break; }
                    catch (IOException) { if (!File.Exists(candidate)) throw; }
                }
                if (output == null) throw new IOException("Could not choose a unique PDF filename.");
                log.Write("report.exported", new { source = study.SourcePath, sourceSha256 = study.SourceSha256,
                    study.StudyType, template = job.Template.Path, output, pdfSha256 = FileValidator.Sha256(output) });
                return output;
            }
            catch (Exception ex)
            {
                if (output != null) throw new IOException("PDF was saved at " + output + ", but writing its diagnostic log failed. " + ex.Message, ex);
                throw;
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        public static string SafeName(string value)
        {
            var invalid = Path.GetInvalidFileNameChars();
            string safe = new string(value.Select(c => invalid.Contains(c) || char.IsControl(c) ? '_' : c).ToArray()).Trim(' ', '.');
            while (safe.Contains("__")) safe = safe.Replace("__", "_");
            if (safe.Length == 0) safe = "Report";
            if (safe.Length > 85) safe = safe.Substring(0, 85).TrimEnd(' ', '.');
            return safe;
        }
    }
}
