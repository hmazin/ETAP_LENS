using System;
using System.IO;
using System.Web.Script.Serialization;

namespace EtapCrystalReporter.Utilities
{
    public sealed class AppSettings
    {
        public string TemplateDirectory { get; set; }
        public string OutputDirectory { get; set; }
        public bool OpenPdf { get; set; }
        public bool Timestamp { get; set; }
        public bool HideSerialNumber { get; set; }
        // "summary" or "complete": which single template auto-match/batch prefers per
        // family (the template literally named Summary.rpt or Complete.rpt), rather
        // than every specialized variant (Momentary Duty, Alerts, LG/LL/LLG, ...).
        public string PreferredReportKind { get; set; }
        public static string DataDirectory
        { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ETAP Lens", "CrystalReporter"); } }
        public AppSettings()
        {
            TemplateDirectory = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Templates");
            OutputDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "ETAP Reports");
            Timestamp = false;
            PreferredReportKind = "summary";
        }
        public static AppSettings Load()
        {
            string path = Path.Combine(DataDirectory, "settings.json");
            if (!File.Exists(path)) return new AppSettings();
            var value = new JavaScriptSerializer().Deserialize<AppSettings>(File.ReadAllText(path));
            if (value == null || string.IsNullOrWhiteSpace(value.TemplateDirectory) || string.IsNullOrWhiteSpace(value.OutputDirectory))
                throw new InvalidDataException("Settings are incomplete: " + path);
            // Settings saved before this option existed have no value here.
            if (string.IsNullOrWhiteSpace(value.PreferredReportKind)) value.PreferredReportKind = "summary";
            return value;
        }
        public void Save()
        {
            Directory.CreateDirectory(DataDirectory);
            string path = Path.Combine(DataDirectory, "settings.json");
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temporary, new JavaScriptSerializer().Serialize(this));
                if (File.Exists(path)) File.Replace(temporary, path, null);
                else File.Move(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
    }
}
