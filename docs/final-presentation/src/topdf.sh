#!/bin/bash
# usage: topdf.sh in.pptx out.pdf  (через PowerPoint, LibreOffice нет)
IN=$(cd "$(dirname "$1")"; pwd)/$(basename "$1"); OUT=$(cd "$(dirname "$2")"; pwd)/$(basename "$2")
osascript <<AS
tell application "Microsoft PowerPoint"
  open POSIX file "$IN"
  set p to active presentation
  save p in POSIX file "$OUT" as save as PDF
  close p saving no
end tell
AS
