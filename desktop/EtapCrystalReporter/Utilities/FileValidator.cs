using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace EtapCrystalReporter.Utilities
{
    public static class FileValidator
    {
        public static string CopyStudy(string source, string destination)
        {
            string extension = Path.GetExtension(source);
            if (!new[] { ".sa1s", ".sa2s", ".ul1s" }.Contains(extension, StringComparer.OrdinalIgnoreCase))
                throw new InvalidDataException("Select an ETAP .SA1S, .SA2S, or .UL1S result file.");
            // We never open the source with SQLite: even read-only SQLite can touch WAL sidecars.
            using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                RejectActiveDatabase(source);
                byte[] header = new byte[16];
                if (input.Read(header, 0, header.Length) != 16 || Encoding.ASCII.GetString(header) != "SQLite format 3\0")
                    throw new InvalidDataException("This file is not a SQLite 3 database.");
                input.Position = 0;
                using (var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    input.CopyTo(output);
                RejectActiveDatabase(source);
                input.Position = 0;
                using (var sha = SHA256.Create())
                    return BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
            }
        }

        private static void RejectActiveDatabase(string source)
        {
            foreach (string suffix in new[] { "-wal", "-journal" })
            {
                var sidecar = new FileInfo(source + suffix);
                if (sidecar.Exists && sidecar.Length > 0)
                    throw new IOException("The result has a non-empty " + suffix + " file. Close the study in ETAP and use a completed, checkpointed result. No source files were changed.");
            }
        }

        public static string Sha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }
    }
}
