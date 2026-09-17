"""Allowlisted presentation fields; never modify the uploaded study."""
import re

# id, label, source table, source column, maximum custom-text length
FIELDS = (
    ("sn", "SN", "Headr", "SN", 60),
    ("date", "Date", "Headr", "Date", 32),
    ("revision", "Revision", "ISCStudyCase", "Revision", 32),
    ("project", "Project", "Headr", "Project", 120),
    ("location", "Location", "Headr", "Loc", 120),
    ("contract", "Contract", "Headr", "Contr", 100),
    ("engineer", "Engineer", "Headr", "Eng", 100),
    ("filename", "Filename", "Headr", "FileN", 120),
    ("study_case", "Study case", "Headr", "STDCase", 120),
    ("configuration", "Configuration", "ISCStudyCase", "Config", 60),
    ("title_1", "Title line 1", "Headr", "1st", 180),
    ("title_2", "Title line 2", "Headr", "2nd", 180),
)
DEFINITIONS = {field[0]: field for field in FIELDS}


def validate(value):
    if not isinstance(value, dict) or any(key not in DEFINITIONS for key in value):
        raise ValueError("Header settings must contain only supported report fields.")
    result = {}
    for key, text in value.items():
        _, label, _, _, maximum = DEFINITIONS[key]
        if text is not None and (not isinstance(text, str) or len(text.encode('utf-16-le', errors='surrogatepass')) // 2 > maximum
                                 or re.search(r"[\x00-\x1f\x7f\ud800-\udfff]", text)):
            raise ValueError(f"{label} must be a single line of at most {maximum} characters, or hidden.")
        result[key] = text  # Absent = original, null = hide label and value, string = literal custom text.
    return result


def read_values(connection):
    result = {}
    tables = {row[0].lower(): row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for key, _, table, column, _ in FIELDS:
        actual = tables.get(table.lower())
        if not actual:
            result[key] = ""
            continue
        quoted = '"' + actual.replace('"', '""') + '"'
        columns = {row[1].lower(): row[1] for row in connection.execute('PRAGMA table_info(' + quoted + ')')}
        actual_column = columns.get(column.lower())
        if not actual_column:
            result[key] = ""
            continue
        selected = '"' + actual_column.replace('"', '""') + '"'
        row = connection.execute('SELECT ' + selected + ' FROM ' + quoted + ' LIMIT 1').fetchone()
        result[key] = str(row[0])[:2000] if row and row[0] is not None else ""
    return result


def public_fields():
    return [{"id": key, "label": label, "max_length": maximum} for key, label, _, _, maximum in FIELDS]
