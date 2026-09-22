using System;
using System.Collections.Generic;
using System.Data;
using System.Globalization;
using System.IO;
using System.Linq;
using EtapCrystalReporter.Models;

namespace EtapCrystalReporter.Services
{
    public static class TableBinding
    {
        public static string Resolve(string alias, string location, string scope, ReportTemplate template, IEnumerable<string> tables)
        {
            string mapped;
            string scoped = string.IsNullOrEmpty(scope) ? alias : scope + "/" + alias;
            if (template.Options.TableMappings.TryGetValue(scoped, out mapped) || template.Options.TableMappings.TryGetValue(alias, out mapped))
                return Match(mapped, tables);
            string direct = tables.FirstOrDefault(x => x.Equals(alias, StringComparison.OrdinalIgnoreCase));
            if (direct != null) return direct;
            // A Crystal alias may differ from its qualified database table location.
            string leaf = (location ?? "").Split('.').Last().Trim('[', ']', '"', '`');
            return Match(leaf, tables);
        }

        private static string ResolveField(string alias, string scope, string fieldName, IDictionary<string, string> fieldMappings)
        {
            if (fieldMappings == null) return fieldName;
            string mapped;
            string scoped = string.IsNullOrEmpty(scope) ? null : scope + "/" + alias + "/" + fieldName;
            if (scoped != null && fieldMappings.TryGetValue(scoped, out mapped)) return mapped;
            if (fieldMappings.TryGetValue(alias + "/" + fieldName, out mapped)) return mapped;
            return fieldName;
        }

        private static bool IsOptional(string alias, string scope, string fieldName, ISet<string> optionalFields)
        {
            if (optionalFields == null) return false;
            string scoped = string.IsNullOrEmpty(scope) ? null : scope + "/" + alias + "/" + fieldName;
            return (scoped != null && optionalFields.Contains(scoped)) || optionalFields.Contains(alias + "/" + fieldName);
        }

        private static string Match(string name, IEnumerable<string> tables)
        {
            string match = tables.FirstOrDefault(x => x.Equals(name, StringComparison.OrdinalIgnoreCase));
            if (match == null)
                throw new InvalidDataException("No SQLite table matches '" + name + "'. Check the template and its .rpt.json TableMappings. SQL Command and stored-procedure templates need an explicitly prepared matching result table.");
            return match;
        }

        public static Type FieldType(string crystalType)
        {
            switch (crystalType)
            {
                case "StringField": case "MemoField": return typeof(string);
                case "NumberField": return typeof(double);
                case "CurrencyField": return typeof(decimal);
                case "BooleanField": return typeof(bool);
                case "DateField": case "DateTimeField": case "TimeField": return typeof(DateTime);
                case "Int8sField": return typeof(sbyte);
                case "Int8uField": return typeof(byte);
                case "Int16sField": return typeof(short);
                case "Int16uField": return typeof(ushort);
                case "Int32sField": return typeof(int);
                case "Int32uField": return typeof(uint);
                case "BlobField": return typeof(byte[]);
                default: throw new NotSupportedException("Unsupported Crystal field type: " + crystalType);
            }
        }

        public static DataTable Shape(DataTable source, string alias, IDictionary<string, Type> fields,
            string scope = null, IDictionary<string, string> fieldMappings = null, ISet<string> optionalFields = null)
        {
            var output = new DataTable(alias) { Locale = CultureInfo.InvariantCulture };
            try
            {
                // A null entry means "no source column" - an optional field bound as all-blank below.
                var columns = new List<DataColumn>();
                foreach (var field in fields)
                {
                    string sourceName = ResolveField(alias, scope, field.Key, fieldMappings);
                    DataColumn column = source.Columns.Cast<DataColumn>().FirstOrDefault(x => x.ColumnName.Equals(sourceName, StringComparison.OrdinalIgnoreCase));
                    if (column == null && !IsOptional(alias, scope, field.Key, optionalFields))
                    {
                        string key = (string.IsNullOrEmpty(scope) ? "" : scope + "/") + alias + "/" + field.Key;
                        throw new InvalidDataException("Table '" + source.TableName + "' (Crystal alias '" + alias + "'" +
                            (string.IsNullOrEmpty(scope) ? "" : ", subreport '" + scope + "'") + ") is missing Crystal field '" + field.Key +
                            (sourceName == field.Key ? "" : "' (mapped to '" + sourceName) +
                            "'. Add '" + key + "' to the template's .rpt.json FieldMappings or OptionalFields.");
                    }
                    columns.Add(column);
                    output.Columns.Add(field.Key, field.Value);
                }
                // Some ETAP formulas reference fields absent from the saved field list.
                // Keep additional source fields available to those formulas and subreports.
                foreach (DataColumn column in source.Columns)
                {
                    if (columns.Contains(column)) continue;
                    columns.Add(column);
                    output.Columns.Add(column.ColumnName, column.DataType);
                }
                foreach (DataRow row in source.Rows)
                {
                    var values = new object[columns.Count];
                    for (int i = 0; i < columns.Count; i++)
                    {
                        if (columns[i] == null) { values[i] = DBNull.Value; continue; }
                        try { values[i] = ConvertValue(row[columns[i]], output.Columns[i].DataType); }
                        catch (Exception ex)
                        {
                            throw new InvalidDataException("Cannot convert " + source.TableName + "." + columns[i].ColumnName +
                                " at row " + (output.Rows.Count + 1) + " to " + output.Columns[i].DataType.Name + ". No PDF was created.", ex);
                        }
                    }
                    output.Rows.Add(values);
                }
                return output;
            }
            catch { output.Dispose(); throw; }
        }

        public static object ConvertValue(object value, Type target)
        {
            if (value == null || value == DBNull.Value) return DBNull.Value;
            if (target.IsInstanceOfType(value)) return value;
            if (target == typeof(bool))
            {
                string text = Convert.ToString(value, CultureInfo.InvariantCulture);
                if (text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase)) return true;
                if (text == "0" || text.Equals("false", StringComparison.OrdinalIgnoreCase)) return false;
                throw new FormatException("Boolean values must be 0, 1, true or false.");
            }
            if (target == typeof(DateTime)) return DateTime.Parse(Convert.ToString(value, CultureInfo.InvariantCulture), CultureInfo.InvariantCulture);
            if (target == typeof(byte[])) throw new FormatException("Expected a binary value.");
            // ChangeType rounds fractions when converting to integers; reject that data loss.
            if (target != typeof(string) && target != typeof(double) && target != typeof(decimal) && target != typeof(float))
            {
                decimal numeric = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                if (numeric != decimal.Truncate(numeric)) throw new FormatException("Fractional value cannot be bound to an integer field.");
            }
            return Convert.ChangeType(value, target, CultureInfo.InvariantCulture);
        }
    }
}
