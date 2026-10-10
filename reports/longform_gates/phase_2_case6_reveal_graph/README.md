# Case 6 RevealGraph Review

- Case: 6 (`CASE_a14593`) The Karmelo Anthony Murder Case
- Blueprint: `1`; status `valid`; beats: 50
- Persisted graph: `reveal_graphs.id=1`; status `validated`
- Nodes: 41; edges: 69; exposure rows: 41
- Blueprint counts: reveals 41, relies_on 97, viewer_knows 1,396

## Validation

`status=valid`; acyclic; all blueprint references resolved; all 41 nodes reachable from the dependency-root nodes. The graph has no `do_not_reveal` or `reveal_map` entries because the persisted blueprint has none.

## Edge construction rule

Only `relies_on` creates edges. Each relied-on node becomes a prerequisite of every node revealed in that same beat. `viewer_knows` is used only to verify that the dependency is listed as known and was first revealed earlier; it never creates an edge. Nodes with no explicit `relies_on` are documentary-start roots rather than being connected with invented prerequisites.

Root nodes (8): F009, F016, F019, F020, T007, T013, T016, T017

## Review files

- [`nodes.csv`](nodes.csv): all 41 reveal nodes and first-reveal beats.
- [`edges.csv`](edges.csv): all 69 authoritative prerequisite edges.
- [`exposures.csv`](exposures.csv): all 41 actual `reveals` exposure rows, grouped by beat.

## Nodes

| Key | Category | First beat | Label |
|---|---|---|---|
| F009 | fact | B02 | In released body-camera footage, Anthony told officers, “I'm not alleged, sir, I did it,” after an officer referred to him as the alleged suspect. |
| T007 | timeline | B02 | In released body-camera footage, Anthony told officers, “I'm not alleged, sir, I did it,” after an officer referred to him as the alleged suspect. |
| F008 | fact | B03 | After his arrest, Anthony repeatedly maintained that he had acted in self-defense. |
| F010 | fact | B03 | In released body-camera footage, Anthony said, “He put his hands on me. I told him not to, he put his hand on me!” |
| T006 | timeline | B03 | After his arrest, Anthony repeatedly maintained that he had acted in self-defense. |
| T008 | timeline | B03 | In released body-camera footage, Anthony said, “He put his hands on me. I told him not to, he put his hand on me!” |
| F001 | fact | B04 | On April 2, 2025, Austin Metcalf was fatally stabbed during a high-school track meet at a stadium in Frisco, Texas. |
| F007 | fact | B04 | Surveillance footage released after trial showed Anthony running away from the tent and appearing to stumble over the bottom rows of bleachers before continuing toward a parking lot. |
| T001 | timeline | B04 | On April 2, 2025, Austin Metcalf was fatally stabbed during a high-school track meet at a stadium in Frisco, Texas. |
| T005 | timeline | B04 | Surveillance footage released after trial showed Anthony running away from the tent and appearing to stumble over the bottom rows of bleachers before continuing toward a parking lot. |
| F002 | fact | B07 | Karmelo Anthony and Austin Metcalf were both 17 at the time of the stabbing and attended different Frisco high schools; available reporting states that they did not know each other before the incident. |
| T002 | timeline | B07 | Karmelo Anthony and Austin Metcalf were both 17 at the time of the stabbing and attended different Frisco high schools; available reporting states that they did not know each other before the incident. |
| C003 | contradiction | B10 | Whether Anthony and Metcalf were classmates |
| F003 | fact | B11 | The confrontation occurred after Anthony was sitting under the Memorial High School team tent during a rainy track meet and was told to leave. |
| T003 | timeline | B11 | The confrontation occurred after Anthony was sitting under the Memorial High School team tent during a rainy track meet and was told to leave. |
| C001 | contradiction | B12 | Who initiated the physical confrontation |
| F004 | fact | B13 | Witnesses testified that Metcalf pushed Anthony, after which Anthony pulled out a knife and stabbed Metcalf in the chest. |
| F005 | fact | B13 | The knife images released after trial showed a gray-handled utility knife with a 3.5-inch blade. |
| F006 | fact | B13 | A photograph released with the trial evidence showed Metcalf's two-inch chest wound. |
| T004 | timeline | B13 | Witnesses testified that Metcalf pushed Anthony, after which Anthony pulled out a knife and stabbed Metcalf in the chest. |
| F021 | fact | B15 | Judge John Roach Jr. publicly released more than six gigabytes of trial evidence, including surveillance footage, 911 calls, and photographs of the weapon and autopsy material. |
| F019 | fact | B18 | On July 28, 2025, Judge John Roach Jr. entered an order restricting extrajudicial statements by parties, attorneys, witnesses, law-enforcement personnel, and court personnel through trial. |
| F020 | fact | B18 | On May 18, 2026, the court's amended trial-access order provided for limited admission times, no more than nine media members, space-available seating, and courtroom doors closing at 9:00 a.m. |
| T016 | timeline | B18 | On July 28, 2025, Judge John Roach Jr. entered an order restricting extrajudicial statements by parties, attorneys, witnesses, law-enforcement personnel, and court personnel through trial. |
| T017 | timeline | B18 | On May 18, 2026, the court's amended trial-access order provided for limited admission times, no more than nine media members, space-available seating, and courtroom doors closing at 9:00 a.m. |
| F013 | fact | B25 | Anthony did not testify at trial. |
| T011 | timeline | B25 | Anthony did not testify at trial. |
| C002 | contradiction | B26 | Scope of the evidentiary agreement and reason Anthony did not testify |
| F014 | fact | B26 | Anthony's defense attorneys alleged that prosecutors violated an unrecorded agreement concerning character evidence, and that the dispute contributed to Anthony's decision not to testify; a county prosecutor denied the claim. |
| F018 | fact | B28 | On June 4, 2026, the auxiliary viewing room described in Anthony's new-trial motion was eliminated when testimony began, and the motion states that no overflow room was provided thereafter. |
| T015 | timeline | B28 | On June 4, 2026, the auxiliary viewing room described in Anthony's new-trial motion was eliminated when testimony began, and the motion states that no overflow room was provided thereafter. |
| F016 | fact | B29 | The jury selected for Anthony's trial had no Black jurors; prosecutors struck three remaining Black potential jurors after the defense raised a Batson challenge, and the court accepted the stated race-neutral reason that they were educators of school-aged children. |
| T013 | timeline | B29 | The jury selected for Anthony's trial had no Black jurors; prosecutors struck three remaining Black potential jurors after the defense raised a Batson challenge, and the court accepted the stated race-neutral reason that they were educators of school-aged children. |
| F011 | fact | B30 | On June 9, 2026, a Collin County jury found Anthony guilty of murder after approximately three hours of deliberation. |
| F012 | fact | B30 | On June 9, 2026, the jury rejected a lesser manslaughter charge and the defense's sudden-passion claim, and Anthony was sentenced to 35 years in prison. |
| F015 | fact | B30 | On August 22, 2026, Judge Michael Chitty denied Anthony's request for a new trial. |
| F017 | fact | B30 | The defense's motion for a new trial alleged that public access to the trial was materially restricted, including limited seating and the absence of continuing overflow, audio, or video access. |
| T009 | timeline | B30 | On June 9, 2026, a Collin County jury found Anthony guilty of murder after approximately three hours of deliberation. |
| T010 | timeline | B30 | On June 9, 2026, the jury rejected a lesser manslaughter charge and the defense's sudden-passion claim, and Anthony was sentenced to 35 years in prison. |
| T012 | timeline | B30 | On August 22, 2026, Judge Michael Chitty denied Anthony's request for a new trial. |
| T014 | timeline | B40 | The defense's motion for a new trial alleged that public access to the trial was materially restricted, including limited seating and the absence of continuing overflow, audio, or video access. |

