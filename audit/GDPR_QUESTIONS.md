# GDPR Questions — Data Reuse Assessment

These are the specific legal questions you must answer (with your data protection advisor) before reusing the ExecFlex/Ainm dataset for pay benchmarking or any other new purpose.

---

## 1. Lawful Basis for Original Collection

**Q1.1**: Under which Article 6 lawful basis were candidate profiles originally collected?
- Was it **consent** (6(1)(a)) — did candidates explicitly consent to their data being stored?
- Was it **contract** (6(1)(b)) — was there a service agreement (recruitment matching) that required the data?
- Was it **legitimate interest** (6(1)(f)) — was a legitimate interest assessment (LIA) documented?

**Why it matters**: The lawful basis for collection constrains what you can do with the data later. If the basis was consent, reuse for a different purpose requires fresh consent. If it was legitimate interest, you need to assess whether benchmarking is compatible with the original purpose.

**Q1.2**: Was the lawful basis documented at the time of collection? Is there a privacy notice, terms of service, or data processing statement that candidates agreed to?

**Q1.3**: For data obtained from third parties (People Data Labs, Apollo.io, LinkedIn imports, bulk CSV uploads) — what was the lawful basis for *those* parties to share the data with you? Do you have data processing agreements (DPAs) with PDL and Apollo?

---

## 2. Purpose Limitation (Article 5(1)(b))

**Q2.1**: What was the stated purpose when data was collected?
- If the purpose was "executive recruitment matching" or "connecting candidates with hiring companies," does "pay benchmarking for a separate HR platform" fall within a reasonable expectation of that purpose?

**Q2.2**: Was a broader purpose stated — e.g., "recruitment services and market analytics" — that might encompass benchmarking?

**Q2.3**: If the original purpose does NOT cover benchmarking, can you rely on the Article 6(4) compatibility test? The factors are:
- (a) Link between original and new purpose
- (b) Context of collection and relationship with data subjects
- (c) Nature of the data (sensitive or not)
- (d) Consequences for data subjects
- (e) Existence of appropriate safeguards (encryption, anonymisation)

**Q2.4**: If the data is *truly anonymised* (irreversible, no reasonable means of re-identification), GDPR no longer applies. But: is your proposed anonymisation actually sufficient given the small dataset size and the identifiability of senior executives in small markets?

---

## 3. Anonymisation vs. Pseudonymisation

**Q3.1**: Is your proposed extraction (removing names, LinkedIn, rounding salaries, banding experience) genuinely **anonymisation** (Article 26 recital — no reasonable means of re-identification), or merely **pseudonymisation** (still personal data, still subject to GDPR)?

**Q3.2**: For senior executives in Ireland (small market, ~200 C-suite roles in major companies), can a record with {CFO, 20+ years, Financial Services, Dublin, €250k-€300k} be re-identified by someone with industry knowledge? If yes, the data is not anonymous despite removing the name.

**Q3.3**: What k-anonymity threshold is legally defensible in your jurisdiction? The Article 29 Working Party (now EDPB) Opinion 05/2014 on anonymisation techniques suggests that k=5 is a minimum, but for small populations of senior executives, even k=5 may be insufficient.

**Q3.4**: Would you need a Data Protection Impact Assessment (DPIA, Article 35) before performing the anonymisation process itself? (The act of processing personal data *in order to* anonymise it is itself processing.)

---

## 4. Retention and Storage

**Q4.1**: What is your retention policy for the original dataset? Has a retention period been communicated to data subjects?

**Q4.2**: If candidates were told their data would be kept "for the duration of their engagement with the platform" or "for 2 years," and the platform has been dormant, are you currently in breach of your stated retention period?

**Q4.3**: Can you continue to store the original data while you decide what to do? What is the lawful basis for continued storage of a dormant dataset?

**Q4.4**: If you extract an anonymised dataset, must you delete the originals? Or can you retain them under a different lawful basis?

---

## 5. Data Subject Rights

**Q5.1**: Have any data subjects exercised their rights (access, erasure, portability, objection) against this dataset? If so, have those requests been fulfilled?

**Q5.2**: If you extract data for benchmarking, do you need to notify data subjects (Article 13/14 transparency obligation)?
- If the extract is truly anonymous: no notification required (GDPR doesn't apply to anonymous data)
- If pseudonymous: notification required unless an exemption applies

**Q5.3**: If a data subject requests erasure (Article 17) after you have already created the benchmarking extract, can you comply? If the extract is anonymous, their data is no longer identifiable within it — but this only works if the anonymisation is genuinely irreversible.

---

## 6. Cross-Entity Reuse

**Q6.1**: Is the benchmarking platform a separate legal entity from the entity that collected the original data? If yes:
- Is there a data sharing agreement between the entities?
- Are both entities named in the original privacy notice?
- Does one entity act as processor for the other, or are they joint controllers?

**Q6.2**: If you are the sole controller of both platforms, is there an internal purpose limitation policy that governs reuse across products?

---

## 7. Third-Party Data Complications

**Q7.1**: For profiles enriched via People Data Labs (PDL) — what does your PDL agreement say about downstream use of enriched data? Many data broker agreements restrict use to the original stated purpose.

**Q7.2**: For profiles imported from LinkedIn (OAuth) — LinkedIn's API terms typically restrict use of imported data. Can you reuse LinkedIn-sourced profile data for benchmarking?

**Q7.3**: For data collected via bulk CSV uploads (admin feature) — who provided those CSVs? What consent/authority did they have to share that data? Is there a documented chain of custody?

---

## 8. The Three Gating Questions

Before any technical extraction work proceeds, these three questions must be answered:

### Gate 1: Is the anonymisation legally sufficient?
Given the small population of senior Irish executives, can the proposed extract (banded experience, rounded salary, region-level location, role taxonomy) defeat motivated re-identification by someone with industry knowledge?

**Test**: Take 5 sample records from the proposed extract schema. Could a competitor, journalist, or industry insider identify the individual with reasonable effort?

### Gate 2: Is there a lawful basis for the processing required to create the extract?
The act of reading the database, running the anonymisation pipeline, and producing the output file is itself data processing. Under which Article 6 basis is this processing lawful?

### Gate 3: Does the original collection purpose (or a compatible purpose) cover benchmarking?
If consent was the basis: you need fresh consent (impractical for a dormant platform).
If legitimate interest: you need a new LIA for the benchmarking purpose.
If contract: the contract didn't cover benchmarking.

---

## Recommended Next Steps

1. **Locate the original privacy notice** — what did candidates see when they signed up or had their data imported?
2. **Check `consent_given` and `consent_given_at` fields** — what percentage of profiles have explicit consent recorded?
3. **Review PDL and LinkedIn agreements** — what are the downstream use restrictions?
4. **Engage a data protection advisor** with this document and the DATA_ASSET_MAP.md — get a formal opinion on Gates 1-3 before any extraction.
5. **Consider a DPIA** — even if you conclude anonymisation is sufficient, document the reasoning in a DPIA as a defensible record.
