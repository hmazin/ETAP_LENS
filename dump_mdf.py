#!/usr/bin/env python3
"""CLI: dump an ETAP project database or study result file into a portable .sqlite file.

Accepts .oti / .mdf / .bak (project model) or .sa1s / .sa2s / .lf1s / .ul1s
(study results) - anything etap_reader.locate.locate() recognizes.
"""
import argparse
import os
import sys

from etap_reader import locate, mdf_dump, study_result


def main():
    ap = argparse.ArgumentParser(description="Dump an ETAP project database or study result into a portable SQLite file.")
    ap.add_argument("input_path", help="Path to .oti, .mdf, .bak, .sa1s, .sa2s, .lf1s, or .ul1s")
    ap.add_argument("--out", help="Output .sqlite path (default: <db name>.sqlite in the current folder)")
    ap.add_argument("--instance", default=mdf_dump.DEFAULT_INSTANCE, help="LocalDB instance name (ignored for study result files)")
    args = ap.parse_args()

    located = locate.locate(args.input_path)
    if located.note:
        print(located.note)
    out_path = args.out or (located.db_name + ".sqlite")

    def progress(stage, current, total):
        if stage == "dumping" and total:
            print(f"  dumping tables: {current}/{total}")
        else:
            print(f"  {stage}...")

    if located.kind == "study":
        stats = study_result.import_study_to_sqlite(located.db_path, out_path, progress_cb=progress)
    else:
        stats = mdf_dump.dump_to_sqlite(located.kind, located.db_path, out_path,
                                         instance=args.instance, progress_cb=progress)
    print(f"\nDone. {stats['tables']} tables, {stats['rows_total']:,} rows -> {os.path.abspath(out_path)}")


if __name__ == "__main__":
    main()
