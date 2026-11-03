# What changed since port_agents_2026-10-27 (switch catalogue moved to Excel)

Package: `port_agents_2026-10-28.zip`. Same entry point, same calls, same result fields - the
integration code does not change. What changes is the catalogue file inside the agent folders.
(Still on the 25 Oct package? Read `CHANGES_SINCE_2026-10-25.md` first, then this.)
A later build of 28 Oct adds the device specification to every result: see `CHANGES_SINCE_2026-10-28.md`.

## 1. How to update

Replace the three folders `switch_agent/`, `router_agent/`, `appliance_agent/` with the ones in this
zip (whole folders, not single files). `run_agent.py` is unchanged and `requirements.txt` lists the
same packages (`openpyxl`, which reads the Excel, was already required; only its comment changed).

## 2. What changed

| | 27 Oct package | 28 Oct package |
|---|---|---|
| Switch catalogue (the vendor / model names the agents match the text on a photo against) | `models_by_vendor.json` in each agent folder | `switch_catalog/Switch_Vendors_and_Models.xlsx` in each agent folder; the JSON is gone |
| Switch agent | read the JSON for vendors and models | reads the Excel: sheet `Switches`, rows with `Used by agent` = Yes; sheet `Vendors`, column `OCR spellings (used by agent)` |
| Router / appliance agents | read the JSON for switch vendor spellings | read the same spellings from the Excel |
| Switch vendors known | 110 | 117 (Adtran, Avaya, Ciena, Nokia, Proscend, TE Connectivity, WAGO added) |
| Switch models matched | 6,558 | 6,955 (every earlier model, same spelling and order, plus 397 from the vendor workbooks: Arista 7050 / 7280 / 7500, Extreme 5320 / 5420 / 5520, Huawei CloudEngine, ZTE, Nokia 7220 ...) |

Effect on results: photos of models the agent knew before give the same result (checked on 11 test
photos of all three agents, run on this package). Photos of the newly added models can now have their
model matched when it is read on the panel.

## 3. Changing the catalogue

The Excel is the only catalogue - there is no build step. Edit it and the next run uses it:
- add a model: a row with `Vendor`, `Model` and `Used by agent` = Yes (other columns are information);
- stop a model being matched: set `Used by agent` to No;
- a new vendor: a row in sheet `Vendors` with its `OCR spellings (used by agent)` (the words printed
  on the panel, upper case, comma separated).

Keep sheet and column names as they are. The three agent folders each carry a copy - change all three
(or keep one copy and replace the others with it). Sheet `Notes` explains every column.

## 4. Also fixed

The switch agent's spec scrapers (`switch_agent/RT-EOS/switch_specs`) cached vendor pages inside the
agent folder (`RT-EOS/switch_specs/data_cache/`) for some vendors (seen with Arista). They now use
the run's own cache under `out/_cache`, removed after the run, so the agent folders are only read, as
the README states. Delete `switch_agent/RT-EOS/switch_specs/data_cache/` if an earlier package left one.

## 5. Not changed

Templates, port detection, numbering, cable status, result fields and error handling are as in the
27 Oct package. The team workbook of 23 Oct (117 vendors, 8,854 models) is still not merged (open item).
