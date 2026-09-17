using System;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace EtapCrystalReporter.Utilities
{
    public sealed class Logger
    {
        private readonly object sync = new object();
        public string DirectoryPath { get; private set; }
        public Logger(string directory) { DirectoryPath = directory; Directory.CreateDirectory(directory); }
        public void Write(string eventName, object details)
        {
            var entry = new { utc = DateTime.UtcNow.ToString("o"), eventName = eventName, details = details };
            lock (sync)
            {
                File.AppendAllText(Path.Combine(DirectoryPath, DateTime.UtcNow.ToString("yyyy-MM-dd") + ".jsonl"),
                    new JavaScriptSerializer().Serialize(entry) + Environment.NewLine, Encoding.UTF8);
            }
        }
    }
}
