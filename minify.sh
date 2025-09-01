#!/bin/bash

set -e

SRC=$1
case "$SRC" in
  *.js) 
      API=https://www.toptal.com/developers/javascript-minifier/api/raw 
      SUFIX=min.js
      echo "Minify JS"
  ;;
  *.css)
      API=https://www.toptal.com/developers/cssminifier/api/raw
      SUFIX=min.css
      echo "Minify CSS"
  ;;
  *.html)
      API=https://www.toptal.com/developers/html-minifier/api/raw
      SUFIX=min.html
      echo "Minify HTML"
  ;;
  *) echo "Unknown format."
    exit 1
  ;;
esac

DST="${SRC%.*}.${SUFIX}"

curl -X POST -s --data-urlencode "input@${SRC}" -o "${DST}" "$API"
