using System;
using System.Collections.Generic;
using System.Data;
using System.Globalization;
using System.Linq;
using EtapCrystalReporter.Models;

namespace EtapCrystalReporter.Services
{
    public static class StudyDetectionService
    {
        public static void Detect(EtapStudyInfo info, Func<string, DataTable> read)
        {
            info.StudyName = "Unknown short-circuit study";
            info.DetectionNote = "ISCStudyCase.StudyType is unavailable. The filename was not used for detection.";
            string table = info.Tables.FirstOrDefault(x => x.Equals("ISCStudyCase", StringComparison.OrdinalIgnoreCase));
            if (table == null) return;
            using (var data = read(table))
            {
                DataColumn column = data.Columns.Cast<DataColumn>().FirstOrDefault(x => x.ColumnName.Equals("StudyType", StringComparison.OrdinalIgnoreCase));
                if (column == null || data.Rows.Count == 0) return;
                var types = new HashSet<int>();
                foreach (DataRow row in data.Rows)
                {
                    int type;
                    if (!int.TryParse(Convert.ToString(row[column], CultureInfo.InvariantCulture), NumberStyles.Integer, CultureInfo.InvariantCulture, out type))
                    { info.DetectionNote = "ISCStudyCase contains a missing or invalid StudyType."; return; }
                    types.Add(type);
                }
                if (types.Count != 1)
                { info.DetectionNote = "ISCStudyCase contains conflicting study types: " + string.Join(", ", types.OrderBy(x => x)); return; }
                info.StudyType = types.Single();
                switch (info.StudyType.Value)
                {
                    case 3: info.StudyName = "ANSI Half-Cycle / Momentary"; break;
                    case 4: info.StudyName = "ANSI 1.5-4 Cycle"; break;
                    case 1: info.StudyName = "Device Duty"; break;
                    case 5: info.StudyName = "ANSI 30-Cycle / Minimum-Fault"; break;
                    default: info.StudyName = "Unknown StudyType " + info.StudyType; break;
                }
                info.DetectionNote = "Detected from ISCStudyCase.StudyType = " + info.StudyType + ".";
            }
        }
    }
}
