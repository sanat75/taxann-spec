# Design Decisions

Each entry: the fork, the choice, and why. This is the "describe the decisions
you made" record — and the running notes behind the spec.

### 1. Annotation is a binding, not a calculator
Chose to make the annotation layer strictly a *presentation binding* — position
+ reference + format — with zero computation. Line 9 (a computed total) and line
1a (a raw input) are described identically; both just reference a finished value.
*Why:* separates concerns cleanly. The tax engine owns math; the annotation owns
placement. It also means one annotation set is correct regardless of how values
were derived.

### 2. Template/instance split — annotation holds no taxpayer data
The annotation set is authored once per form-year and reused for every filer.
*Why:* the form changes ~once a year; data changes per person. Mixing them would
force re-authoring per return. Proven by rendering two taxpayers through one file.

### 3. Coordinates: top-left origin, PDF points
Authoring origin is top-left (y down); renderer flips to PDF's bottom-left.
*Why:* humans read top-left; PDF is bottom-left. This mismatch is the #1 bug in
form stamping. Making the convention an explicit declared field (not an implicit
assumption) removes the ambiguity. Points chosen over pixels for
resolution-independence; normalized 0–1 deferred to v2.

### 4. Referencing: restricted single-node JSONPath (not JSON Pointer)
Chose JSONPath authoring syntax (`$.a.b[0].c`), constrained to resolve to exactly
one node.
*Why:* JSONPath is familiar and composes naturally with templated text and the
array-heavy shape of tax data. JSON Pointer (`/a/b/0/c`) is the RFC-standard
single-node purist option and is a fine alternative — both are pure resolvers, so
supporting Pointer later is trivial. The single-node *constraint* is the key
call: a binding that could silently match two nodes is a correctness hazard on a
legal document, so multi-match is a validation error in v1.0.

### 5. Transforms are a closed enum, never an expression language
`digitsOnly | roundWholeDollar | upper | lower | trim`.
*Why:* an eval/expression field would make shared annotation sets a security
surface and impossible to validate statically. A fixed enum keeps the format
declarative, safe, and machine-checkable. New transforms are added to the spec,
not written by annotators.

### 6. Named styles
Fonts/sizes/alignment defined once in `styles`, referenced by fields.
*Why:* cleanliness — avoids hundreds of repeated font declarations and gives a
single place to adjust the look of all currency cells at once.

### 7. Field taxonomy includes `comb` and `choiceGroup` from day one
Rather than only `text`/`currency`, the model has `comb` (one value across N
cells, e.g. SSN) and `choiceGroup` (mutually exclusive, e.g. filing status).
*Why:* inspecting a real 1040 shows "which box gets the value" has three shapes —
one↔one, one→one-of-many, one→many. A taxonomy that only imagined the first would
break on half of page 1.

### 8. Mutual exclusion lives in the annotation, not the form
Empirically confirmed the 1040's filing-status "check only one" is not enforced
by the PDF structure in the general case — it's printed instruction to a human.
So `choiceGroup` owns the exclusion: the value picks exactly one box.

### 9. `source.pdfFieldId` as a provenance/migration anchor
Each field records the AcroForm id it was authored from, but placement still uses
coordinates.
*Why:* enables year-over-year re-anchoring and an optional AcroForm-native render
path. Honest caveat found on the real form: sequential ids shift when the IRS
inserts fields between years, so the anchor aids migration but isn't a stable key
— a verification pass is still required.

### 10. Coordinates extracted from the fillable PDF, not eyeballed
The official 1040 is a fillable AcroForm (199 fields); coordinates were read from
its field rectangles, not measured by hand.
*Why:* trust the form over the human wherever the form will talk to you. Removes
an entire class of measurement error. Hand-measurement is the documented fallback
only for non-fillable forms.

### 11. Ship a reference renderer, not just a schema
Built a ~250-line renderer that stamps sample data onto the real PDF.
*Why:* the assessment's real test is "a stranger's code can execute this spec."
Building the renderer makes *us* that stranger — every ambiguity (overflow,
missing data, comb spacing, y-flip) surfaced and got fixed. A spec nobody ran is
a guess; a spec that drove a real render is proven.

### 12. Scope chosen as depth over breadth
Annotated a representative *slice* (identity, filing status, income) covering
every field type, rather than all ~130 boxes.
*Why:* the slice demonstrates the full expressiveness of the format; annotating
the remaining boxes is mechanical repetition with no new design signal. Effort
was spent on the renderer and verification instead — two of the three graded
dimensions.
