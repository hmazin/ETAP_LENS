using System;
using System.Collections.Generic;
using System.Data;
using System.Data.SQLite;
using System.IO;
using System.Linq;
using EtapCrystalReporter.Models;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter.Services
{
    public sealed class EtapDatabaseService
    {
        private readonly Logger log;
        private readonly string temporaryRoot;
        public EtapDatabaseService(Logger log, string temporaryRoot = null)
        { this.log = log; this.temporaryRoot = temporaryRoot ?? Path.GetTempPath(); }

        public DatabaseSnapshot Open(string source)
        {
            source = Path.GetFullPath(source);
            string directory = Path.Combine(temporaryRoot, "EtapCrystal-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            SQLiteConnection connection = null;
            Action cleanup = delegate
            {
                if (connection != null) { connection.Dispose(); connection = null; }
                if (Directory.Exists(directory)) Directory.Delete(directory, true);
            };
            try
            {
                string snapshot = Path.Combine(directory, "result.sqlite");
                string hash = FileValidator.CopyStudy(source, snapshot);
                var builder = new SQLiteConnectionStringBuilder
                { DataSource = snapshot, ReadOnly = true, FailIfMissing = true, Pooling = false };
                connection = new SQLiteConnection(builder.ConnectionString);
                connection.Open();
                using (var command = connection.CreateCommand())
                {
                    command.CommandText = "PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;";
                    command.ExecuteNonQuery();
                    command.CommandText = "PRAGMA quick_check;";
                    if (!string.Equals(Convert.ToString(command.ExecuteScalar()), "ok", StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("SQLite integrity check failed.");
                }
                var tables = new List<string>();
                using (var command = connection.CreateCommand())
                {
                    command.CommandText = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name";
                    using (var reader = command.ExecuteReader()) while (reader.Read()) tables.Add(reader.GetString(0));
                }
                var info = new EtapStudyInfo { SourcePath = source, SourceSha256 = hash, Tables = tables };
                // Log schema even when metadata detection fails later.
                log.Write("database.schema", new { source, sha256 = hash, tables });
                Func<string, DataTable> read = delegate(string name)
                {
                    string actual = tables.FirstOrDefault(x => x.Equals(name, StringComparison.OrdinalIgnoreCase));
                    if (actual == null) throw new InvalidDataException("Required SQLite table is missing: " + name);
                    if (connection == null) throw new ObjectDisposedException("DatabaseSnapshot");
                    using (var command = connection.CreateCommand())
                    {
                        command.CommandText = "SELECT * FROM \"" + actual.Replace("\"", "\"\"") + "\"";
                        using (var adapter = new SQLiteDataAdapter(command))
                        {
                            var data = new DataTable(actual) { Locale = System.Globalization.CultureInfo.InvariantCulture };
                            adapter.Fill(data);
                            return data;
                        }
                    }
                };
                StudyDetectionService.Detect(info, read);
                log.Write("database.detected", new { source, info.StudyType, info.StudyName, info.DetectionNote });
                return new DatabaseSnapshot(info, read, cleanup);
            }
            catch { cleanup(); throw; }
        }
    }
}
