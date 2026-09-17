using System;
using System.Collections.Generic;
using System.Data;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using EtapCrystalReporter.Models;

namespace EtapCrystalReporter.Services
{
    public static class ReportHeaders
    {
        // These are display fields only. In particular, Revision is the study
        // revision, not Headr.PSRev (the ETAP software version).
        private static readonly Dictionary<string, string[]> Fields = new Dictionary<string, string[]> {
            { "sn", new[] { "Headr", "SN", "60" } },
            { "date", new[] { "Headr", "Date", "32" } },
            { "revision", new[] { "ISCStudyCase", "Revision", "32" } },
            { "project", new[] { "Headr", "Project", "120" } },
            { "location", new[] { "Headr", "Loc", "120" } },
            { "contract", new[] { "Headr", "Contr", "100" } },
            { "engineer", new[] { "Headr", "Eng", "100" } },
            { "filename", new[] { "Headr", "FileN", "120" } },
            { "study_case", new[] { "Headr", "STDCase", "120" } },
            { "configuration", new[] { "ISCStudyCase", "Config", "60" } },
            { "title_1", new[] { "Headr", "1st", "180" } },
            { "title_2", new[] { "Headr", "2nd", "180" } }
        };

        public static void Validate(IDictionary<string, string> settings)
        {
            if (settings == null) return;
            foreach (var pair in settings)
            {
                if (!Fields.ContainsKey(pair.Key)) throw new InvalidDataException("Unsupported report header: " + pair.Key);
                string value = pair.Value;
                if (value != null && (value.Length > int.Parse(Fields[pair.Key][2]) || value.Any(char.IsControl)))
                    throw new InvalidDataException("Report header text is too long or contains a line break/control character: " + pair.Key);
            }
        }

        public static void ApplyToTable(DataTable table, IDictionary<string, string> settings)
        {
            Validate(settings);
            if (settings == null) return;
            foreach (var pair in settings)
            {
                // Only the two plain title fields require data binding. Named
                // header text is replaced in the report objects below, so study
                // case settings and database join fields retain their values.
                if (pair.Key != "title_1" && pair.Key != "title_2") continue;
                var field = Fields[pair.Key];
                if (!table.TableName.Equals(field[0], StringComparison.OrdinalIgnoreCase)) continue;
                var column = table.Columns.Cast<DataColumn>().FirstOrDefault(c => c.ColumnName.Equals(field[1], StringComparison.OrdinalIgnoreCase));
                if (column == null || column.DataType != typeof(string))
                    throw new InvalidDataException("This study does not contain the expected text header: " + pair.Key);
                column.MaxLength = -1;
                foreach (DataRow row in table.Rows) row[column] = pair.Value ?? "";
            }
        }

        public static void ApplyPresentation(dynamic report, string scope, DatabaseSnapshot database,
                                           ReportTemplate template, IDictionary<string, string> settings)
        {
            if (settings == null || settings.Count == 0) return;
            var replacements = new Dictionary<string, string>();
            foreach (dynamic table in report.Database.Tables)
            {
                string alias = (string)table.Name;
                string actual = TableBinding.Resolve(alias, (string)table.Location, scope, template, database.Info.Tables);
                foreach (var pair in settings)
                {
                    var field = Fields[pair.Key];
                    if (actual.Equals(field[0], StringComparison.OrdinalIgnoreCase)) replacements["{" + alias + "." + field[1] + "}"] = pair.Value;
                }
            }
            foreach (dynamic section in report.ReportDefinition.Sections)
            foreach (dynamic item in section.ReportObjects)
            {
                string kind = item.Kind.ToString();
                string text = kind == "TextObject" ? (string)item.Text : kind == "FieldObject" ? (string)item.DataSource.FormulaName : "";
                var matches = replacements.Where(pair => text.IndexOf(pair.Key, StringComparison.OrdinalIgnoreCase) >= 0).ToList();
                if (!matches.Any()) continue;
                if (matches.Any(pair => pair.Value == null))
                {
                    // ETAP embeds label and field in one text object. Removing
                    // the complete object hides both on every page.
                    if (kind == "TextObject") item.Text = "";
                    item.ObjectFormat.EnableSuppress = true;
                }
                else if (kind == "TextObject")
                {
                    foreach (var pair in matches)
                        text = Regex.Replace(text, Regex.Escape(pair.Key), match => pair.Value, RegexOptions.IgnoreCase);
                    item.Text = text; // Literal text, never an executable Crystal formula.
                }
            }
        }
    }
}
