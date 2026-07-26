# TaxAnn — A Tax-Form Annotation Specification (v1.0)

## 1. What this is

TaxAnn is a data format for describing **how to print values onto the boxes of a
U.S. tax form**. An *annotation set* is authored once per form + tax year and
reused for every taxpayer. Given an annotation set and one taxpayer's data, any
conforming renderer can produce a filled PDF.

The formal, machine-checkable definition lives in
[`spec/annotation.schema.json`](spec/annotation.schema.json) (JSON Schema
draft-07). This document explains the model and the reasoning behind it.

## 2. The one idea everything rests on: template vs. instance

Two things change at completely different rates:

- **The form** is fixed. The IRS publishes the 2025 Form 1040 once; it is
  identical for the ~150M people who file it.
- **The data** is different for every filer.

So TaxAnn describes the form **once**, holding **zero taxpayer data**. It is a
stencil: it says *where* the name goes, not *whose* name. The same annotation
set renders a million different returns. This is proven in the reference
implementation by rendering two different taxpayers through the identical
annotation file.

A direct corollary: **the annotation layer never computes anything.** Line 9
("total income") and line 1a ("wages") are described identically — both simply
*reference* a finished value produced upstream by the tax engine and print it.
"Reference, don't compute" is the founding rule.

## 3. The atom: every box answers three questions

A blind program filling a box needs exactly three things, and TaxAnn's field
object is built around them:

| Question | Answered by | Example |
|----------|-------------|---------|
| **Where** is the box? | `bounds` (+ `coordinateSystem`) | `{x, y, width, height}` in points |
| **Which** value fills it? | `value` (a reference into the data) | `$.income.wagesW2Box1` |
| **How** should it look? | `style` + `format` + `padding` + `overflow` | right-aligned, whole-dollar, `(1,234)` |

Everything else in the spec is machinery for boxes that don't fit the simple
one-value-one-rectangle mould.

## 4. Positioning

```json
"coordinateSystem": { "origin": "top-left", "unit": "pt" }
```

- **Unit is PDF points** (72 per inch). Points are resolution-independent, so a
  renderer at any DPI places text identically.
- **Origin is `top-left`, y grows downward** — the way a human reads a page.
  PDF's native origin is *bottom-left* with y growing upward; conflating the two
  is the single most common bug in this domain (text lands ~700pt too high).
  TaxAnn makes the convention an explicit, declared field, and the renderer
  flips y using each page's declared `height`. Every `pages[]` entry therefore
  carries `width` and `height`.
- A box is a **bounding box**, not a point: `x, y, width, height`. Alignment
  happens *inside* the box (see formatting), which is why currency can sit flush
  against the right edge exactly as the form expects.

## 5. Referencing a value

The `value` binding resolves a value out of the (arbitrarily nested) taxpayer
data. One of three forms:

```json
"value": { "ref": "$.income.w2[0].box1" }                       // single node
"value": { "template": "{$.address.city}, {$.address.state}" }  // compose several
"value": { "const": "X" }                                       // literal
```

**Path syntax.** `ref` uses a **restricted JSONPath**: `$` root, `.key`
navigation, `[index]` for arrays. It **must resolve to exactly one node**;
multi-match expressions (wildcards, filters) are a validation error in v1.0.
This is a deliberate choice — a binding that could silently match two nodes is a
correctness hazard on a legal document. (See DECISIONS.md for JSONPath vs. JSON
Pointer.)

**Optional modifiers on any binding:**

- `default` — value to use when the path is missing/null (e.g. `0` for an empty
  income line). Without it, a missing value renders the box **blank** rather than
  crashing — real returns have many empty lines.
- `transform` — a **single transform from a fixed, closed enum**:
  `digitsOnly`, `roundWholeDollar`, `upper`, `lower`, `trim`. It is *not* an
  expression language. Arbitrary evaluation would make annotation sets unsafe to
  share and impossible to validate statically; the closed enum is the feature.

## 6. Formatting

**Named styles**, defined once and referenced by fields, keep the set clean
(no repeated font declarations):

```json
"styles": {
  "currency": { "font": "Helvetica", "size": 10, "align": "right", "valign": "middle" }
}
```

A style carries `font`, `size`, `color`, horizontal `align`, and vertical
`valign` (alignment *within* the bounding box).

**Number formatting** (`format` on numeric/currency fields):

- `decimals` — IRS whole-dollar lines use `0`.
- `thousands` — insert `1,234` separators.
- `negative` — `"parens"` renders `(1,234)`, the accounting/IRS convention, vs.
  `"minus"` for `-1,234`.

**`padding`** insets text from the box edges so it doesn't touch printed rules.

**`overflow`** decides what happens when text doesn't fit: `shrink` (reduce font
size, default), `truncate`, `clip`, or `error`. Real data will exceed real
boxes; naming the policy per field is required scope, not a nicety.

