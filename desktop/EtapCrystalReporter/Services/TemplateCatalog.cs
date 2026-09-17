using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;
using EtapCrystalReporter.Models;

namespace EtapCrystalReporter.Services
{
    public static class TemplateCatalog
    {
        public static List<ReportTemplate> Scan(string directory, Action<string> warning)
        {
            var templates = new List<ReportTemplate>();
            if (!Directory.Exists(directory)) { warning("Template folder does not exist: " + directory); return templates; }
            string root = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            foreach (string path in Directory.EnumerateFiles(root, "*.rpt", SearchOption.AllDirectories).OrderBy(x => x))
            {
                try { templates.Add(Load(path, Path.ChangeExtension(path.Substring(root.Length), null).Replace(Path.DirectorySeparatorChar.ToString(), " / "))); }
                catch (Exception ex) { warning(Path.GetFileName(path) + ": " + ex.Message); }
            }
            return templates;
        }

        public static ReportTemplate Load(string path, string name = null)
        {
            path = Path.GetFullPath(path);
            if (!File.Exists(path) || !Path.GetExtension(path).Equals(".rpt", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Select an existing Crystal Reports .rpt template.");
            var options = new TemplateOptions();
            if (File.Exists(path + ".json"))
            {
                options = new JavaScriptSerializer().Deserialize<TemplateOptions>(File.ReadAllText(path + ".json"));
                if (options == null || options.StudyTypes == null || options.TableMappings == null || options.Parameters == null)
                    throw new InvalidDataException("Template metadata must contain valid StudyTypes, TableMappings and Parameters values.");
                options.TableMappings = new Dictionary<string, string>(options.TableMappings, StringComparer.OrdinalIgnoreCase);
                if (options.TableMappings.Any(x => string.IsNullOrWhiteSpace(x.Key) || string.IsNullOrWhiteSpace(x.Value)))
                    throw new InvalidDataException("Table mappings cannot be empty.");
            }
            return new ReportTemplate { Path = path, Name = name ?? Path.GetFileNameWithoutExtension(path), Options = options };
        }

        public static void ValidateStudy(ReportTemplate template, EtapStudyInfo info)
        {
            if (template.Options.StudyTypes.Length > 0 && (!info.StudyType.HasValue || !template.Options.StudyTypes.Contains(info.StudyType.Value)))
                throw new InvalidDataException("Template '" + template.Name + "' supports StudyType " + string.Join(", ", template.Options.StudyTypes) + "; selected study is " + info.StudyName + ".");
        }
    }
}
