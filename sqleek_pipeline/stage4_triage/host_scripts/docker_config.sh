#!/bin/bash

slowDiskPath="$1"
fastDiskPath="$2"

umount /var/lib/docker
