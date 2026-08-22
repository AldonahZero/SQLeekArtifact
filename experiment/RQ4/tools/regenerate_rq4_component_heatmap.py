#!/usr/bin/env python3
"""Regenerate the RQ4 component heatmap from merged component rows."""

from __future__ import annotations

import sys


sys.path.insert(0, "/root/SQLeek/experiment/RQ4")
import update_wo_m1_results as updater  # noqa: E402


rows, _ = updater.read_rows(updater.DATA / "component_heatmap.csv")
updater.plot_component_heatmap(rows)
print("regenerated RQ4 component heatmap")
