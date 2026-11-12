"""Fails when a pytest run skipped a test, or ran none: `python tools/check_no_skips.py report.xml`.

For the CI job that runs the browser tests. Those tests skip themselves where Playwright or Chrome is missing, which is
right on a developer's machine and wrong in that job: there a skip means the job tested nothing. pytest writes the report
with --junitxml=report.xml.
"""
import sys
import xml.etree.ElementTree as ET


def main() -> int:
    root = ET.parse(sys.argv[1]).getroot()
    cases = list(root.iter("testcase"))
    skipped = [c for c in cases if c.find("skipped") is not None]
    for case in skipped:
        reason = case.find("skipped").get("message", "")
        print("SKIPPED: %s::%s (%s)" % (case.get("classname", ""), case.get("name", ""), reason))
    if not cases:
        print("FAILED: no test ran")
        return 1
    if skipped:
        print("FAILED: %d of %d tests were skipped" % (len(skipped), len(cases)))
        return 1
    print("%d tests ran, none skipped" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
