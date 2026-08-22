#!/bin/bash

plotFilePath="$1"

cat "$plotFilePath" | tr -d ',' | cut -d ' ' -f 13

