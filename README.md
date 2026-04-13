# Canonical US Novels

This corpus uses *The MLA International Bibliography* (*MLAIB*) as a principle of selection to identify canonical US novels in the public domain.

## Principle of selection

*MLAIB* identifies authors and works that appear as subjects of academic monographs, chapters, and essays. Not all works or authors discussed in a given work of scholarship rise to the level of a subject. While many works have a single primary subject, works can have multiple subjects.

This project identifies all records that meet the following criteria:

- Genre discussed: "novel"
- National literature: "american"
- Novel is in English

*MLAIB* export limits do not permit all of these records to be generated at once, so they need to be exported in batches.

## Processing records

I count citations of every novel identified as a subject work. I filter those novels to only identify works in the US public domain based on their date of publication. Currently, this applies to written works published in or before 1930.

Then, I calculate the total number of records in which each subject work appeared, and use that value to calculate a z score within each literary period (identified by *MLAIB* annotators as centuries) to ensure wide historical coverage. Finally, I filter for novels with a z score >= 1 in each period.

## Getting texts

After the texts in the corpus have been identified, I then proceed to collect URLs for the texts that have already been digitized. Preferred sources include Project Gutenberg and scholarly cites that hand-key their texts. After these, we get OCR from scanned public domain copies.