## 7. Field types

| Type | Purpose |
|------|---------|
| `text`, `multiline` | Plain strings (names, addresses). |
| `number`, `currency` | Formatted numerics. |
| `date` | Dates (formatting via style/transform). |
| `checkbox` | An *independent* box: check it or not. |
| `choiceGroup` | **Mutually exclusive** options; the value selects exactly one. |
| `comb` | **One value distributed across N cells** (SSN, EIN). |

Two of these carry the design's weight:

### `comb` — the split-cell field

An SSN is not one box; visually it is nine cells with group separators
(`XXX-XX-XXXX`). `comb` takes **one** value and distributes it across `cells`
evenly, with optional `groupGaps` to reproduce the separators:

```json
{ "type": "comb", "value": { "ref": "$.taxpayer.ssn", "transform": "digitsOnly" },
  "bounds": {...}, "comb": { "cells": 9, "groupGaps": { "3": 7, "5": 7 } } }
```

(Note: on the 2025 Form 1040 the PDF implements SSN as a single comb-flagged
field, so one set of coordinates covers all nine cells — exactly what this type
models.)

### `choiceGroup` — mutually exclusive selection

Filing status is five options where exactly one is marked. The **value picks the
box**, not the other way around:

```json
{ "type": "choiceGroup", "value": { "ref": "$.filingStatus" },
  "options": [ { "when": "single", "bounds": {...} },
               { "when": "marriedFilingJointly", "bounds": {...} }, ... ] }
```

This deliberately lives in the annotation layer because the form does **not**
reliably enforce the "check only one" rule — that constraint is printed
instruction to a human, so the renderer must own it.

Together these show that "which box gets the value" has three shapes:
one-box↔one-value (`text`/`currency`), one-value→one-of-many (`choiceGroup`),
and one-value→many (`comb`). A design that imagined only the first would break on
half of page 1.

## 8. Provenance & migration: the `source` anchor

Every field may carry a `source.pdfFieldId` — the stable AcroForm field id the
box was authored from (e.g. `topmostSubform[0].Page1[0].f1_11[0]`). This is not
used to *place* text (coordinates do that); it exists so that:

1. When the IRS reshuffles next year's layout, annotations can be **re-anchored**
   by id and only genuinely new/removed boxes need human attention.
2. A renderer *may* offer an alternative **AcroForm-native** path (fill the named
   field instead of overlaying) where the source PDF is fillable — more robust
   than coordinates when available.

> Caveat learned from the real form: the sequential ids (`f1_11`, `f1_12`, …)
> **shift** when the IRS inserts fields between years (the 2025 form added a
> top block, renumbering everything). So the id is a useful anchor but not a
> stable key across years; migration still needs a verification pass.

## 9. How a renderer consumes this (the contract)

1. Load annotation set, taxpayer data, source PDF.
2. For each field: resolve `value` → apply `transform` → apply `default` if
   missing → format per type/`format`.
3. Convert `bounds` from top-left to PDF bottom-left using page `height`.
4. Draw aligned inside the box per `style`/`padding`, honoring `overflow`.
   - `comb`: distribute characters across cells with `groupGaps`.
   - `choiceGroup`: resolve value, stamp `mark` in the matching option's box.
5. Merge the overlay onto the source PDF.

The reference implementation in [`renderer/render.py`](renderer/render.py) is
~250 lines and does exactly this. It is the proof that the spec is complete
enough for a stranger to build against.

## 10. Verification method

Correctness is confirmed by round-trip, not by inspection: feed known data
through the renderer and look at the output. `123-45-6789` landing across the
SSN cells and `92,451` sitting in line 1a *is* the proof the mapping is right.
The repo includes two sample taxpayers rendered through the identical annotation
set (identity, filing status, currency, overflow, negative-as-parens).

## 11. Future enhancements

Deliberately out of scope for v1.0, in rough priority order:

1. **Repeating groups** — iterate `$.dependents[*]` down N rows at a fixed
   row-height offset (the schema already reserves room for this).
2. **Conditional fields** — render a box only when a condition holds (e.g. the
   1040's "if you checked MFS, enter spouse name" line).
3. **Multi-page overflow / continuation pages** — >4 dependents spilling to a
   schedule.
4. **AcroForm-native binding** as a first-class render mode with coordinate
   fallback.
5. **Normalized (0–1) coordinates** for true resolution/page-size independence.
6. **Richer references** — opt-in filtered/multi-match JSONPath
   (`$.w2[?(@.employer=='X')]`) once single-node ergonomics are locked.
7. **Visual authoring UI** with OCR-assisted box detection, and a **year-over-year
   diff tool** that re-anchors annotations via `source.pdfFieldId`.
8. **A canonical taxpayer data schema** (or an adapter layer) so `ref` paths
   don't shatter when the tax engine's output shape changes.
