#!/usr/bin/env python3
"""CLI: dump the raw contents of an ETAP .OTI file to stdout/a text report."""
import argparse
import json
import sys

from etap_reader.oti_parser import parse_oti


def main():
    ap = argparse.ArgumentParser(description="Dump contents of an ETAP .OTI (OLE compound file).")
    ap.add_argument("oti_path")
    ap.add_argument("--json", action="store_true", help="Output raw JSON instead of a formatted report")
    ap.add_argument("--out", help="Write report to this file")
    args = ap.parse_args()

    parsed = parse_oti(args.oti_path)

    if args.json:
        report = json.dumps(parsed, indent=2)
    else:
        lines = [f"File: {parsed['file']}", f"Size: {parsed['size']:,} bytes", ""]
        for s in parsed["streams"]:
            lines.append(f"### {s['path']}  ({s['size']} bytes)")
            if s["text"]:
                lines.append(f"  [text] {s['text']}")
            elif s["strings"]:
                lines.append("  [strings] " + ", ".join(s["strings"][:20]))
            else:
                lines.append("  [binary data]")
            lines.append("")
        report = "\n".join(lines)

    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n(also written to {args.out})", file=sys.stderr)


if __name__ == "__main__":
    main()