## Per-beat exposure summary

| Beat | Exposed nodes | Withheld nodes |
|---|---|---|
| B01 | none | none |
| B02 | F009, T007 | none |
| B03 | F008, F010, T006, T008 | none |
| B04 | F001, F007, T001, T005 | none |
| B05 | none | none |
| B06 | none | none |
| B07 | F002, T002 | none |
| B08 | none | none |
| B09 | none | none |
| B10 | C003 | none |
| B11 | F003, T003 | none |
| B12 | C001 | none |
| B13 | F004, F005, F006, T004 | none |
| B14 | none | none |
| B15 | F021 | none |
| B16 | none | none |
| B17 | none | none |
| B18 | F019, F020, T016, T017 | none |
| B19 | none | none |
| B20 | none | none |
| B21 | none | none |
| B22 | none | none |
| B23 | none | none |
| B24 | none | none |
| B25 | F013, T011 | none |
| B26 | C002, F014 | none |
| B27 | none | none |
| B28 | F018, T015 | none |
| B29 | F016, T013 | none |
| B30 | F011, F012, F015, F017, T009, T010, T012 | none |
| B31 | none | none |
| B32 | none | none |
| B33 | none | none |
| B34 | none | none |
| B35 | none | none |
| B36 | none | none |
| B37 | none | none |
| B38 | none | none |
| B39 | none | none |
| B40 | T014 | none |
| B41 | none | none |
| B42 | none | none |
| B43 | none | none |
| B44 | none | none |
| B45 | none | none |
| B46 | none | none |
| B47 | none | none |
| B48 | none | none |
| B49 | none | none |
| B50 | none | none |
